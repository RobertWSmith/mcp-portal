"""Construct MCP Portal servers and operational endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
import inspect

from mcp.server.auth.settings import AuthSettings as SdkAuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_portal.auth import create_auth_provider
from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.namespaces import Namespace, NamespaceDependencies, build_namespace_runtimes, iter_namespaces
from mcp_portal.observability import configure_observability_environment, create_telemetry_recorder
from mcp_portal.server.runtime import PortalDependencies, PortalFastMCP

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
    return SdkAuthSettings(
        issuer_url=issuer_url,
        resource_server_url=(
            settings.auth.resource_server_url or f"http://localhost{resource_path}"
        ),
        required_scopes=list(settings.auth.required_scopes),
    )


def create_mcp(
    settings: Settings | None = None,
    namespaces: Sequence[Namespace] | None = None,
    include_production_middleware: bool | None = None,
    dependencies: PortalDependencies | None = None,
) -> FastMCP:
    """Create the top-level FastMCP server and mount namespace servers.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        namespaces: Optional namespace registry. When omitted, the default namespaces are used.
        include_production_middleware: Retained for CLI compatibility. The SDK
            FastMCP server does not expose FastMCP 3 middleware hooks.
        dependencies: Optional service adapters for external clients, authorization,
            audit, quotas, approvals, tasks, telemetry, and cost accounting.

    Returns:
        A configured FastMCP server with all namespace servers mounted.
    """
    settings = settings or Settings.from_env()
    dependencies = dependencies or PortalDependencies()
    configure_observability_environment(settings)
    shared_telemetry = dependencies.telemetry or create_telemetry_recorder(
        settings, cost_sink=dependencies.cost_sink
    )
    namespace_manifests = tuple(
        namespaces or iter_namespaces(strict=settings.namespace_discovery.strict)
    )
    if settings.enterprise.namespace_allowlist:
        namespace_manifests = tuple(
            namespace
            for namespace in namespace_manifests
            if namespace.name in settings.enterprise.namespace_allowlist
        )
    shared_clients = (
        dependencies.clients.with_resilience(settings, telemetry=shared_telemetry)
        if dependencies.clients is not None
        else default_client_factories(settings, telemetry=shared_telemetry)
    )
    namespace_runtimes = build_namespace_runtimes(
        namespace_manifests,
        settings,
        NamespaceDependencies(
            clients=shared_clients,
            tasks=dependencies.task_store,
            telemetry=shared_telemetry,
        ),
    )
    runtime_dependencies = replace(
        dependencies,
        clients=shared_clients,
        telemetry=shared_telemetry,
    )
    server = PortalFastMCP(
        name="MCP Portal",
        instructions="Use namespaced tools for portal capabilities.",
        auth=_sdk_auth_settings(settings),
        token_verifier=create_auth_provider(settings),
        streamable_http_path=settings.http.path,
        json_response=bool(settings.http.json_response),
        stateless_http=bool(settings.http.stateless),
        lifespan=create_portal_lifespan(shared_clients),
        portal_settings=settings,
        dependencies=runtime_dependencies,
        enforce_request_controls=(
            settings.middleware.enabled
            if include_production_middleware is None
            else include_production_middleware
        ),
    )
    server.namespace_runtimes = namespace_runtimes
    server.clients = shared_clients

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
) -> FastMCP:
    """Create the production FastMCP server.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        dependencies: Optional service adapters for production integrations.

    Returns:
        A configured production server without development UI providers.
    """
    selected_settings = settings or Settings.from_env()
    selected_settings.validate_production()
    server = create_mcp(
        selected_settings,
        include_production_middleware=True,
        dependencies=dependencies,
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

