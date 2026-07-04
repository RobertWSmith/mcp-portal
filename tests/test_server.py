from __future__ import annotations

import pytest
from fastmcp import Client

from mcp_portal.config import Settings
from mcp_portal.namespaces import Namespace
from mcp_portal.server import create_mcp


@pytest.fixture
def settings() -> Settings:
    """Create deterministic settings for server tests.

    Returns:
        Settings with placeholder test model values.
    """
    return Settings(
        openai_api_key="test-key",
        openai_large_language_model="large-model",
        openai_small_language_model="small-model",
        openai_embedding_model="embedding-model",
    )


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


async def test_custom_namespace_registry(settings: Settings) -> None:
    """Verify callers can mount custom namespace registries."""
    from fastmcp import FastMCP

    def create_example_server(settings: Settings) -> FastMCP:
        """Create an example namespace server for test composition.

        Args:
            settings: Runtime settings shared by namespace servers.

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
            return settings.openai_large_language_model

        return server

    async with Client(
        create_mcp(settings, namespaces=(Namespace("example", create_example_server),))
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
        "has_openai_api_key": True,
        "openai_large_language_model": "large-model",
        "openai_small_language_model": "small-model",
        "openai_embedding_model": "embedding-model",
    }
