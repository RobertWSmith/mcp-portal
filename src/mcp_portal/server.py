from __future__ import annotations

# ruff: noqa: E402

import argparse
import inspect
import json
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import time
from typing import Any, Literal

import anyio

# The MCP CLI imports `mcp dev src/mcp_portal/server.py` as a standalone
# file, which skips the package's `src` root unless the project is installed.
if __package__ in {None, ""}:
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "mcp_portal").is_dir():
        source_root_text = str(source_root)
        if source_root_text not in sys.path:
            sys.path.insert(0, source_root_text)

from mcp.server.auth.settings import AuthSettings as SdkAuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import Tool
from mcp.types import ToolAnnotations, ToolExecution
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_portal.auth import (
    EnterpriseAuthProvider,
    EnterpriseAuthSchemeMiddleware,
    create_auth_provider,
)
from mcp_portal.approvals import ApprovalVerifier, RejectingApprovalVerifier
from mcp_portal.audit import AuditSink, LoggingAuditSink, audit_event
from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.debug_ui import create_debug_app
from mcp_portal.errors import PermissionPortalError, TimeoutPortalError, UpstreamPortalError
from mcp_portal.namespaces import (
    Namespace,
    NamespaceProvider,
    build_namespace_runtimes,
    iter_namespaces,
)
from mcp_portal.observability import (
    configure_observability_environment,
    create_telemetry_recorder,
)
from mcp_portal.policy import PolicyDecision, PolicyEngine, ScopePolicyEngine
from mcp_portal.resilience import AdmissionController, QuotaBackend
from mcp_portal.security import identity_from_access_token, invocation_scope, new_invocation
from mcp_portal.tasks import MemoryTaskStore
from mcp_portal.telemetry import CostSink, TelemetryRecorder

Transport = Literal["stdio", "http", "sse", "streamable-http"]
HTTP_TRANSPORTS: set[Transport] = {"http", "sse", "streamable-http"}


class PortalFastMCP(FastMCP):
    """Small compatibility layer around the SDK FastMCP server."""

    def __init__(
        self,
        *args: Any,
        portal_settings: Settings | None = None,
        policy_engine: PolicyEngine | None = None,
        audit_sink: AuditSink | None = None,
        quota_backend: QuotaBackend | None = None,
        approval_verifier: ApprovalVerifier | None = None,
        telemetry: TelemetryRecorder | None = None,
        enforce_request_controls: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the compatibility server with enterprise request controls.

        Args:
            args: Positional SDK server arguments.
            portal_settings: Portal configuration used by enforcement layers.
            policy_engine: Optional external authorization policy adapter.
            audit_sink: Optional append-only audit destination.
            quota_backend: Optional distributed quota implementation.
            approval_verifier: Optional out-of-band approval receipt verifier.
            telemetry: Optional shared metrics and cost-accounting recorder.
            enforce_request_controls: Whether rate and size controls are active.
            kwargs: Keyword SDK server arguments.
        """
        selected_settings = portal_settings or Settings.from_env()
        self.portal_settings = selected_settings
        self.policy_engine = policy_engine or ScopePolicyEngine(selected_settings)
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.enforce_request_controls = enforce_request_controls
        self.approval_verifier = approval_verifier or RejectingApprovalVerifier()
        self.telemetry = telemetry or create_telemetry_recorder(selected_settings)
        self._component_namespaces: dict[tuple[str, str], Namespace] = {}
        self.admission = AdmissionController(
            selected_settings.enterprise.max_concurrent_requests, quota_backend
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
            task_support = (tool.meta or {}).get("task_support", "forbidden")
            exposed_tool = exposed_tool.model_copy(
                update={"execution": ToolExecution(taskSupport=task_support)}
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

        invocation = new_invocation(
            name,
            self.portal_settings.enterprise.tenant_claim,
            self.portal_settings.enterprise.tool_timeout(name, tool.meta),
        )
        tool_concurrency = self.portal_settings.enterprise.tool_concurrency(name, tool.meta)
        started = time.perf_counter()
        with invocation_scope(invocation):
            decision = await self.policy_engine.authorize(invocation, tool, arguments)
            if self.portal_settings.enterprise.audit_enabled:
                await self.audit_sink.append(
                    audit_event("authorization", invocation, arguments, decision=decision)
                )
            if not decision.allowed:
                self.telemetry.record_tool_call(
                    invocation,
                    outcome="denied",
                    duration_seconds=time.perf_counter() - started,
                )
                raise PermissionPortalError(
                    "Tool invocation is not authorized.",
                    details={"required_scopes": sorted(decision.required_scopes)},
                )
            if (
                "approval_required" in decision.obligations
                and not await self.approval_verifier.verify(invocation, tool, arguments)
            ):
                if self.portal_settings.enterprise.audit_enabled:
                    await self.audit_sink.append(
                        audit_event(
                            "approval",
                            invocation,
                            arguments,
                            decision=PolicyDecision(False, "approval receipt missing or invalid"),
                        )
                    )
                self.telemetry.record_tool_call(
                    invocation,
                    outcome="denied",
                    duration_seconds=time.perf_counter() - started,
                )
                raise PermissionPortalError(
                    "Destructive tool invocation requires an approved out-of-band receipt."
                )

            subject = invocation.identity.subject or invocation.identity.client_id or "anonymous"
            quota_key = f"{invocation.identity.tenant_id or '-'}:{subject}:{name}"
            if self.enforce_request_controls:
                try:
                    await self.admission.check_quota(
                        quota_key,
                        self.portal_settings.middleware.rate_limit_per_second,
                        self.portal_settings.middleware.rate_limit_burst,
                    )
                except PermissionPortalError:
                    self.telemetry.record_tool_call(
                        invocation,
                        outcome="quota_rejected",
                        duration_seconds=time.perf_counter() - started,
                    )
                    raise

            outcome = "succeeded"
            admission_started = time.perf_counter()
            admission_wait_seconds = 0.0
            try:
                with anyio.fail_after(invocation.deadline_seconds):
                    async with self.admission.capacity_for(name, tool_concurrency):
                        admission_wait_seconds = time.perf_counter() - admission_started
                        result = await super().call_tool(name, arguments)
                if (
                    self.enforce_request_controls
                    and self.portal_settings.middleware.response_max_bytes > 0
                ):
                    size = len(json.dumps(result, default=str).encode("utf-8"))
                    if size > self.portal_settings.middleware.response_max_bytes:
                        raise UpstreamPortalError(
                            "Tool response exceeded the configured size limit.",
                            details={"size": size},
                        )
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
                duration_ms = (time.perf_counter() - started) * 1000
                if admission_wait_seconds == 0:
                    admission_wait_seconds = time.perf_counter() - admission_started
                self.telemetry.record_tool_call(
                    invocation,
                    outcome=outcome,
                    duration_seconds=duration_ms / 1000,
                    admission_wait_seconds=admission_wait_seconds,
                )
                if self.portal_settings.enterprise.audit_enabled:
                    await self.audit_sink.append(
                        audit_event(
                            "completion",
                            invocation,
                            arguments,
                            outcome=outcome,
                            duration_ms=duration_ms,
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
            path=path,
            json_response=json_response,
            stateless=stateless_http,
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
            _apply_transport_settings(self, **transport_kwargs)
            if selected_transport == "sse":
                mount_path = transport_kwargs.get("path")

        sdk_transport = "streamable-http" if selected_transport == "http" else selected_transport
        super().run(sdk_transport, mount_path=mount_path)


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
        update={"name": name, "title": title, "annotations": annotations, "meta": meta}
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
            component_type = "template" if contribution.is_template else "resource"
            target._component_namespaces[(component_type, contribution.uri)] = namespace

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
    *,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    log_level: str | None = None,
    json_response: bool | None = None,
    stateless: bool | None = None,
    **_: Any,
) -> None:
    """Apply previous FastMCP run kwargs to SDK server settings.

    Args:
        server: SDK FastMCP server to mutate before launch.
        host: Optional HTTP host override.
        port: Optional HTTP port override.
        path: Optional streamable HTTP path override.
        log_level: Optional server log level override.
        json_response: Optional JSON response mode override.
        stateless: Optional stateless HTTP mode override.
        _: Ignored compatibility keyword arguments.
    """
    if host is not None:
        server.settings.host = host
    if port is not None:
        server.settings.port = port
    if path is not None:
        server.settings.streamable_http_path = path
    if log_level is not None:
        server.settings.log_level = log_level.upper()
    if json_response is not None:
        server.settings.json_response = json_response
    if stateless is not None:
        server.settings.stateless_http = stateless


def _sdk_auth_settings(settings: Settings) -> SdkAuthSettings | None:
    """Build the SDK auth settings required when a token verifier is attached.

    Args:
        settings: Portal runtime settings containing auth configuration.

    Returns:
        SDK auth settings when authentication is enabled, otherwise None.
    """
    if not settings.auth.enabled:
        return None

    issuer_url = settings.auth.jwt_issuer or "http://localhost"
    if not issuer_url.startswith(("http://", "https://")):
        issuer_url = "http://localhost"

    resource_path = settings.http.path
    if not resource_path.startswith("/"):
        resource_path = f"/{resource_path}"
    resource_server_url = settings.auth.resource_server_url or f"http://localhost{resource_path}"

    return SdkAuthSettings(
        issuer_url=issuer_url,
        resource_server_url=resource_server_url,
        required_scopes=list(settings.auth.required_scopes),
    )


def create_mcp(
    settings: Settings | None = None,
    namespaces: Sequence[Namespace] | None = None,
    include_debug_ui: bool = True,
    include_production_middleware: bool | None = None,
    clients: ClientFactories | None = None,
    policy_engine: PolicyEngine | None = None,
    audit_sink: AuditSink | None = None,
    quota_backend: QuotaBackend | None = None,
    approval_verifier: ApprovalVerifier | None = None,
    task_store: MemoryTaskStore | None = None,
    telemetry: TelemetryRecorder | None = None,
    cost_sink: CostSink | None = None,
) -> FastMCP:
    """Create the top-level FastMCP server and mount namespace servers.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        namespaces: Optional namespace registry. When omitted, the default namespaces are used.
        include_debug_ui: Whether to add development debug tools.
        include_production_middleware: Retained for CLI compatibility. The SDK
            FastMCP server does not expose FastMCP 3 middleware hooks.
        clients: Optional shared client factory registry.
        policy_engine: Optional external authorization policy adapter.
        audit_sink: Optional append-only audit destination.
        quota_backend: Optional shared quota backend.
        approval_verifier: Optional approval receipt verifier.
        task_store: Optional shared authorization-bound task store.
        telemetry: Optional metrics and cost-accounting recorder.
        cost_sink: Optional detailed accounting destination used by the default recorder.

    Returns:
        A configured FastMCP server with all namespace servers mounted.
    """
    settings = settings or Settings.from_env()
    configure_observability_environment(settings)
    shared_telemetry = telemetry or create_telemetry_recorder(settings, cost_sink=cost_sink)
    namespace_manifests = tuple(
        namespaces or iter_namespaces(strict=settings.namespace_discovery.strict)
    )
    if settings.enterprise.namespace_allowlist:
        approved = frozenset(settings.enterprise.namespace_allowlist)
        namespace_manifests = tuple(
            namespace for namespace in namespace_manifests if namespace.name in approved
        )
    shared_clients = (
        clients.with_resilience(settings, telemetry=shared_telemetry)
        if clients is not None
        else default_client_factories(settings, telemetry=shared_telemetry)
    )
    namespace_runtimes = build_namespace_runtimes(
        namespace_manifests,
        settings,
        clients=shared_clients,
        tasks=task_store,
        telemetry=shared_telemetry,
    )
    auth_provider = create_auth_provider(settings)
    enforce_request_controls = (
        settings.middleware.enabled
        if include_production_middleware is None
        else include_production_middleware
    )
    server = PortalFastMCP(
        name="MCP Portal",
        instructions="Use namespaced tools for portal capabilities.",
        auth=_sdk_auth_settings(settings),
        token_verifier=auth_provider,
        streamable_http_path=settings.http.path,
        json_response=bool(settings.http.json_response),
        stateless_http=bool(settings.http.stateless),
        lifespan=create_portal_lifespan(shared_clients),
        portal_settings=settings,
        policy_engine=policy_engine,
        audit_sink=audit_sink,
        quota_backend=quota_backend,
        approval_verifier=approval_verifier,
        telemetry=shared_telemetry,
        enforce_request_controls=enforce_request_controls,
    )
    server.namespace_runtimes = namespace_runtimes
    server.clients = shared_clients

    for runtime in namespace_runtimes:
        if not settings.namespace_enabled(runtime.namespace.name):
            runtime.context.logger.info("Namespace disabled; skipping provider mount")
            continue

        server.mount(runtime.namespace.create(runtime.context), namespace=runtime.namespace)

    if include_debug_ui:
        server.add_provider(create_debug_app(settings, namespace_runtimes))

    return server


def create_production_mcp(
    settings: Settings | None = None,
    *,
    telemetry: TelemetryRecorder | None = None,
    cost_sink: CostSink | None = None,
) -> FastMCP:
    """Create the production FastMCP server.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        telemetry: Optional metrics and cost-accounting recorder.
        cost_sink: Optional detailed accounting destination used by the default recorder.

    Returns:
        A configured production server without development UI providers.
    """
    selected_settings = settings or Settings.from_env()
    selected_settings.validate_production()
    server = create_mcp(
        selected_settings,
        include_debug_ui=False,
        include_production_middleware=True,
        telemetry=telemetry,
        cost_sink=cost_sink,
    )
    add_operational_routes(server, selected_settings)
    return server


def create_portal_lifespan(clients: ClientFactories):
    """Create a FastMCP lifespan that manages shared external clients.

    Args:
        clients: Shared client factory registry to close during shutdown.

    Returns:
        A composable FastMCP lifespan.
    """

    @asynccontextmanager
    async def portal_lifespan(server: FastMCP):
        """Manage portal startup and shutdown resources.

        Args:
            server: FastMCP server entering its lifespan.
        """
        try:
            yield {"clients": clients}
        finally:
            await clients.aclose()

    return portal_lifespan


def add_operational_routes(server: FastMCP, settings: Settings) -> FastMCP:
    """Attach unauthenticated operational routes to an HTTP-capable server.

    Args:
        server: FastMCP server receiving operational routes.
        settings: Runtime settings containing HTTP route paths.

    Returns:
        The same server, with routes attached.
    """

    @server.custom_route(settings.http.health_path, methods=["GET"], include_in_schema=False)
    async def health_check(request: Request) -> Response:
        """Return a dependency-free process liveness response.

        Args:
            request: Starlette request for the health endpoint.

        Returns:
            A minimal JSON response suitable for a liveness probe.
        """
        _ = request
        return JSONResponse(
            {
                "status": "alive",
                "service": "mcp-portal",
            }
        )

    @server.custom_route(settings.http.readiness_path, methods=["GET"], include_in_schema=False)
    async def readiness_check(request: Request) -> Response:
        """Return dependency and namespace readiness for traffic admission.

        Args:
            request: Starlette request for the readiness endpoint.

        Returns:
            JSON readiness result with a failing status when a namespace is unhealthy.
        """
        _ = request
        statuses: dict[str, str] = {}
        ready = True
        for runtime in getattr(server, "namespace_runtimes", ()):
            if not settings.namespace_enabled(runtime.namespace.name):
                continue
            if runtime.namespace.health_check is None:
                statuses[runtime.namespace.name] = "unknown"
                continue
            try:
                status = runtime.namespace.health_check(runtime.context)
                if inspect.isawaitable(status):
                    status = await status
                statuses[runtime.namespace.name] = status.state
                ready = ready and status.state in {"ok", "warning"}
            except Exception:
                statuses[runtime.namespace.name] = "error"
                ready = False
        dependency_statuses = await server.clients.check_readiness()
        ready = ready and all(status["status"] == "ok" for status in dependency_statuses.values())
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "namespaces": statuses,
                "dependencies": dependency_statuses,
            },
            status_code=200 if ready else 503,
        )

    return server


mcp = create_mcp()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the console script.

    Returns:
        An argument parser with FastMCP launch options.
    """
    parser = argparse.ArgumentParser(description="Run the MCP Portal FastMCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "sse", "streamable-http"),
        default="stdio",
        help="Transport protocol to use. Defaults to stdio.",
    )
    parser.add_argument(
        "--host",
        help="Host to bind for HTTP-based transports.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port to bind for HTTP-based transports.",
    )
    parser.add_argument(
        "--path",
        help="Endpoint path for HTTP-based transports.",
    )
    parser.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error", "critical"),
        help="Server log level.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Dotenv file to load before creating the server.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Use the production server profile: no debug tools and an operational health route.",
    )

    banner_group = parser.add_mutually_exclusive_group()
    banner_group.add_argument(
        "--show-banner",
        dest="show_banner",
        action="store_true",
        default=None,
        help="Show FastMCP's server banner.",
    )
    banner_group.add_argument(
        "--no-banner",
        dest="show_banner",
        action="store_false",
        help="Hide FastMCP's server banner.",
    )

    debug_ui_group = parser.add_mutually_exclusive_group()
    debug_ui_group.add_argument(
        "--debug-ui",
        dest="debug_ui",
        action="store_true",
        default=True,
        help="Include MCP Portal debug tools. Enabled by default.",
    )
    debug_ui_group.add_argument(
        "--no-debug-ui",
        dest="debug_ui",
        action="store_false",
        help="Run without MCP Portal debug tools.",
    )

    json_group = parser.add_mutually_exclusive_group()
    json_group.add_argument(
        "--json-response",
        dest="json_response",
        action="store_true",
        default=None,
        help="Use JSON responses for HTTP-based transports.",
    )
    json_group.add_argument(
        "--no-json-response",
        dest="json_response",
        action="store_false",
        help="Disable JSON responses for HTTP-based transports.",
    )

    state_group = parser.add_mutually_exclusive_group()
    state_group.add_argument(
        "--stateless",
        dest="stateless",
        action="store_true",
        default=None,
        help="Run without session initialization or server-side session state.",
    )
    state_group.add_argument(
        "--stateful",
        dest="stateless",
        action="store_false",
        help="Run with server-side session state.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the FastMCP server from command-line options.

    Args:
        argv: Optional command-line arguments. When omitted, arguments are read from
            `sys.argv`.
    """
    options = _parse_args(argv)
    server = _server_for_cli_options(options)

    server.run(
        transport=options.transport,
        show_banner=options.show_banner,
        **_transport_kwargs(options),
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse and validate command-line arguments.

    Args:
        argv: Optional command-line arguments. When omitted, arguments are read from
            `sys.argv`.

    Returns:
        Parsed command-line options.
    """
    parser = build_arg_parser()
    options = parser.parse_args(argv)
    _validate_options(parser, options)
    return options


def _validate_options(parser: argparse.ArgumentParser, options: argparse.Namespace) -> None:
    """Validate option combinations that depend on the selected transport.

    Args:
        parser: Parser used to report user-facing command errors.
        options: Parsed command-line options.
    """
    if options.transport == "stdio":
        invalid_flags = [
            flag
            for value, flag in (
                (options.host, "--host"),
                (options.port, "--port"),
                (options.path, "--path"),
                (options.json_response, "--json-response/--no-json-response"),
            )
            if value is not None
        ]
        if invalid_flags:
            flags = ", ".join(invalid_flags)
            parser.error(f"{flags} require --transport http, sse, or streamable-http")

    if options.transport == "sse" and options.stateless is True:
        parser.error("--stateless is not supported with --transport sse")


def _server_for_cli_options(options: argparse.Namespace) -> FastMCP:
    """Return the server instance to run for the parsed options.

    Args:
        options: Parsed command-line options.

    Returns:
        The default module server or a freshly configured server when options require it.
    """
    if options.production:
        return create_production_mcp(
            Settings.from_env(options.env_file, override=options.env_file is not None)
        )

    if options.env_file is None and options.debug_ui:
        return mcp

    return create_mcp(
        Settings.from_env(options.env_file, override=options.env_file is not None),
        include_debug_ui=options.debug_ui,
    )


def _transport_kwargs(options: argparse.Namespace) -> dict[str, Any]:
    """Build FastMCP transport keyword arguments from parsed options.

    Args:
        options: Parsed command-line options.

    Returns:
        Keyword arguments safe to pass to `FastMCP.run`.
    """
    kwargs: dict[str, Any] = {}

    if options.log_level is not None:
        kwargs["log_level"] = options.log_level
    if options.stateless is not None:
        kwargs["stateless"] = options.stateless

    if options.transport in HTTP_TRANSPORTS:
        for name in ("host", "port", "path", "json_response"):
            value = getattr(options, name)
            if value is not None:
                kwargs[name] = value

    return kwargs


if __name__ == "__main__":
    main()
