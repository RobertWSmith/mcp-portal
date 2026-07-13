"""Govern MCP Portal runtime behavior and SDK compatibility."""

from __future__ import annotations

from dataclasses import dataclass
import json
from collections.abc import Mapping
import time
from typing import Any, Literal

import anyio

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import Tool
from mcp.types import ToolAnnotations, ToolExecution

from mcp_portal.auth import (
    EnterpriseAuthProvider,
    EnterpriseAuthSchemeMiddleware,
)
from mcp_portal.approvals import ApprovalVerifier, RejectingApprovalVerifier
from mcp_portal.audit import AuditDetails, AuditSink, LoggingAuditSink, audit_event
from mcp_portal.clients import ClientFactories
from mcp_portal.config import Settings
from mcp_portal.errors import PermissionPortalError, TimeoutPortalError, UpstreamPortalError
from mcp_portal.namespaces import (
    Namespace,
    NamespaceProvider,
)
from mcp_portal.observability import create_telemetry_recorder
from mcp_portal.policy import PolicyDecision, PolicyEngine, ScopePolicyEngine
from mcp_portal.resilience import AdmissionController, QuotaBackend
from mcp_portal.security import (
    InvocationContext,
    identity_from_access_token,
    invocation_scope,
    new_invocation,
)
from mcp_portal.tasks import TaskStore
from mcp_portal.telemetry import CostSink, TelemetryRecorder

Transport = Literal["stdio", "http", "sse", "streamable-http"]
HTTP_TRANSPORTS: set[Transport] = {"http", "sse", "streamable-http"}


@dataclass(frozen=True)
class PortalDependencies:
    """Optional service adapters injected into a portal server.

    Attributes:
        clients: Shared external client registry.
        policy_engine: Authorization policy adapter.
        audit_sink: Append-only audit destination.
        quota_backend: Shared quota backend.
        approval_verifier: Out-of-band approval verifier.
        task_store: Authorization-bound task store.
        telemetry: Metrics and cost-accounting recorder.
        cost_sink: Detailed cost-accounting destination.
    """

    clients: ClientFactories | None = None
    policy_engine: PolicyEngine | None = None
    audit_sink: AuditSink | None = None
    quota_backend: QuotaBackend | None = None
    approval_verifier: ApprovalVerifier | None = None
    task_store: TaskStore | None = None
    telemetry: TelemetryRecorder | None = None
    cost_sink: CostSink | None = None


@dataclass(frozen=True)
class _ToolCall:
    """Trusted state shared by tool-invocation pipeline stages.

    Attributes:
        name: Mounted MCP tool name.
        arguments: Validated invocation arguments.
        tool: Registered FastMCP tool.
        invocation: Trusted request and identity context.
        started: Monotonic invocation start time.
    """

    name: str
    arguments: dict[str, Any]
    tool: Tool
    invocation: InvocationContext
    started: float


@dataclass(frozen=True)
class _TransportOverrides:
    """Optional SDK transport settings supplied by compatibility callers.

    Attributes:
        host: Optional HTTP bind host.
        port: Optional HTTP bind port.
        path: Optional HTTP endpoint path.
        log_level: Optional SDK log level.
        json_response: Optional JSON response mode.
        stateless: Optional stateless HTTP mode.
    """

    host: str | None = None
    port: int | None = None
    path: str | None = None
    log_level: str | None = None
    json_response: bool | None = None
    stateless: bool | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "_TransportOverrides":
        """Build supported overrides while ignoring compatibility-only keywords.

        Args:
            values: Arbitrary legacy transport keyword mapping.

        Returns:
            Normalized supported transport overrides.
        """
        return cls(
            host=values.get("host"),
            port=values.get("port"),
            path=values.get("path"),
            log_level=values.get("log_level"),
            json_response=values.get("json_response"),
            stateless=values.get("stateless"),
        )


class PortalFastMCP(FastMCP):
    """Small compatibility layer around the SDK FastMCP server."""

    def __init__(
        self,
        *args: Any,
        portal_settings: Settings | None = None,
        dependencies: PortalDependencies | None = None,
        enforce_request_controls: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the compatibility server with enterprise request controls.

        Args:
            args: Positional SDK server arguments.
            portal_settings: Portal configuration used by enforcement layers.
            dependencies: Optional injected service adapters.
            enforce_request_controls: Whether rate and size controls are active.
            kwargs: Keyword SDK server arguments.
        """
        selected_settings = portal_settings or Settings.from_env()
        selected_dependencies = dependencies or PortalDependencies()
        self.portal_settings = selected_settings
        self.policy_engine = selected_dependencies.policy_engine or ScopePolicyEngine(
            selected_settings
        )
        self.audit_sink = selected_dependencies.audit_sink or LoggingAuditSink()
        self.enforce_request_controls = enforce_request_controls
        self.approval_verifier = (
            selected_dependencies.approval_verifier or RejectingApprovalVerifier()
        )
        self.telemetry = selected_dependencies.telemetry or create_telemetry_recorder(
            selected_settings, cost_sink=selected_dependencies.cost_sink
        )
        self._component_namespaces: dict[tuple[str, str], Namespace] = {}
        self.admission = AdmissionController(
            selected_settings.enterprise.max_concurrent_requests,
            selected_dependencies.quota_backend,
        )
        super().__init__(*args, **kwargs)

    async def list_tools(self) -> list[Any]:
        """Return only tools authorized for the current verified caller.

        Returns:
            MCP tool definitions whose namespace and tool policy is satisfied.
        """
        visible: list[Any] = []
        for exposed_tool in await super().list_tools():
            tool = self._tool_manager.get_tool(exposed_tool.name)
            if tool is None:
                continue
            exposed_tool = exposed_tool.model_copy(
                update={
                    "execution": ToolExecution(
                        taskSupport=(tool.meta or {}).get("task_support", "forbidden")
                    )
                }
            )
            invocation = new_invocation(
                exposed_tool.name,
                self.portal_settings.enterprise.tenant_claim,
                self.portal_settings.enterprise.tool_timeout(exposed_tool.name, tool.meta),
            )
            catalog_authorizer = getattr(self.policy_engine, "authorize_catalog", None)
            if catalog_authorizer is None:
                decision = await self.policy_engine.authorize(invocation, tool, {})
            else:
                decision = await catalog_authorizer(invocation, tool)
            if decision.allowed:
                visible.append(exposed_tool)
        return visible

    async def list_resources(self) -> list[Any]:
        """Hide resources belonging to namespaces unavailable to the caller.

        Returns:
            MCP resource definitions from visible namespaces.
        """
        return [
            resource
            for resource in await super().list_resources()
            if self._namespace_visible(
                self._component_namespaces.get(("resource", str(resource.uri)))
            )
        ]

    async def list_resource_templates(self) -> list[Any]:
        """Hide resource templates belonging to unavailable namespaces.

        Returns:
            MCP resource-template definitions from visible namespaces.
        """
        return [
            template
            for template in await super().list_resource_templates()
            if self._namespace_visible(
                self._component_namespaces.get(("template", template.uriTemplate))
            )
        ]

    async def list_prompts(self) -> list[Any]:
        """Hide prompts belonging to namespaces unavailable to the caller.

        Returns:
            MCP prompt definitions from visible namespaces.
        """
        return [
            prompt
            for prompt in await super().list_prompts()
            if self._namespace_visible(self._component_namespaces.get(("prompt", prompt.name)))
        ]

    async def read_resource(self, uri: Any) -> Any:
        """Fail closed when a caller requests a hidden namespace resource.

        Args:
            uri: Concrete resource URI requested by the client.

        Returns:
            Resource contents from an authorized namespace.

        Raises:
            ValueError: If the resource belongs to a hidden namespace.
        """
        namespace = self._resource_namespace(str(uri))
        if namespace is not None and not self._namespace_visible(namespace):
            raise ValueError(f"Unknown resource: {uri}")
        return await super().read_resource(uri)

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Fail closed when a caller requests a hidden namespace prompt.

        Args:
            name: Mounted prompt name requested by the client.
            arguments: Optional prompt template arguments.

        Returns:
            Rendered prompt from an authorized namespace.

        Raises:
            ValueError: If the prompt belongs to a hidden namespace.
        """
        namespace = self._component_namespaces.get(("prompt", name))
        if namespace is not None and not self._namespace_visible(namespace):
            raise ValueError(f"Unknown prompt: {name}")
        return await super().get_prompt(name, arguments)

    def _namespace_visible(self, namespace: Namespace | None) -> bool:
        """Evaluate manifest and deployment scopes for a namespace catalog entry.

        Args:
            namespace: Governed namespace associated with a catalog component.

        Returns:
            True when the verified caller may discover the namespace.
        """
        if namespace is None or not self.portal_settings.auth.enabled:
            return True
        identity = identity_from_access_token(self.portal_settings.enterprise.tenant_claim)
        if identity.subject is None and identity.client_id is None:
            return False
        if self.portal_settings.enterprise.require_tenant and identity.tenant_id is None:
            return False
        required = namespace.required_scopes | frozenset(
            self.portal_settings.authorization.namespace_scopes.get(namespace.name, ())
        )
        return required <= identity.scopes

    def _resource_namespace(self, uri: str) -> Namespace | None:
        """Resolve the governed namespace for a concrete or templated resource URI.

        Args:
            uri: Concrete resource URI requested by the client.

        Returns:
            Matching governed namespace, if the resource is namespace-owned.
        """
        namespace = self._component_namespaces.get(("resource", uri))
        if namespace is not None:
            return namespace
        for template in self._resource_manager.list_templates():
            if template.matches(uri):
                return self._component_namespaces.get(("template", template.uri_template))
        return None

    def mount(self, provider: NamespaceProvider, *, namespace: Namespace | str) -> None:
        """Mount every component contributed by a namespace provider.

        Args:
            provider: Declarative provider containing namespace MCP primitives.
            namespace: Namespace prefix to prepend to child tool names.
        """
        namespace_name = namespace.name if isinstance(namespace, Namespace) else namespace
        _install_provider_components(
            provider,
            self,
            prefix=f"{namespace_name}_",
            namespace=namespace,
        )

    def add_provider(self, provider: NamespaceProvider) -> None:
        """Mount an ungoverned development provider without a prefix.

        Args:
            provider: Declarative provider exposing MCP primitives.
        """
        _install_provider_components(provider, self, prefix="")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Enforce identity, policy, quotas, deadlines, size limits, and audit.

        Args:
            name: Mounted MCP tool name.
            arguments: Validated tool arguments received from the client.

        Returns:
            MCP content returned by the underlying SDK tool handler.
        """
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            return await super().call_tool(name, arguments)

        call = _ToolCall(
            name=name,
            arguments=arguments,
            tool=tool,
            invocation=new_invocation(
                name,
                self.portal_settings.enterprise.tenant_claim,
                self.portal_settings.enterprise.tool_timeout(name, tool.meta),
            ),
            started=time.perf_counter(),
        )
        with invocation_scope(call.invocation):
            decision = await self._authorize_tool_call(call)
            await self._verify_tool_approval(call, decision)
            await self._enforce_tool_quota(call)
            return await self._execute_tool_call(call)

    async def _authorize_tool_call(self, call: _ToolCall) -> PolicyDecision:
        """Authorize and audit one tool invocation.

        Args:
            call: Trusted invocation state.

        Returns:
            The allowing policy decision.

        Raises:
            PermissionPortalError: If policy denies the invocation.
        """
        decision = await self.policy_engine.authorize(call.invocation, call.tool, call.arguments)
        if self.portal_settings.enterprise.audit_enabled:
            await self.audit_sink.append(
                audit_event(
                    "authorization",
                    call.invocation,
                    call.arguments,
                    AuditDetails(decision=decision),
                )
            )
        if decision.allowed:
            return decision

        self._record_rejected_tool_call(call, "denied")
        raise PermissionPortalError(
            "Tool invocation is not authorized.",
            details={"required_scopes": sorted(decision.required_scopes)},
        )

    async def _verify_tool_approval(self, call: _ToolCall, decision: PolicyDecision) -> None:
        """Verify any approval obligation attached by policy.

        Args:
            call: Trusted invocation state.
            decision: Allowing policy decision and its obligations.

        Raises:
            PermissionPortalError: If a required approval is absent or invalid.
        """
        if "approval_required" not in decision.obligations or await self.approval_verifier.verify(
            call.invocation, call.tool, call.arguments
        ):
            return

        if self.portal_settings.enterprise.audit_enabled:
            await self.audit_sink.append(
                audit_event(
                    "approval",
                    call.invocation,
                    call.arguments,
                    AuditDetails(
                        decision=PolicyDecision(False, "approval receipt missing or invalid")
                    ),
                )
            )
        self._record_rejected_tool_call(call, "denied")
        raise PermissionPortalError(
            "Destructive tool invocation requires an approved out-of-band receipt."
        )

    async def _enforce_tool_quota(self, call: _ToolCall) -> None:
        """Apply configured request quota controls.

        Args:
            call: Trusted invocation state.

        Raises:
            PermissionPortalError: If the caller's quota is exhausted.
        """
        if not self.enforce_request_controls:
            return

        identity = call.invocation.identity
        actor = identity.subject or identity.client_id or "anonymous"
        quota_key = f"{identity.tenant_id or '-'}:{actor}:{call.name}"
        try:
            await self.admission.check_quota(
                quota_key,
                self.portal_settings.middleware.rate_limit_per_second,
                self.portal_settings.middleware.rate_limit_burst,
            )
        except PermissionPortalError:
            self._record_rejected_tool_call(call, "quota_rejected")
            raise

    async def _execute_tool_call(self, call: _ToolCall) -> Any:
        """Execute a tool under deadline, capacity, response, and telemetry controls.

        Args:
            call: Trusted invocation state.

        Returns:
            MCP content returned by the underlying SDK tool handler.

        Raises:
            TimeoutPortalError: If execution exceeds its deadline.
            UpstreamPortalError: If the serialized response exceeds its size limit.
        """
        outcome = "succeeded"
        admission_started = time.perf_counter()
        admission_wait_seconds = 0.0
        try:
            with anyio.fail_after(call.invocation.deadline_seconds):
                async with self.admission.capacity_for(
                    call.name,
                    self.portal_settings.enterprise.tool_concurrency(call.name, call.tool.meta),
                ):
                    admission_wait_seconds = time.perf_counter() - admission_started
                    result = await super().call_tool(call.name, call.arguments)
            self._validate_tool_response_size(result)
            return result
        except TimeoutError as error:
            outcome = "timed_out"
            raise TimeoutPortalError(
                "Tool execution exceeded its deadline.", cause=error
            ) from error
        except Exception:
            outcome = "failed"
            raise
        finally:
            duration_ms = (time.perf_counter() - call.started) * 1000
            if admission_wait_seconds == 0:
                admission_wait_seconds = time.perf_counter() - admission_started
            await self._record_tool_completion(call, outcome, duration_ms, admission_wait_seconds)

    def _validate_tool_response_size(self, result: Any) -> None:
        """Reject a serialized tool response that exceeds its configured limit.

        Args:
            result: Tool response to measure.

        Raises:
            UpstreamPortalError: If the response is too large.
        """
        maximum_bytes = self.portal_settings.middleware.response_max_bytes
        if not self.enforce_request_controls or maximum_bytes <= 0:
            return

        response_bytes = len(json.dumps(result, default=str).encode("utf-8"))
        if response_bytes > maximum_bytes:
            raise UpstreamPortalError(
                "Tool response exceeded the configured size limit.",
                details={"size": response_bytes},
            )

    def _record_rejected_tool_call(self, call: _ToolCall, outcome: str) -> None:
        """Record telemetry for a rejected invocation.

        Args:
            call: Trusted invocation state.
            outcome: Rejection classification.
        """
        self.telemetry.record_tool_call(
            call.invocation,
            outcome=outcome,
            duration_seconds=time.perf_counter() - call.started,
        )

    async def _record_tool_completion(
        self,
        call: _ToolCall,
        outcome: str,
        duration_ms: float,
        admission_wait_seconds: float,
    ) -> None:
        """Record completion telemetry and audit metadata.

        Args:
            call: Trusted invocation state.
            outcome: Execution result classification.
            duration_ms: Total invocation duration in milliseconds.
            admission_wait_seconds: Time spent awaiting execution capacity.
        """
        self.telemetry.record_tool_call(
            call.invocation,
            outcome=outcome,
            duration_seconds=duration_ms / 1000,
            admission_wait_seconds=admission_wait_seconds,
        )
        if self.portal_settings.enterprise.audit_enabled:
            await self.audit_sink.append(
                audit_event(
                    "completion",
                    call.invocation,
                    call.arguments,
                    AuditDetails(outcome=outcome, duration_ms=duration_ms),
                )
            )

    def http_app(
        self,
        *,
        path: str | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
    ) -> Any:
        """Return a streamable HTTP ASGI app using the old project call shape.

        Args:
            path: Optional MCP endpoint path override.
            json_response: Optional JSON response mode override.
            stateless_http: Optional stateless HTTP mode override.

        Returns:
            A Starlette-compatible streamable HTTP ASGI app.
        """
        _apply_transport_settings(
            self,
            _TransportOverrides(
                path=path,
                json_response=json_response,
                stateless=stateless_http,
            ),
        )
        return self.streamable_http_app()

    def streamable_http_app(self) -> Any:
        """Build the HTTP app and enable LDAP/Kerberos header schemes when selected.

        Returns:
            Streamable HTTP ASGI application with any required scheme adapter.
        """
        app = super().streamable_http_app()
        if isinstance(self._token_verifier, EnterpriseAuthProvider):
            return EnterpriseAuthSchemeMiddleware(app, self._token_verifier)
        return app

    def run(
        self,
        transport: Transport | None = None,
        show_banner: bool | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the SDK server while accepting the previous CLI keyword shape.

        Args:
            transport: Transport protocol to run.
            show_banner: Ignored compatibility flag from FastMCP 3.
            transport_kwargs: Optional transport settings to apply before running.
        """
        _ = show_banner
        selected_transport = transport or "stdio"
        mount_path = None

        if selected_transport in HTTP_TRANSPORTS:
            overrides = _TransportOverrides.from_mapping(transport_kwargs)
            _apply_transport_settings(self, overrides)
            if selected_transport == "sse":
                mount_path = overrides.path

        super().run(
            "streamable-http" if selected_transport == "http" else selected_transport,
            mount_path=mount_path,
        )


def _governed_tool(tool: Tool, name: str, namespace: Namespace | str | None) -> Tool:
    """Apply standard MCP annotations and governed namespace metadata.

    Args:
        tool: Source SDK tool registration.
        name: Fully-qualified mounted name.
        namespace: Optional governed namespace manifest.

    Returns:
        Copied tool registration with normalized public metadata.
    """
    meta = dict(tool.meta or {})
    tags = frozenset(meta.get("tags", ()))
    inferred_annotations = ToolAnnotations(
        title=tool.title,
        readOnlyHint=True if "readonly" in tags else None,
        destructiveHint=(True if "destructive" in tags else False if "readonly" in tags else None),
        idempotentHint=True if tags & {"idempotent", "readonly"} else None,
        openWorldHint=(True if "external" in tags else False if "closed-world" in tags else None),
    )
    annotation_payload = inferred_annotations.model_dump(exclude_none=True)
    if tool.annotations is not None:
        annotation_payload.update(tool.annotations.model_dump(exclude_none=True))
    annotations = ToolAnnotations.model_validate(annotation_payload)
    title = tool.title or annotations.title or name.replace("_", " ").title()
    if isinstance(namespace, Namespace):
        meta.update(
            {
                "namespace": namespace.name,
                "namespace_version": namespace.version,
                "owner": namespace.owner,
                "maturity": namespace.maturity,
                "data_classification": namespace.data_classification,
                "required_scopes": sorted(namespace.required_scopes),
            }
        )
    return tool.model_copy(
        update={
            "name": name,
            "title": title,
            "annotations": annotations,
            "meta": meta,
        }
    )


def _install_provider_components(
    provider: NamespaceProvider,
    target: FastMCP,
    *,
    prefix: str,
    namespace: Namespace | str | None = None,
) -> None:
    """Install tools, resources, templates, and prompts through public SDK APIs.

    Args:
        provider: Declarative namespace component provider.
        target: Server receiving copied component definitions.
        prefix: Prefix to prepend to copied tool and prompt names.
        namespace: Optional governed namespace manifest.
    """
    configured_scopes = (
        frozenset(target.portal_settings.authorization.namespace_scopes.get(namespace.name, ()))
        if isinstance(target, PortalFastMCP) and isinstance(namespace, Namespace)
        else frozenset()
    )
    for contribution in provider.tools:
        tool = Tool.from_function(
            contribution.function,
            name=contribution.name,
            title=contribution.title,
            description=contribution.description,
            annotations=contribution.annotations,
            icons=list(contribution.icons) or None,
            meta=dict(contribution.meta),
            structured_output=contribution.structured_output,
        )
        name = f"{prefix}{tool.name}"
        governed = _governed_tool(tool, name, namespace)
        if isinstance(namespace, Namespace):
            meta = dict(governed.meta or {})
            meta["required_scopes"] = sorted(
                frozenset(meta.get("required_scopes", ())) | configured_scopes
            )
            governed = governed.model_copy(update={"meta": meta})
            if isinstance(target, PortalFastMCP):
                target._component_namespaces[("tool", name)] = namespace
        target.add_tool(
            governed.fn,
            name=governed.name,
            title=governed.title,
            description=governed.description,
            annotations=governed.annotations,
            icons=governed.icons,
            meta=governed.meta,
            structured_output=contribution.structured_output,
        )

    for contribution in provider.resources:
        name = f"{prefix}{contribution.name or contribution.function.__name__}"
        target.resource(
            contribution.uri,
            name=name,
            title=contribution.title,
            description=contribution.description,
            mime_type=contribution.mime_type,
            icons=list(contribution.icons) or None,
            annotations=contribution.annotations,
            meta=dict(contribution.meta),
        )(contribution.function)
        if isinstance(target, PortalFastMCP) and isinstance(namespace, Namespace):
            target._component_namespaces[
                ("template" if contribution.is_template else "resource", contribution.uri)
            ] = namespace

    for contribution in provider.prompts:
        name = f"{prefix}{contribution.name or contribution.function.__name__}"
        target.prompt(
            name=name,
            title=contribution.title,
            description=contribution.description,
            icons=list(contribution.icons) or None,
        )(contribution.function)
        if isinstance(target, PortalFastMCP) and isinstance(namespace, Namespace):
            target._component_namespaces[("prompt", name)] = namespace


def _apply_transport_settings(
    server: FastMCP,
    overrides: _TransportOverrides,
) -> None:
    """Apply previous FastMCP run kwargs to SDK server settings.

    Args:
        server: SDK FastMCP server to mutate before launch.
        overrides: Supported normalized transport overrides.
    """
    if overrides.host is not None:
        server.settings.host = overrides.host
    if overrides.port is not None:
        server.settings.port = overrides.port
    if overrides.path is not None:
        server.settings.streamable_http_path = overrides.path
    if overrides.log_level is not None:
        server.settings.log_level = overrides.log_level.upper()
    if overrides.json_response is not None:
        server.settings.json_response = overrides.json_response
    if overrides.stateless is not None:
        server.settings.stateless_http = overrides.stateless
