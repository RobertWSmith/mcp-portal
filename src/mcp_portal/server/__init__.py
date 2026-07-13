"""Public server package for runtime, construction, and CLI APIs."""

from mcp.server.fastmcp import FastMCP

from mcp_portal.server.cli import build_arg_parser, main, mcp
from mcp_portal.server.factory import (
    add_operational_routes,
    create_mcp,
    create_portal_lifespan,
    create_production_mcp,
)
from mcp_portal.server.runtime import PortalDependencies, PortalFastMCP

__all__ = [
    "FastMCP",
    "PortalDependencies",
    "PortalFastMCP",
    "add_operational_routes",
    "build_arg_parser",
    "create_mcp",
    "create_portal_lifespan",
    "create_production_mcp",
    "main",
    "mcp",
]

