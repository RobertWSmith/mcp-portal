from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceDebugPanel,
    NamespaceStatus,
    register_namespace,
)


def health_status(context: NamespaceContext) -> NamespaceStatus:
    """Report health namespace runtime status.

    Args:
        context: Runtime services shared with the health namespace.

    Returns:
        Current health namespace status.
    """
    return NamespaceStatus(
        state="ok",
        message="Health namespace is ready.",
        details={
            "namespace": context.name,
            "config": context.settings.health.public_snapshot(),
        },
    )


def health_debug_panel(context: NamespaceContext) -> NamespaceDebugPanel:
    """Build health namespace diagnostics for the central debug UI.

    Args:
        context: Runtime services shared with the health namespace.

    Returns:
        Debug metadata for health namespace tools and settings.
    """
    return NamespaceDebugPanel(
        title="Health Namespace",
        summary="Liveness and public runtime configuration tools.",
        snapshot={
            "namespace": context.name,
            "tools": ["ping", "runtime_config"],
            "settings": context.settings.health.public_snapshot(),
        },
    )


@register_namespace(
    "health",
    description="Liveness checks and non-secret runtime configuration metadata.",
    tags={"core", "health", "readonly"},
    health_check=health_status,
    debug=health_debug_panel,
    owner="platform-engineering",
    version="1.0.0",
    maturity="stable",
    data_classification="internal",
)
def create_server(context: NamespaceContext) -> FastMCP:
    """Create the health namespace server.

    Args:
        context: Runtime services shared with the health namespace.

    Returns:
        A configured FastMCP child server with health and config tools.
    """
    server = FastMCP("Health")

    @server.tool(meta={"tags": ["health", "readonly"]})
    def ping() -> str:
        """Return pong to confirm the server is alive.

        Returns:
            The literal string `pong`.
        """
        context.logger.debug("Health ping requested")
        return "pong"

    @server.tool(meta={"tags": ["health", "config", "readonly"]})
    def runtime_config() -> dict[str, Any]:
        """Return non-secret runtime configuration for development.

        Returns:
            Public runtime settings with secrets omitted.
        """
        context.logger.debug("Health runtime configuration requested")
        return context.public_snapshot(context.settings.public_snapshot())

    @server.resource(
        "portal://runtime/config",
        name="runtime-config",
        description="Non-secret MCP Portal runtime configuration.",
        mime_type="application/json",
    )
    def runtime_config_resource() -> str:
        """Return non-secret configuration as a client-managed MCP resource.

        Returns:
            Canonical JSON representation of public runtime settings.
        """
        return json.dumps(
            context.public_snapshot(context.settings.public_snapshot()), sort_keys=True
        )

    @server.prompt(
        name="diagnose",
        description="Guide a user through safe MCP Portal operational diagnosis.",
    )
    def diagnose_prompt() -> str:
        """Return a user-controlled operational diagnosis prompt.

        Returns:
            Prompt text that avoids requesting or exposing secrets.
        """
        return "Inspect portal health and public runtime metadata without exposing secrets."

    return server
