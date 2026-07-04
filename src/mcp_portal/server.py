from __future__ import annotations

from collections.abc import Sequence

from fastmcp import FastMCP

from mcp_portal.config import Settings
from mcp_portal.namespaces import Namespace, iter_namespaces


def create_mcp(
    settings: Settings | None = None,
    namespaces: Sequence[Namespace] | None = None,
) -> FastMCP:
    """Create the top-level FastMCP server and mount namespace servers.

    Args:
        settings: Optional settings object. When omitted, settings are loaded from the
            environment.
        namespaces: Optional namespace registry. When omitted, the default namespaces are used.

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

    return server


mcp = create_mcp()


def main() -> None:
    """Run the default FastMCP server over its configured transport."""
    mcp.run()
