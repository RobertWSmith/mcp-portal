"""Test server construction and the built-in health namespace surface."""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

import mcp_portal.namespaces as namespace_registry
from mcp_portal.config import Settings
from mcp_portal.namespaces import (
    Namespace,
    NamespaceContext,
    NamespaceMetadata,
    NamespaceProvider,
)
from mcp_portal.server import create_mcp
from mcp_portal.testing import create_test_settings


@pytest.fixture
def settings() -> Settings:
    """Create deterministic settings for server tests.

    Returns:
        Settings with placeholder test model values.
    """
    return create_test_settings()


@pytest.fixture
async def client(settings: Settings):
    """Create an in-memory FastMCP client for the composed server.

    Args:
        settings: Deterministic test settings.

    Yields:
        Connected FastMCP client backed by the in-memory server.
    """
    async with Client(create_mcp(settings)) as mcp_client:
        yield mcp_client


async def test_default_namespaces_are_mounted(client: Client) -> None:
    """Verify default namespace tools are exposed with FastMCP prefixes."""
    tools = await client.list_tools()
    tool_names = {tool.name for tool in tools}

    assert {"health_ping", "health_runtime_config"} <= tool_names


async def test_health_tools_publish_standard_mcp_semantics(client: Client) -> None:
    """Verify health tools expose titles, annotations, execution, and output schemas."""
    tools = {tool.name: tool for tool in await client.list_tools()}
    ping = tools["health_ping"]
    runtime_config = tools["health_runtime_config"]

    assert ping.title == "Portal Health Check"
    assert ping.annotations is not None
    assert ping.annotations.model_dump(exclude_none=True) == {
        "title": "Portal Health Check",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert ping.execution is not None
    assert ping.execution.taskSupport == "forbidden"
    assert ping.outputSchema["properties"]["status"]["const"] == "ok"
    assert runtime_config.title == "Public Runtime Configuration"
    assert set(runtime_config.outputSchema["required"]) == {
        "auth",
        "authorization",
        "azure_identity",
        "azure_openai",
        "database",
        "enterprise",
        "health",
        "http",
        "middleware",
        "model_provider",
        "mongodb",
        "namespace_discovery",
        "observability",
        "openai",
    }


async def test_health_namespace_publishes_complete_server_surface(client: Client) -> None:
    """Verify the reference namespace exposes resources, a template, and a prompt."""
    resources = await client.list_resources()
    templates = await client.list_resource_templates()
    prompts = await client.list_prompts()

    assert [(str(resource.uri), resource.name) for resource in resources] == [
        ("portal://health/runtime/config", "health_runtime-config")
    ]
    assert [(template.uriTemplate, template.name) for template in templates] == [
        ("portal://health/runtime/{section}", "health_runtime-section")
    ]
    assert [prompt.name for prompt in prompts] == ["health_diagnose"]

    config_contents = await client.read_resource("portal://health/runtime/config")
    section_contents = await client.read_resource("portal://health/runtime/health")
    diagnosis = await client.get_prompt("health_diagnose", {"focus": "authorization"})

    assert '"model_provider"' in config_contents[0].text
    assert json.loads(section_contents[0].text) == {"enabled": True}
    assert "Diagnose MCP Portal authorization" in diagnosis.messages[0].content.text


def test_namespace_registration_decorator_records_factory(monkeypatch) -> None:
    """Verify namespace factories can register themselves with a decorator."""
    monkeypatch.setattr(namespace_registry, "_NAMESPACE_REGISTRY", {})
    monkeypatch.setattr(namespace_registry, "_DISCOVERED", True)

    def create_example_provider(context: NamespaceContext) -> NamespaceProvider:
        """Create a placeholder namespace provider.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured namespace provider.
        """
        return NamespaceProvider(f"Example {context.settings.large_language_model}")

    decorated = namespace_registry.register_namespace(
        NamespaceMetadata(
            name="example",
            description="Example namespace.",
            tags=frozenset({"example", "test"}),
        )
    )(create_example_provider)

    assert decorated is create_example_provider
    assert namespace_registry.iter_namespaces() == (
        Namespace(
            "example",
            create_example_provider,
            description="Example namespace.",
            tags=frozenset({"example", "test"}),
        ),
    )


def test_namespace_registration_rejects_duplicate_names(monkeypatch) -> None:
    """Verify duplicate namespace prefixes fail during decorator registration."""
    monkeypatch.setattr(namespace_registry, "_NAMESPACE_REGISTRY", {})
    monkeypatch.setattr(namespace_registry, "_DISCOVERED", True)

    def create_first_provider(context: NamespaceContext) -> NamespaceProvider:
        """Create a first placeholder namespace provider.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured namespace provider.
        """
        return NamespaceProvider(f"First {context.settings.large_language_model}")

    def create_second_provider(context: NamespaceContext) -> NamespaceProvider:
        """Create a second placeholder namespace provider.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured namespace provider.
        """
        return NamespaceProvider(f"Second {context.settings.large_language_model}")

    namespace_registry.register_namespace("example")(create_first_provider)

    with pytest.raises(ValueError, match="already registered"):
        namespace_registry.register_namespace("example")(create_second_provider)


async def test_custom_namespace_registry(settings: Settings) -> None:
    """Verify callers can mount custom namespace registries."""

    def create_example_provider(context: NamespaceContext) -> NamespaceProvider:
        """Create an example namespace provider for test composition.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured namespace provider.
        """
        provider = NamespaceProvider("Example")

        @provider.tool()
        def configured_model() -> str:
            """Return the configured large model name.

            Returns:
                The large language model from test settings.
            """
            return context.settings.large_language_model

        return provider

    async with Client(
        create_mcp(
            settings,
            namespaces=(
                Namespace(
                    "example",
                    create_example_provider,
                    description="Example namespace.",
                    tags=frozenset({"example", "readonly"}),
                ),
            ),
        )
    ) as custom_client:
        tools = await custom_client.list_tools()
        result = await custom_client.call_tool("example_configured_model", {})

    assert {tool.name for tool in tools} == {"example_configured_model"}
    assert result.content[0].text == "large-model"


async def test_health_ping(client: Client) -> None:
    """Verify the health ping tool returns a simple liveness response."""
    result = await client.call_tool("health_ping", {})

    assert result.structured_content == {"status": "ok", "message": "pong"}


async def test_runtime_config_does_not_expose_secret(client: Client) -> None:
    """Verify public runtime config omits raw secret values."""
    result = await client.call_tool("health_runtime_config", {})
    data = result.structured_content
    assert data is not None

    assert data["model_provider"] == {
        "provider": "openai",
        "configured": True,
        "auth_mode": "api_key",
        "large_language_model": "large-model",
        "small_language_model": "small-model",
        "embedding_model": "embedding-model",
    }
    assert data["openai"] == {
        "has_api_key": True,
        "large_language_model": "large-model",
        "small_language_model": "small-model",
        "embedding_model": "embedding-model",
    }
    assert data["azure_openai"] == {
        "auth_mode": "azure_identity",
        "configured": False,
        "endpoint_configured": False,
        "api_version": None,
        "api_version_configured": False,
        "token_scope": "https://cognitiveservices.azure.com/.default",
        "deployments_configured": False,
        "large_language_model_deployment": None,
        "small_language_model_deployment": None,
        "embedding_model_deployment": None,
    }
    assert data["azure_identity"] == {
        "service_principal_configured": False,
        "tenant_id_configured": False,
        "client_id_configured": False,
        "client_secret_configured": False,
    }
    assert data["health"] == {"enabled": True}
    assert data["auth"]["enabled"] is False
    assert data["database"] == {
        "provider": "oracle",
        "oracle_preferred": True,
        "sqlalchemy_enforced": True,
        "sqlalchemy_configured": False,
        "sqlalchemy_url_configured": False,
        "oracle_configured": False,
        "oracle_dsn_configured": False,
        "oracle_user_configured": False,
        "oracle_pool_min": 1,
        "oracle_pool_max": 4,
    }
    assert data["mongodb"] == {
        "configured": False,
        "connection_string_configured": False,
        "database_configured": False,
        "collections": {
            "cache": "cache",
            "chat_history": "chat_history",
            "documents": "documents",
            "semantic_cache": "semantic_cache",
        },
        "vector_search_configured": False,
        "vector_search_index": "vector_index",
    }
    assert data["authorization"]["tag_scopes"]["write"] == ["write"]
    assert data != {
        "openai": {
            "has_api_key": True,
            "large_language_model": "large-model",
            "small_language_model": "small-model",
            "embedding_model": "embedding-model",
        },
        "health": {"enabled": True},
    }
