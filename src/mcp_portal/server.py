from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP

from mcp_portal.config import Settings
from mcp_portal.debug_ui import create_debug_app
from mcp_portal.namespaces import Namespace, iter_namespaces

Transport = Literal["stdio", "http", "sse", "streamable-http"]
HTTP_TRANSPORTS: set[Transport] = {"http", "sse", "streamable-http"}


def create_mcp(
    settings: Settings | None = None,
    namespaces: Sequence[Namespace] | None = None,
    include_debug_ui: bool = True,
) -> FastMCP:
    """Create the top-level FastMCP server and mount namespace servers.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        namespaces: Optional namespace registry. When omitted, the default namespaces are used.
        include_debug_ui: Whether to add the FastMCP Apps development dashboard.

    Returns:
        A configured FastMCP server with all namespace servers mounted.
    """
    settings = settings or Settings.from_env()
    server = FastMCP(
        name="MCP Portal",
        instructions="Use namespaced tools for portal capabilities.",
    )

    for namespace in namespaces or iter_namespaces():
        server.mount(namespace.create(settings), namespace=namespace.name)

    if include_debug_ui:
        server.add_provider(create_debug_app(settings))

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
