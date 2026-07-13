"""Parse command-line options and launch MCP Portal."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP  # noqa: F401 - compatibility test seam

from mcp_portal.config import Settings
from mcp_portal.server.factory import create_mcp, create_production_mcp
from mcp_portal.server.runtime import (  # noqa: F401 - compatibility test seam
    HTTP_TRANSPORTS,
    PortalFastMCP,
)

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
        help="Use the production server profile with an operational health route.",
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
    parser = build_arg_parser()
    options = parser.parse_args(argv)

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
            parser.error(
                f"{', '.join(invalid_flags)} require --transport http, sse, or streamable-http"
            )

    if options.transport == "sse" and options.stateless is True:
        parser.error("--stateless is not supported with --transport sse")

    if options.production:
        server = create_production_mcp(
            Settings.from_env(options.env_file, override=options.env_file is not None)
        )
    elif options.env_file is None:
        server = mcp
    else:
        server = create_mcp(
            Settings.from_env(options.env_file, override=options.env_file is not None),
        )

    transport_options: dict[str, Any] = {}
    if options.log_level is not None:
        transport_options["log_level"] = options.log_level
    if options.stateless is not None:
        transport_options["stateless"] = options.stateless
    if options.transport in HTTP_TRANSPORTS:
        transport_options.update(
            {
                name: value
                for name in ("host", "port", "path", "json_response")
                if (value := getattr(options, name)) is not None
            }
        )

    server.run(
        transport=options.transport,
        show_banner=options.show_banner,
        **transport_options,
    )
