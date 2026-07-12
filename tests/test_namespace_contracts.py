"""Test namespace service contracts, redaction, clients, and test helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastmcp import Client

from mcp_portal.clients import ClientFactories
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.namespaces import (
    Namespace,
    NamespaceContext,
    NamespaceDependencies,
    NamespaceProvider,
)
from mcp_portal.redaction import Redactor
from mcp_portal.server import create_mcp
from mcp_portal.testing import (
    SettingsOverrides,
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
            "authorization": {"tag_scopes": {"write": ["write"]}},
        }
    ) == {
        "openai_api_key": "[REDACTED]",
        "has_openai_api_key": True,
        "message": "using [REDACTED]",
        "nested": {"token": "[REDACTED]"},
        "authorization": {"tag_scopes": {"write": ["write"]}},
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
    context = create_namespace_test_context(dependencies=NamespaceDependencies(clock=lambda: now))
    azure_context = create_namespace_test_context(
        settings=create_test_settings(SettingsOverrides(azure_client_secret="azure-secret"))
    )

    assert context.name == "test"
    assert context.now() == now
    assert context.logger.name == "mcp_portal.namespaces.test"
    assert context.public_snapshot({"api_key": "test-key"}) == {"api_key": "[REDACTED]"}
    assert azure_context.public_snapshot(
        {"client_secret": "azure-secret", "message": "using azure-secret"}
    ) == {"client_secret": "[REDACTED]", "message": "using [REDACTED]"}


async def test_disabled_namespace_is_visible_but_not_mounted() -> None:
    """Verify disabled namespaces do not expose tools."""
    settings = create_test_settings(SettingsOverrides(health_enabled=False))

    async with Client(create_mcp(settings)) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}

    assert "health_ping" not in tool_names
    assert not tool_names


async def test_namespace_test_client_mounts_one_namespace() -> None:
    """Verify the namespace test harness creates focused in-memory clients."""

    def create_example_provider(context: NamespaceContext) -> NamespaceProvider:
        provider = NamespaceProvider("Example")

        @provider.tool(meta={"tags": ["example", "readonly"]})
        def configured_model() -> str:
            return context.settings.large_language_model

        return provider

    namespace = Namespace(
        name="example",
        create=create_example_provider,
        description="Example namespace.",
        tags=frozenset({"example", "readonly"}),
    )

    async with create_namespace_test_client(namespace) as client:
        tools = await client.list_tools()
        result = await client.call_tool("example_configured_model", {})

    assert {tool.name for tool in tools} == {"example_configured_model"}
    assert result.content[0].text == "large-model"
