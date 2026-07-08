from __future__ import annotations

from fastmcp import Client
from mcp.server.fastmcp import FastMCP

from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import HealthSettings, OpenAISettings, Settings
from mcp_portal.namespaces import Clock, Namespace, NamespaceContext, build_namespace_context
from mcp_portal.redaction import Redactor


def create_test_settings(
    *,
    openai_api_key: str | None = "test-key",
    large_model: str = "large-model",
    small_model: str = "small-model",
    embedding_model: str = "embedding-model",
    health_enabled: bool = True,
) -> Settings:
    """Create deterministic settings for namespace tests.

    Args:
        openai_api_key: Optional test OpenAI API key.
        large_model: Test large language model name.
        small_model: Test small language model name.
        embedding_model: Test embedding model name.
        health_enabled: Whether the health namespace should mount in tests.

    Returns:
        Settings populated with deterministic test values.
    """
    return Settings(
        openai=OpenAISettings(
            api_key=openai_api_key,
            large_language_model=large_model,
            small_language_model=small_model,
            embedding_model=embedding_model,
        ),
        health=HealthSettings(enabled=health_enabled),
    )


def create_namespace_test_context(
    namespace_name: str = "test",
    *,
    settings: Settings | None = None,
    clients: ClientFactories | None = None,
    redactor: Redactor | None = None,
    clock: Clock | None = None,
) -> NamespaceContext:
    """Create a namespace context for direct unit tests.

    Args:
        namespace_name: Namespace prefix to use in the test context.
        settings: Optional deterministic settings.
        clients: Optional external client factory registry.
        redactor: Optional diagnostic redactor.
        clock: Optional test clock.

    Returns:
        A namespace context built through the production helper.
    """
    selected_settings = settings or create_test_settings()
    namespace = Namespace(
        name=namespace_name,
        create=_empty_namespace_server,
        description="Test namespace.",
        tags=frozenset({"test"}),
    )
    return build_namespace_context(
        namespace,
        selected_settings,
        clients=clients or default_client_factories(),
        redactor=redactor
        or Redactor.from_secrets(
            (
                selected_settings.openai.api_key,
                selected_settings.mongodb.connection_string,
            )
        ),
        clock=clock,
    )


def create_namespace_test_client(
    namespace: Namespace,
    *,
    settings: Settings | None = None,
    include_debug_ui: bool = False,
) -> Client:
    """Create an in-memory FastMCP client for one namespace.

    Args:
        namespace: Namespace manifest to mount.
        settings: Optional deterministic settings.
        include_debug_ui: Whether to include the central debug app.

    Returns:
        A FastMCP client ready for use as an async context manager.
    """
    from mcp_portal.server import create_mcp

    return Client(
        create_mcp(
            settings or create_test_settings(),
            namespaces=(namespace,),
            include_debug_ui=include_debug_ui,
        )
    )


def _empty_namespace_server(context: NamespaceContext) -> FastMCP:
    """Create an empty namespace server for test context construction.

    Args:
        context: Runtime services shared with the test namespace.

    Returns:
        An empty FastMCP server named for the test namespace.
    """
    return FastMCP(f"Test {context.name}")
