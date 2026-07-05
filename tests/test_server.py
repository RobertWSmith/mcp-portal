from __future__ import annotations

import json

import pytest
from fastmcp import Client, FastMCP

import mcp_portal.namespaces as namespace_registry
from mcp_portal.config import Settings
from mcp_portal.debug_ui import _runtime_snapshot_text, create_debug_app
from mcp_portal.namespaces import Namespace, NamespaceContext, build_namespace_runtimes
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


async def test_debug_ui_tool_is_exposed(client: Client) -> None:
    """Verify the FastMCP Apps debug dashboard is available to dev tools."""
    tools = await client.list_tools()
    tool_names = {tool.name for tool in tools}

    assert "portal_debug" in tool_names


async def test_debug_ui_provider_renders_dashboard(settings: Settings) -> None:
    """Verify the debug provider builds its snapshot tool and Prefab UI."""
    runtimes = build_namespace_runtimes(namespace_registry.iter_namespaces(), settings)
    debug_app = create_debug_app(settings, runtimes)
    app_tools = {tool.name: tool for tool in await debug_app._list_tools()}

    snapshot_result = await app_tools["debug_snapshot"].run({})
    dashboard_result = await app_tools["portal_debug"].run({})

    assert set(app_tools) == {"debug_snapshot", "portal_debug"}
    assert "large-model" in snapshot_result.content[0].text
    assert "Health Namespace" in snapshot_result.content[0].text
    assert dashboard_result.structured_content is not None
    assert dashboard_result.structured_content["state"] == {
        "snapshot_text": _runtime_snapshot_text(settings, runtimes)
    }


async def test_debug_ui_marks_missing_api_key() -> None:
    """Verify the dashboard handles missing OpenAI credentials."""
    settings = create_test_settings(openai_api_key=None)
    debug_app = create_debug_app(settings)
    app_tools = {tool.name: tool for tool in await debug_app._list_tools()}

    dashboard_result = await app_tools["portal_debug"].run({})

    assert dashboard_result.structured_content is not None
    assert "API key missing" in json.dumps(dashboard_result.structured_content)


def test_namespace_registration_decorator_records_factory(monkeypatch) -> None:
    """Verify namespace factories can register themselves with a decorator."""
    monkeypatch.setattr(namespace_registry, "_NAMESPACE_REGISTRY", {})
    monkeypatch.setattr(namespace_registry, "_DISCOVERED", True)

    def create_example_server(context: NamespaceContext) -> FastMCP:
        """Create a placeholder namespace server.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured FastMCP child server.
        """
        return FastMCP(f"Example {context.settings.openai_large_language_model}")

    decorated = namespace_registry.register_namespace(
        "example",
        description="Example namespace.",
        tags={"example", "test"},
    )(create_example_server)

    assert decorated is create_example_server
    assert namespace_registry.iter_namespaces() == (
        Namespace(
            "example",
            create_example_server,
            description="Example namespace.",
            tags=frozenset({"example", "test"}),
        ),
    )


def test_namespace_registration_rejects_duplicate_names(monkeypatch) -> None:
    """Verify duplicate namespace prefixes fail during decorator registration."""
    monkeypatch.setattr(namespace_registry, "_NAMESPACE_REGISTRY", {})
    monkeypatch.setattr(namespace_registry, "_DISCOVERED", True)

    def create_first_server(context: NamespaceContext) -> FastMCP:
        """Create a first placeholder namespace server.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured FastMCP child server.
        """
        return FastMCP(f"First {context.settings.openai_large_language_model}")

    def create_second_server(context: NamespaceContext) -> FastMCP:
        """Create a second placeholder namespace server.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured FastMCP child server.
        """
        return FastMCP(f"Second {context.settings.openai_large_language_model}")

    namespace_registry.register_namespace("example")(create_first_server)

    with pytest.raises(ValueError, match="already registered"):
        namespace_registry.register_namespace("example")(create_second_server)


async def test_custom_namespace_registry(settings: Settings) -> None:
    """Verify callers can mount custom namespace registries."""

    def create_example_server(context: NamespaceContext) -> FastMCP:
        """Create an example namespace server for test composition.

        Args:
            context: Runtime services shared with the namespace.

        Returns:
            A configured FastMCP child server.
        """
        server = FastMCP("Example")

        @server.tool
        def configured_model() -> str:
            """Return the configured large model name.

            Returns:
                The large language model from test settings.
            """
            return context.settings.openai_large_language_model

        return server

    async with Client(
        create_mcp(
            settings,
            namespaces=(
                Namespace(
                    "example",
                    create_example_server,
                    description="Example namespace.",
                    tags=frozenset({"example", "readonly"}),
                ),
            ),
            include_debug_ui=False,
        )
    ) as custom_client:
        tools = await custom_client.list_tools()
        result = await custom_client.call_tool("example_configured_model", {})

    assert {tool.name for tool in tools} == {"example_configured_model"}
    assert result.data == "large-model"


async def test_health_ping(client: Client) -> None:
    """Verify the health ping tool returns a simple liveness response."""
    result = await client.call_tool("health_ping", {})

    assert result.data == "pong"


async def test_runtime_config_does_not_expose_secret(client: Client) -> None:
    """Verify public runtime config omits the raw API key value."""
    result = await client.call_tool("health_runtime_config", {})

    assert result.data == {
        "openai": {
            "has_api_key": True,
            "large_language_model": "large-model",
            "small_language_model": "small-model",
            "embedding_model": "embedding-model",
        },
        "health": {"enabled": True},
    }
