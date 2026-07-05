from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_portal.auth import create_auth_provider
from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.debug_ui import create_debug_app
from mcp_portal.middleware import create_production_middleware
from mcp_portal.namespaces import Namespace, build_namespace_runtimes, iter_namespaces
from mcp_portal.observability import configure_observability_environment

Transport = Literal["stdio", "http", "sse", "streamable-http"]
HTTP_TRANSPORTS: set[Transport] = {"http", "sse", "streamable-http"}


def create_mcp(
    settings: Settings | None = None,
    namespaces: Sequence[Namespace] | None = None,
    include_debug_ui: bool = True,
    include_production_middleware: bool | None = None,
    clients: ClientFactories | None = None,
) -> FastMCP:
    """Create the top-level FastMCP server and mount namespace servers.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        namespaces: Optional namespace registry. When omitted, the default namespaces are used.
        include_debug_ui: Whether to add the FastMCP Apps development dashboard.
        include_production_middleware: Whether to attach production middleware. When
            omitted, the value comes from settings.
        clients: Optional shared client factory registry.

    Returns:
        A configured FastMCP server with all namespace servers mounted.
    """
    settings = settings or Settings.from_env()
    configure_observability_environment(settings)
    namespace_manifests = tuple(
        namespaces or iter_namespaces(strict=settings.namespace_discovery.strict)
    )
    shared_clients = clients or default_client_factories(settings)
    namespace_runtimes = build_namespace_runtimes(
        namespace_manifests,
        settings,
        clients=shared_clients,
    )
    server = FastMCP(
        name="MCP Portal",
        instructions="Use namespaced tools for portal capabilities.",
        auth=create_auth_provider(settings),
        middleware=create_production_middleware(
            settings,
            enabled=include_production_middleware,
        ),
        lifespan=create_portal_lifespan(shared_clients),
    )

    for runtime in namespace_runtimes:
        if not settings.namespace_enabled(runtime.namespace.name):
            runtime.context.logger.info("Namespace disabled; skipping tool mount")
            continue

        server.mount(runtime.namespace.create(runtime.context), namespace=runtime.namespace.name)

    if include_debug_ui:
        server.add_provider(create_debug_app(settings, namespace_runtimes))

    return server


def create_production_mcp(settings: Settings | None = None) -> FastMCP:
    """Create the production FastMCP server.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.

    Returns:
        A configured production server without development UI providers.
    """
    selected_settings = settings or Settings.from_env()
    server = create_mcp(
        selected_settings,
        include_debug_ui=False,
        include_production_middleware=True,
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

    @lifespan
    async def portal_lifespan(server: FastMCP):
        """Manage portal startup and shutdown resources.

        Args:
            server: FastMCP server entering its lifespan.
        """
        yield {"clients": clients}
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
        """Return an operational health response.

        Args:
            request: Starlette request for the health endpoint.

        Returns:
            A JSON health response for load balancers and probes.
        """
        _ = request
        return JSONResponse(
            {
                "status": "healthy",
                "service": "mcp-portal",
                "mcp_path": settings.http.path,
                "oracle_preferred": settings.database.provider == "oracle",
                "sqlalchemy_enforced": True,
                "database_configured": settings.database.sqlalchemy_configured,
                "oracle_configured": settings.database.oracle_configured,
            }
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
        help="Use the production server profile: no debug UI and production middleware on.",
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
        help="Include the FastMCP Apps debug provider. Enabled by default.",
    )
    debug_ui_group.add_argument(
        "--no-debug-ui",
        dest="debug_ui",
        action="store_false",
        help="Run without the FastMCP Apps debug provider.",
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
