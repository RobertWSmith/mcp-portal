"""Offer test settings, namespace contexts, and isolated MCP clients."""

from __future__ import annotations

from dataclasses import dataclass, replace

from fastmcp import Client

from mcp_portal.clients import default_client_factories
from mcp_portal.config import (
    AzureIdentitySettings,
    AzureOpenAISettings,
    DEFAULT_AZURE_OPENAI_TOKEN_SCOPE,
    HealthSettings,
    ModelProviderName,
    OpenAISettings,
    Settings,
)
from mcp_portal.namespaces import (
    Namespace,
    NamespaceContext,
    NamespaceDependencies,
    NamespaceProvider,
    build_namespace_context,
)
from mcp_portal.redaction import Redactor


@dataclass(frozen=True)
class SettingsOverrides:
    """Overrides for deterministic test settings.

    Attributes:
        model_provider: Active test model provider.
        openai_api_key: Optional test OpenAI API key.
        large_model: Test large language model name.
        small_model: Test small language model name.
        embedding_model: Test embedding model name.
        azure_openai_endpoint: Optional Azure OpenAI endpoint.
        azure_openai_api_version: Optional Azure OpenAI API version.
        azure_openai_token_scope: Optional Azure OpenAI token scope.
        azure_large_model_deployment: Optional Azure large model deployment.
        azure_small_model_deployment: Optional Azure small model deployment.
        azure_embedding_model_deployment: Optional Azure embedding deployment.
        azure_tenant_id: Optional Azure tenant id.
        azure_client_id: Optional Azure client id.
        azure_client_secret: Optional Azure client secret.
        health_enabled: Whether the health namespace mounts.
    """

    model_provider: ModelProviderName = "openai"
    openai_api_key: str | None = "test-key"
    large_model: str = "large-model"
    small_model: str = "small-model"
    embedding_model: str = "embedding-model"
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_token_scope: str | None = None
    azure_large_model_deployment: str | None = None
    azure_small_model_deployment: str | None = None
    azure_embedding_model_deployment: str | None = None
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    health_enabled: bool = True


def create_test_settings(overrides: SettingsOverrides | None = None) -> Settings:
    """Create deterministic settings for namespace tests.

    Args:
        overrides: Optional deterministic setting overrides.

    Returns:
        Settings populated with deterministic test values.
    """
    overrides = overrides or SettingsOverrides()
    return Settings(
        openai=OpenAISettings(
            api_key=overrides.openai_api_key,
            large_language_model=overrides.large_model,
            small_language_model=overrides.small_model,
            embedding_model=overrides.embedding_model,
        ),
        model_provider=overrides.model_provider,
        azure_openai=AzureOpenAISettings(
            endpoint=overrides.azure_openai_endpoint,
            api_version=overrides.azure_openai_api_version,
            token_scope=overrides.azure_openai_token_scope or DEFAULT_AZURE_OPENAI_TOKEN_SCOPE,
            large_language_model_deployment=overrides.azure_large_model_deployment,
            small_language_model_deployment=overrides.azure_small_model_deployment,
            embedding_model_deployment=overrides.azure_embedding_model_deployment,
        ),
        azure_identity=AzureIdentitySettings(
            tenant_id=overrides.azure_tenant_id,
            client_id=overrides.azure_client_id,
            client_secret=overrides.azure_client_secret,
        ),
        health=HealthSettings(enabled=overrides.health_enabled),
    )


def create_namespace_test_context(
    namespace_name: str = "test",
    *,
    settings: Settings | None = None,
    dependencies: NamespaceDependencies | None = None,
) -> NamespaceContext:
    """Create a namespace context for direct unit tests.

    Args:
        namespace_name: Namespace prefix to use in the test context.
        settings: Optional deterministic settings.
        dependencies: Optional namespace service overrides.

    Returns:
        A namespace context built through the production helper.
    """
    selected_settings = settings or create_test_settings()
    dependencies = dependencies or NamespaceDependencies()
    return build_namespace_context(
        Namespace(
            name=namespace_name,
            create=lambda context: NamespaceProvider(f"Test {context.name}"),
            description="Test namespace.",
            tags=frozenset({"test"}),
        ),
        selected_settings,
        replace(
            dependencies,
            clients=dependencies.clients or default_client_factories(),
            redactor=dependencies.redactor
            or Redactor.from_secrets(
                (
                    selected_settings.openai.api_key,
                    selected_settings.azure_identity.client_secret,
                    selected_settings.auth.static_token,
                    selected_settings.auth.jwt_public_key,
                    selected_settings.auth.ldap_bind_password,
                    selected_settings.mongodb.connection_string,
                )
            ),
            clock=dependencies.clock,
        ),
    )


def create_namespace_test_client(
    namespace: Namespace,
    *,
    settings: Settings | None = None,
) -> Client:
    """Create an in-memory FastMCP client for one namespace.

    Args:
        namespace: Namespace manifest to mount.
        settings: Optional deterministic settings.
    Returns:
        A FastMCP client ready for use as an async context manager.
    """
    from mcp_portal.server import create_mcp

    return Client(
        create_mcp(
            settings or create_test_settings(),
            namespaces=(namespace,),
        )
    )
