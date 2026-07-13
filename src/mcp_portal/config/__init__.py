"""Load and validate MCP Portal settings from environment sources."""

# ruff: noqa: F401 - this module intentionally preserves the legacy import surface

from mcp_portal.config.constants import (
    AuthProviderName,
    DatabaseProviderName,
    DEFAULT_AZURE_OPENAI_TOKEN_SCOPE,
    DEFAULT_MONGODB_COLLECTIONS,
    DEFAULT_MONGODB_VECTOR_INDEX,
    DEFAULT_TAG_SCOPE_RULES,
    ENVIRONMENT_VARIABLE_NAMES,
    EnvironmentVariable,
    ModelProviderName,
    MongoDBCollectionName,
    OPENAI_API_KEY_PLACEHOLDER,
    PROJECT_ROOT,
)
from mcp_portal.config.environment import (
    _auth_provider_env,
    _bool_env,
    _csv_env,
    _database_provider_env,
    _float_env,
    _int_env,
    _model_provider_env,
    _number_map_env,
    _optional_bool_env,
    _optional_env,
    _resolve_env_file,
    _tag_scope_env,
)
from mcp_portal.config.models import (
    AuthSettings,
    AuthorizationSettings,
    AzureIdentitySettings,
    AzureOpenAISettings,
    DatabaseSettings,
    EnterpriseSettings,
    HealthSettings,
    HttpSettings,
    MiddlewareSettings,
    MongoDBSettings,
    NamespaceDiscoverySettings,
    ObservabilitySettings,
    OpenAISettings,
)
from mcp_portal.config.settings import Settings

__all__ = [name for name in globals() if not name.startswith("__")]
