from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceDebugPanel,
    NamespaceStatus,
    register_namespace,
)


class HealthPingResult(BaseModel):
    """Structured result returned by the health ping tool.

    Attributes:
        status: Machine-readable health state.
        message: Stable liveness acknowledgement.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = Field(description="Machine-readable health state.")
    message: Literal["pong"] = Field(description="Stable liveness acknowledgement.")


class RuntimeConfigResult(BaseModel):
    """Structured, non-secret runtime configuration exposed through MCP.

    Attributes:
        model_provider: Active provider and model selection metadata.
        openai: Direct OpenAI provider metadata.
        azure_openai: Azure OpenAI provider metadata.
        azure_identity: Azure identity configuration metadata.
        health: Health namespace configuration.
        auth: Authentication posture metadata.
        authorization: Authorization policy metadata.
        middleware: Request middleware configuration.
        http: HTTP transport configuration.
        enterprise: Enterprise control-plane configuration.
        namespace_discovery: Namespace discovery configuration.
        observability: Telemetry export configuration.
        database: Relational database configuration metadata.
        mongodb: MongoDB connector configuration metadata.
    """

    model_config = ConfigDict(extra="forbid")

    model_provider: dict[str, Any]
    openai: dict[str, Any]
    azure_openai: dict[str, Any]
    azure_identity: dict[str, Any]
    health: dict[str, Any]
    auth: dict[str, Any]
    authorization: dict[str, Any]
    middleware: dict[str, Any]
    http: dict[str, Any]
    enterprise: dict[str, Any]
    namespace_discovery: dict[str, Any]
    observability: dict[str, Any]
    database: dict[str, Any]
    mongodb: dict[str, Any]


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

    @server.tool(
        title="Portal Health Check",
        annotations=ToolAnnotations(
            title="Portal Health Check",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"tags": ["health", "readonly"]},
        structured_output=True,
    )
    def ping() -> HealthPingResult:
        """Confirm that the MCP server can execute tools.

        Returns:
            Structured liveness state and acknowledgement.
        """
        context.logger.debug("Health ping requested")
        return HealthPingResult(status="ok", message="pong")

    @server.tool(
        title="Public Runtime Configuration",
        annotations=ToolAnnotations(
            title="Public Runtime Configuration",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"tags": ["health", "config", "readonly"]},
        structured_output=True,
    )
    def runtime_config() -> RuntimeConfigResult:
        """Return non-secret runtime configuration for development.

        Returns:
            Validated public runtime settings with secrets omitted.
        """
        context.logger.debug("Health runtime configuration requested")
        snapshot = context.public_snapshot(context.settings.public_snapshot())
        return RuntimeConfigResult.model_validate(snapshot)

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
