"""Provide the FastMCP 3 runtime adapter for MCP Portal."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations, ToolExecution

from mcp_portal.approvals import RejectingApprovalVerifier
from mcp_portal.audit import LoggingAuditSink
from mcp_portal.auth import EnterpriseAuthProvider, EnterpriseAuthSchemeMiddleware
from mcp_portal.config import Settings
from mcp_portal.namespaces import Namespace, NamespaceProvider
from mcp_portal.observability import create_telemetry_recorder
from mcp_portal.policy import PolicyDecision, ScopePolicyEngine
from mcp_portal.resilience import AdmissionController
from mcp_portal.remote import RemoteNamespaceProvider
from mcp_portal.security import identity_from_access_token
from mcp_portal.services import PortalDependencies, PortalServices

Transport = str
HTTP_TRANSPORTS: set[str] = {"http", "sse", "streamable-http"}


class PortalFastMCP(FastMCP):
    """FastMCP 3 server with portal composition and ownership metadata."""

    def __init__(
        self,
        *args: Any,
        portal_settings: Settings | None = None,
        services: PortalServices | None = None,
        dependencies: PortalServices | None = None,
        enforce_request_controls: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize a portal application server.

        Args:
            args: Positional FastMCP server arguments.
            portal_settings: Validated portal deployment settings.
            services: Unified deployment adapter container.
            dependencies: Compatibility alias for `services`.
            enforce_request_controls: Whether operational request limits are enabled.
            kwargs: FastMCP server keyword arguments.
        """
        selected_settings = portal_settings or Settings.from_env()
        selected_services = services or dependencies or PortalServices()
        super().__init__(*args, **kwargs)
        self.portal_settings = selected_settings
        self.services = selected_services
        self.policy_engine = selected_services.policy_engine or ScopePolicyEngine(selected_settings)
        self.audit_sink = selected_services.audit_sink or LoggingAuditSink()
        self.approval_verifier = selected_services.approval_verifier or RejectingApprovalVerifier()
        self.telemetry = selected_services.telemetry or create_telemetry_recorder(
            selected_settings, cost_sink=selected_services.cost_sink
        )
        self.enforce_request_controls = enforce_request_controls
        self.admission = AdmissionController(
            selected_settings.enterprise.max_concurrent_requests,
            selected_services.quota_backend,
        )
        self.component_namespaces: dict[tuple[str, str], Namespace] = {}
        self.remote_namespaces: dict[str, Namespace] = {}
        self.namespace_runtimes: tuple[Any, ...] = ()
        self.clients = selected_services.clients
        self.current_decision: ContextVar[PolicyDecision] = ContextVar(
            f"mcp_portal_decision_{id(self)}"
        )

    def mount(
        self,
        provider: NamespaceProvider | RemoteNamespaceProvider,
        *,
        namespace: Namespace | str,
    ) -> None:
        """Install every component contributed by one namespace.

        Args:
            provider: Declarative namespace component provider.
            namespace: Namespace manifest or prefix.
        """
        namespace_name = namespace.name if isinstance(namespace, Namespace) else namespace
        if isinstance(provider, RemoteNamespaceProvider):
            if not isinstance(namespace, Namespace):
                raise TypeError("Remote namespace providers require a governed Namespace manifest")
            provider.install(self, namespace)
            self.remote_namespaces[namespace.name] = namespace
            return
        _install_provider_components(
            provider,
            self,
            prefix=f"{namespace_name}_",
            namespace=namespace,
        )

    def add_namespace_provider(self, provider: NamespaceProvider) -> None:
        """Install an ungoverned development provider without a prefix.

        Args:
            provider: Declarative development provider.
        """
        _install_provider_components(provider, self, prefix="")

    def namespace_visible(self, namespace: Namespace | None) -> bool:
        """Evaluate namespace catalog scopes for the verified caller.

        Args:
            namespace: Namespace associated with a catalog component.

        Returns:
            True when the namespace may be disclosed.
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

    def component_namespace(self, kind: str, identifier: str) -> Namespace | None:
        """Resolve local or remote ownership for one MCP component.

        Args:
            kind: Component kind such as tool, prompt, resource, or template.
            identifier: Component name or URI.

        Returns:
            Owning namespace when known.
        """
        direct = self.component_namespaces.get((kind, identifier))
        if direct is not None:
            return direct
        if kind in {"tool", "prompt"}:
            return next(
                (
                    namespace
                    for name, namespace in self.remote_namespaces.items()
                    if identifier.startswith(f"{name}_")
                ),
                None,
            )
        return next(
            (
                namespace
                for name, namespace in self.remote_namespaces.items()
                if f"://{name}/" in identifier
            ),
            None,
        )

    async def resource_namespace(self, uri: str) -> Namespace | None:
        """Resolve ownership for a concrete resource URI.

        Args:
            uri: Concrete resource URI.

        Returns:
            Owning namespace when registered.
        """
        namespace = self.component_namespace("resource", uri)
        if namespace is not None:
            return namespace
        for template in await self.list_resource_templates(run_middleware=False):
            if template.matches(uri):
                return self.component_namespace("template", str(template.uri_template))
        return None

    def http_app(self, *args: Any, **kwargs: Any) -> Any:
        """Build the FastMCP HTTP app with enterprise scheme translation.

        Args:
            args: Positional FastMCP HTTP options.
            kwargs: Keyword FastMCP HTTP options.

        Returns:
            Starlette-compatible ASGI application.
        """
        app = super().http_app(*args, **kwargs)
        if isinstance(self.auth, EnterpriseAuthProvider):
            return EnterpriseAuthSchemeMiddleware(app, self.auth)
        return app

    def streamable_http_app(self) -> Any:
        """Return a compatibility streamable-HTTP application.

        Returns:
            Starlette-compatible ASGI application.
        """
        settings = self.portal_settings.http
        return self.http_app(
            path=settings.path,
            json_response=settings.json_response,
            stateless_http=settings.stateless,
            transport="streamable-http",
        )


def _governed_tool(tool: Tool, name: str, namespace: Namespace | str | None) -> Tool:
    """Apply MCP semantics and namespace governance metadata to a tool.

    Args:
        tool: Source FastMCP tool.
        name: Fully-qualified mounted name.
        namespace: Optional governed namespace manifest.

    Returns:
        Copied governed FastMCP tool.
    """
    meta = dict(tool.meta or {})
    tags = frozenset(meta.get("tags", ()))
    inferred = ToolAnnotations(
        title=tool.title,
        readOnlyHint=True if "readonly" in tags else None,
        destructiveHint=True if "destructive" in tags else False if "readonly" in tags else None,
        idempotentHint=True if tags & {"idempotent", "readonly"} else None,
        openWorldHint=True if "external" in tags else False if "closed-world" in tags else None,
    )
    annotation_payload = inferred.model_dump(exclude_none=True)
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
            "execution": ToolExecution(taskSupport=meta.get("task_support", "forbidden")),
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
    """Install namespace components using public FastMCP APIs.

    Args:
        provider: Declarative namespace component provider.
        target: FastMCP server receiving components.
        prefix: Prefix for tool and prompt names.
        namespace: Optional governed namespace manifest.
    """
    configured_scopes = (
        frozenset(target.portal_settings.authorization.namespace_scopes.get(namespace.name, ()))
        if isinstance(target, PortalFastMCP) and isinstance(namespace, Namespace)
        else frozenset()
    )
    for contribution in provider.tools:
        output_schema: Any = None if contribution.structured_output is False else ...
        tool = Tool.from_function(
            contribution.function,
            name=contribution.name,
            title=contribution.title,
            description=contribution.description,
            annotations=contribution.annotations,
            icons=list(contribution.icons) or None,
            meta=dict(contribution.meta),
            output_schema=output_schema,
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
                target.component_namespaces[("tool", name)] = namespace
        target.add_tool(governed)

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
            kind = "template" if contribution.is_template else "resource"
            target.component_namespaces[(kind, contribution.uri)] = namespace

    for contribution in provider.prompts:
        name = f"{prefix}{contribution.name or contribution.function.__name__}"
        target.prompt(
            name=name,
            title=contribution.title,
            description=contribution.description,
            icons=list(contribution.icons) or None,
        )(contribution.function)
        if isinstance(target, PortalFastMCP) and isinstance(namespace, Namespace):
            target.component_namespaces[("prompt", name)] = namespace


__all__ = [
    "HTTP_TRANSPORTS",
    "PortalDependencies",
    "PortalFastMCP",
    "PortalServices",
    "Transport",
]
