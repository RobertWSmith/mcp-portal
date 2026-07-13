"""Provide health and runtime-configuration MCP components."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceMetadata,
    NamespaceProvider,
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


@register_namespace(
    NamespaceMetadata(
        name="health",
        description="Liveness checks and non-secret runtime configuration metadata.",
        tags=frozenset({"core", "health", "readonly"}),
        health_check=health_status,
        owner="platform-engineering",
        version="1.0.0",
        maturity="stable",
        data_classification="internal",
        timeout_seconds=10.0,
    )
)
def create_provider(context: NamespaceContext) -> NamespaceProvider:
    """Create the health namespace provider.

    Args:
        context: Runtime services shared with the health namespace.

    Returns:
        A complete provider demonstrating tools, resources, templates, and prompts.
    """
    provider = NamespaceProvider("Health")

    @provider.tool(
        title="Portal Health Check",
        description=(
            "Confirm that the MCP server can execute tools.\n\n"
            "Returns:\n"
            "    Structured liveness state and acknowledgement.\n"
        ),
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

    @provider.tool(
        title="Public Runtime Configuration",
        description=(
            "Return non-secret runtime configuration for development.\n\n"
            "Returns:\n"
            "    Validated public runtime settings with secrets omitted.\n"
        ),
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
        return RuntimeConfigResult.model_validate(
            context.public_snapshot(context.settings.public_snapshot())
        )

    @provider.resource(
        "portal://health/runtime/config",
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

    @provider.resource(
        "portal://health/runtime/{section}",
        name="runtime-section",
        title="Runtime Configuration Section",
        description="One non-secret section of MCP Portal runtime configuration.",
        mime_type="application/json",
    )
    def runtime_section_resource(section: str) -> str:
        """Return one public runtime configuration section.

        Args:
            section: Top-level public settings section to retrieve.

        Returns:
            Canonical JSON representation of the requested section.

        Raises:
            ValueError: If the requested public settings section does not exist.
        """
        snapshot = context.public_snapshot(context.settings.public_snapshot())
        if section not in snapshot:
            raise ValueError(f"Unknown public runtime configuration section: {section}")
        return json.dumps(snapshot[section], sort_keys=True)

    @provider.prompt(
        name="diagnose",
        title="Diagnose MCP Portal",
        description="Guide a user through safe MCP Portal operational diagnosis.",
    )
    def diagnose_prompt(focus: str = "runtime configuration") -> str:
        """Return a user-controlled operational diagnosis prompt.

        Args:
            focus: Operational area the user wants to investigate.

        Returns:
            Prompt text that avoids requesting or exposing secrets.
        """
        return (
            f"Diagnose MCP Portal {focus} using health tools and portal://health resources. "
            "Do not request, reveal, or infer secret values."
        )

    return provider
