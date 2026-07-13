"""Construct MCP Portal servers and operational endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
import inspect

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_portal.auth import create_auth_provider
from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.credentials import RejectingCredentialBroker
from mcp_portal.egress import EgressPolicy
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.middleware import create_governance_middleware
from mcp_portal.namespaces import Namespace, build_namespace_runtimes, iter_namespaces
from mcp_portal.observability import configure_observability_environment, create_telemetry_recorder
from mcp_portal.redaction import Redactor
from mcp_portal.resilience import MemoryQuotaBackend
from mcp_portal.server.runtime import PortalDependencies, PortalFastMCP
from mcp_portal.services import PortalServices
from mcp_portal.tasks import MemoryTaskStore
from mcp_portal.telemetry import TelemetryRecorder


def create_mcp(
    settings: Settings | None = None,
    namespaces: Sequence[Namespace] | None = None,
    include_production_middleware: bool | None = None,
    dependencies: PortalDependencies | None = None,
    services: PortalServices | None = None,
) -> FastMCP:
    """Create the top-level FastMCP server and mount namespace servers.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        namespaces: Optional namespace registry. When omitted, the default namespaces are used.
        include_production_middleware: Whether operational request limits are active.
        dependencies: Optional service adapters for external clients, authorization,
            audit, quotas, approvals, tasks, telemetry, and cost accounting.
        services: Preferred name for the unified deployment adapter container.

    Returns:
        A configured FastMCP server with all namespace servers mounted.
    """
    settings = settings or Settings.from_env()
    if dependencies is not None and services is not None:
        raise ValueError("Pass either services or dependencies, not both")
    services = services or dependencies or PortalServices()
    configure_observability_environment(settings)
    shared_telemetry = services.telemetry or create_telemetry_recorder(
        settings, cost_sink=services.cost_sink
    )
    namespace_manifests = tuple(
        namespaces
        if namespaces is not None
        else iter_namespaces(strict=settings.namespace_discovery.strict)
    )
    if settings.enterprise.namespace_allowlist:
        namespace_manifests = tuple(
            namespace
            for namespace in namespace_manifests
            if namespace.name in settings.enterprise.namespace_allowlist
        )
    shared_clients = (
        services.clients.with_resilience(settings, telemetry=shared_telemetry)
        if services.clients is not None
        else default_client_factories(settings, telemetry=shared_telemetry)
    )
    runtime_services = _resolve_portal_services(
        settings,
        services,
        clients=shared_clients,
        telemetry=shared_telemetry,
    )
    namespace_runtimes = build_namespace_runtimes(
        namespace_manifests,
        settings,
        runtime_services,
    )
    server = PortalFastMCP(
        name="MCP Portal",
        instructions="Use namespaced tools for portal capabilities.",
        auth=create_auth_provider(settings),
        lifespan=create_portal_lifespan(shared_clients),
        portal_settings=settings,
        services=runtime_services,
        enforce_request_controls=(
            settings.middleware.enabled
            if include_production_middleware is None
            else include_production_middleware
        ),
    )
    server.namespace_runtimes = namespace_runtimes
    server.clients = shared_clients
    for middleware in create_governance_middleware(server):
        server.add_middleware(middleware)

    for runtime in namespace_runtimes:
        if not settings.namespace_enabled(runtime.namespace.name):
            runtime.context.logger.info("Namespace disabled; skipping provider mount")
            continue

        server.mount(runtime.namespace.create(runtime.context), namespace=runtime.namespace)

    return server


def create_production_mcp(
    settings: Settings | None = None,
    *,
    dependencies: PortalDependencies | None = None,
    services: PortalServices | None = None,
) -> FastMCP:
    """Create the production FastMCP server.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        dependencies: Optional service adapters for production integrations.
        services: Preferred name for production service adapters.

    Returns:
        A configured production server without development UI providers.
    """
    selected_settings = settings or Settings.from_env()
    selected_settings.validate_production()
    server = create_mcp(
        selected_settings,
        include_production_middleware=True,
        dependencies=dependencies,
        services=services,
    )
    validate_production_services(server)
    add_operational_routes(server, selected_settings)
    return server


def _resolve_portal_services(
    settings: Settings,
    services: PortalServices,
    *,
    clients: ClientFactories,
    telemetry: TelemetryRecorder,
) -> PortalServices:
    """Resolve safe defaults once at the application composition root.

    Args:
        settings: Validated deployment configuration.
        services: Explicit deployment adapter overrides.
        clients: Shared lifecycle-managed client registry.
        telemetry: Shared telemetry recorder.

    Returns:
        Fully resolved namespace-facing services.
    """
    redactor = services.redactor or Redactor.from_secrets(
        (
            settings.openai.api_key,
            settings.azure_identity.client_secret,
            settings.auth.static_token,
            settings.auth.jwt_public_key,
            settings.auth.ldap_bind_password,
            settings.database.sqlalchemy_url,
            settings.database.oracle_password,
            settings.mongodb.connection_string,
        )
    )
    return replace(
        services,
        clients=clients,
        telemetry=telemetry,
        redactor=redactor,
        egress_policy=services.egress_policy
        or EgressPolicy(
            allowed_hosts=frozenset(
                host.lower() for host in settings.enterprise.egress_allowed_hosts
            )
        ),
        credential_broker=services.credential_broker or RejectingCredentialBroker(),
        task_store=services.task_store
        or MemoryTaskStore(
            max_ttl_seconds=settings.enterprise.task_max_ttl_seconds,
            max_per_owner=settings.enterprise.task_max_concurrent_per_subject,
        ),
    )


def validate_production_services(server: PortalFastMCP) -> None:
    """Reject process-local state in a declared multi-instance deployment.

    Args:
        server: Fully composed production portal server.

    Raises:
        ConfigurationPortalError: If horizontally scaled state is process-local.
    """
    if not server.portal_settings.enterprise.multi_instance:
        return
    problems: list[str] = []
    if isinstance(server.admission.quota_backend, MemoryQuotaBackend):
        problems.append("multi-instance deployments require a distributed quota backend")
    if isinstance(server.services.task_store, MemoryTaskStore):
        problems.append("multi-instance deployments require a durable shared task store")
    if problems:
        raise ConfigurationPortalError("; ".join(problems))


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
