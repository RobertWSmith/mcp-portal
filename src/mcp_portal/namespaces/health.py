from __future__ import annotations

from fastmcp import FastMCP

from mcp_portal.config import Settings
from mcp_portal.namespaces import register_namespace


@register_namespace("health")
def create_server(settings: Settings) -> FastMCP:
    """Create the health namespace server.

    Args:
        settings: Runtime settings shared by namespace servers.

    Returns:
        A configured FastMCP child server with health and config tools.
    """
    server = FastMCP("Health")

    @server.tool(tags={"health"})
    def ping() -> str:
        """Return pong to confirm the server is alive.

        Returns:
            The literal string `pong`.
        """
        return "pong"

    @server.tool(tags={"health", "config"})
    def runtime_config() -> dict[str, str | bool]:
        """Return non-secret runtime configuration for development.

        Returns:
            Public runtime settings with secrets omitted.
        """
        return settings.public_snapshot()

    return server
