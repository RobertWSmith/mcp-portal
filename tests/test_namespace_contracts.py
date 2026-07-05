from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from mcp_portal.clients import ClientFactories
from mcp_portal.debug_ui import _runtime_snapshot
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.namespaces import (
    Namespace,
    NamespaceContext,
    NamespaceDebugPanel,
    NamespaceStatus,
    build_namespace_runtimes,
)
from mcp_portal.redaction import Redactor
from mcp_portal.server import create_mcp
from mcp_portal.testing import (
    create_namespace_test_client,
    create_namespace_test_context,
    create_test_settings,
)


def test_redactor_removes_secret_fields_and_literal_values() -> None:
    """Verify diagnostic payloads are redacted by key and literal secret value."""
    redactor = Redactor.from_secrets(("secret-key", None, "your-api-key"))

    assert redactor.redact(
        {
            "openai_api_key": "secret-key",
            "has_openai_api_key": True,
            "message": "using secret-key",
            "nested": {"token": "abc"},
        }
    ) == {
        "openai_api_key": "[REDACTED]",
        "has_openai_api_key": True,
        "message": "using [REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }


def test_portal_error_public_payload_is_redacted() -> None:
    """Verify structured errors expose safe public details."""
    redactor = Redactor.from_secrets(("secret-key",))
    error = ConfigurationPortalError(
        "Missing provider settings.",
        namespace="example",
        details={"api_key": "secret-key", "hint": "set secret-key"},
    )

    assert error.to_public_dict(redactor) == {
        "code": "configuration_error",
        "category": "configuration",
        "message": "Missing provider settings.",
        "namespace": "example",
        "details": {"api_key": "[REDACTED]", "hint": "set [REDACTED]"},
    }


def test_client_factories_create_clients_and_report_missing_entries() -> None:
    """Verify namespaces can request external clients through shared factories."""
    clients = ClientFactories().with_factory("example", lambda: {"ready": True})

    assert clients.create("example") == {"ready": True}
    with pytest.raises(ConfigurationPortalError, match="not configured"):
        clients.require("missing", namespace="example")


def test_namespace_context_exposes_standard_services() -> None:
    """Verify test contexts provide loggers, clocks, redaction, and settings."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = create_namespace_test_context(clock=lambda: now)

    assert context.name == "test"
    assert context.now() == now
    assert context.logger.name == "mcp_portal.namespaces.test"
    assert context.public_snapshot({"api_key": "test-key"}) == {"api_key": "[REDACTED]"}


async def test_disabled_namespace_is_visible_but_not_mounted() -> None:
    """Verify disabled namespaces appear in diagnostics without exposing tools."""
    settings = create_test_settings(health_enabled=False)

    async with Client(create_mcp(settings)) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}
        dashboard_result = await client.call_tool("portal_debug", {})

    assert "health_ping" not in tool_names
    assert "portal_debug" in tool_names
    assert dashboard_result.structured_content is not None
    assert "disabled" in json.dumps(dashboard_result.structured_content)


async def test_namespace_test_client_mounts_one_namespace() -> None:
    """Verify the namespace test harness creates focused in-memory clients."""

    def create_example_server(context: NamespaceContext) -> FastMCP:
        server = FastMCP("Example")

        @server.tool(tags={"example", "readonly"})
        def configured_model() -> str:
            return context.settings.openai_large_language_model

        return server

    namespace = Namespace(
        name="example",
        create=create_example_server,
        description="Example namespace.",
        tags=frozenset({"example", "readonly"}),
    )

    async with create_namespace_test_client(namespace) as client:
        tools = await client.list_tools()
        result = await client.call_tool("example_configured_model", {})

    assert {tool.name for tool in tools} == {"example_configured_model"}
    assert result.data == "large-model"


def test_namespace_debug_snapshot_handles_hook_failures() -> None:
    """Verify debug snapshots convert namespace hook failures into public errors."""

    def create_empty_server(context: NamespaceContext) -> FastMCP:
        return FastMCP(f"Broken {context.name}")

    def broken_status(context: NamespaceContext) -> NamespaceStatus:
        raise RuntimeError(context.name)

    def debug_panel(context: NamespaceContext) -> NamespaceDebugPanel:
        return NamespaceDebugPanel(
            title="Broken Namespace",
            summary="Debug hook still works.",
            snapshot={"api_key": context.settings.openai_api_key},
        )

    settings = create_test_settings()
    namespace = Namespace(
        name="broken",
        create=create_empty_server,
        description="Broken namespace.",
        tags=frozenset({"broken"}),
        health_check=broken_status,
        debug=debug_panel,
    )
    runtimes = build_namespace_runtimes((namespace,), settings)

    namespace_snapshot: dict[str, Any] = _runtime_snapshot(settings, runtimes)["namespaces"][0]

    assert namespace_snapshot["status"]["state"] == "error"
    assert namespace_snapshot["status"]["details"]["error"]["code"] == "internal_error"
    assert namespace_snapshot["debug"]["snapshot"] == {"api_key": "[REDACTED]"}
