from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AuthProviderName = Literal["none", "static", "jwt"]
DatabaseProviderName = Literal["none", "oracle", "sqlalchemy"]
LangChainMongoDBCollectionName = Literal[
    "documents",
    "chat_history",
    "cache",
    "semantic_cache",
]
DEFAULT_LANGCHAIN_MONGODB_COLLECTIONS: dict[LangChainMongoDBCollectionName, str] = {
    "documents": "documents",
    "chat_history": "chat_history",
    "cache": "cache",
    "semantic_cache": "semantic_cache",
}
DEFAULT_LANGCHAIN_MONGODB_VECTOR_INDEX = "vector_index"
DEFAULT_TAG_SCOPE_RULES: dict[str, tuple[str, ...]] = {
    "admin": ("admin",),
    "destructive": ("admin",),
    "external": ("external",),
    "write": ("write",),
}


@dataclass(frozen=True)
class OpenAISettings:
    """OpenAI-related runtime settings.

    Attributes:
        api_key: Optional OpenAI API key used by namespaces that call OpenAI.
        large_language_model: Model name for larger language-model tasks.
        small_language_model: Model name for smaller language-model tasks.
        embedding_model: Model name for embedding tasks.
    """

    api_key: str | None
    large_language_model: str
    small_language_model: str
    embedding_model: str

    @property
    def has_api_key(self) -> bool:
        """Report whether a non-placeholder OpenAI API key is configured.

        Returns:
            True when `OPENAI_API_KEY` is set to a non-placeholder value.
        """
        return bool(self.api_key and self.api_key != "your-api-key")

    def public_snapshot(self) -> dict[str, str | bool]:
        """Return OpenAI settings safe to expose through development tools.

        Returns:
            Public model names and whether an API key is configured.
        """
        return {
            "has_api_key": self.has_api_key,
            "large_language_model": self.large_language_model,
            "small_language_model": self.small_language_model,
            "embedding_model": self.embedding_model,
        }


@dataclass(frozen=True)
class HealthSettings:
    """Health namespace runtime settings.

    Attributes:
        enabled: Whether the health namespace tools should be mounted.
    """

    enabled: bool = True

    def public_snapshot(self) -> dict[str, bool]:
        """Return health settings safe to expose through development tools.

        Returns:
            Public health namespace configuration.
        """
        return {"enabled": self.enabled}


@dataclass(frozen=True)
class AuthSettings:
    """Authentication settings for HTTP-based production transports.

    Attributes:
        provider: Authentication provider strategy.
        required_scopes: Scopes required on every accepted bearer token.
        static_token: Development-only static bearer token.
        static_client_id: Client id attached to the static token.
        static_scopes: Scopes attached to the static token.
        jwt_public_key: Static JWT verification key or shared secret.
        jwt_jwks_uri: Remote JWKS endpoint for JWT verification.
        jwt_issuer: Optional expected JWT issuer.
        jwt_audience: Optional expected JWT audience.
        jwt_algorithm: JWT signing algorithm to accept.
    """

    provider: AuthProviderName = "none"
    required_scopes: tuple[str, ...] = ()
    static_token: str | None = None
    static_client_id: str = "mcp-portal-static"
    static_scopes: tuple[str, ...] = ()
    jwt_public_key: str | None = None
    jwt_jwks_uri: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_algorithm: str = "RS256"

    @property
    def enabled(self) -> bool:
        """Report whether authentication is configured.

        Returns:
            True when a concrete authentication provider is selected.
        """
        return self.provider != "none"

    def public_snapshot(self) -> dict[str, object]:
        """Return authentication settings safe to expose through development tools.

        Returns:
            Public authentication metadata with secret values omitted.
        """
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "required_scopes": list(self.required_scopes),
            "static_token_configured": self.static_token is not None,
            "static_scopes": list(self.static_scopes),
            "jwt_public_key_configured": self.jwt_public_key is not None,
            "jwt_jwks_uri_configured": self.jwt_jwks_uri is not None,
            "jwt_issuer_configured": self.jwt_issuer is not None,
            "jwt_audience_configured": self.jwt_audience is not None,
            "jwt_algorithm": self.jwt_algorithm,
        }


@dataclass(frozen=True)
class AuthorizationSettings:
    """Authorization policy mapped from component tags to required scopes.

    Attributes:
        tag_scopes: Mapping of FastMCP component tags to required OAuth scopes.
    """

    tag_scopes: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_TAG_SCOPE_RULES)
    )

    @property
    def enabled(self) -> bool:
        """Report whether tag-based authorization rules exist.

        Returns:
            True when at least one tag-to-scope rule is configured.
        """
        return bool(self.tag_scopes)

    def public_snapshot(self) -> dict[str, object]:
        """Return authorization policy safe to expose through development tools.

        Returns:
            Public tag-to-scope authorization metadata.
        """
        return {
            "enabled": self.enabled,
            "tag_scopes": {tag: list(scopes) for tag, scopes in sorted(self.tag_scopes.items())},
        }


@dataclass(frozen=True)
class MiddlewareSettings:
    """Production middleware settings.

    Attributes:
        enabled: Whether production middleware should be attached automatically.
        structured_logging: Whether request logs should be emitted as JSON.
        include_payload_length: Whether request payload lengths should be logged.
        rate_limit_per_second: Sustained request rate allowed by the token bucket.
        rate_limit_burst: Maximum burst capacity allowed by the token bucket.
        response_max_bytes: Maximum serialized tool response size.
    """

    enabled: bool = False
    structured_logging: bool = True
    include_payload_length: bool = True
    rate_limit_per_second: float = 25.0
    rate_limit_burst: int = 50
    response_max_bytes: int = 1_000_000

    def public_snapshot(self) -> dict[str, object]:
        """Return middleware settings safe to expose through development tools.

        Returns:
            Public production middleware metadata.
        """
        return {
            "enabled": self.enabled,
            "structured_logging": self.structured_logging,
            "include_payload_length": self.include_payload_length,
            "rate_limit_per_second": self.rate_limit_per_second,
            "rate_limit_burst": self.rate_limit_burst,
            "response_max_bytes": self.response_max_bytes,
        }


@dataclass(frozen=True)
class HttpSettings:
    """HTTP and ASGI deployment settings.

    Attributes:
        path: MCP endpoint path for HTTP-based transports.
        health_path: Unauthenticated operational health endpoint path.
        json_response: Optional FastMCP JSON response mode.
        stateless: Optional FastMCP stateless HTTP mode.
    """

    path: str = "/mcp"
    health_path: str = "/healthz"
    json_response: bool | None = None
    stateless: bool | None = None

    def public_snapshot(self) -> dict[str, object]:
        """Return HTTP settings safe to expose through development tools.

        Returns:
            Public HTTP deployment metadata.
        """
        return {
            "path": self.path,
            "health_path": self.health_path,
            "json_response": self.json_response,
            "stateless": self.stateless,
        }


@dataclass(frozen=True)
class NamespaceDiscoverySettings:
    """Namespace discovery settings.

    Attributes:
        strict: Whether namespace import failures should stop server startup.
    """

    strict: bool = False

    def public_snapshot(self) -> dict[str, bool]:
        """Return namespace discovery settings safe to expose.

        Returns:
            Public namespace discovery metadata.
        """
        return {"strict": self.strict}


@dataclass(frozen=True)
class ObservabilitySettings:
    """Observability settings for production deployment.

    Attributes:
        service_name: Service name used by OpenTelemetry launchers.
        otlp_endpoint: Optional OTLP collector endpoint.
    """

    service_name: str = "mcp-portal"
    otlp_endpoint: str | None = None

    @property
    def enabled(self) -> bool:
        """Report whether outbound telemetry export is configured.

        Returns:
            True when an OTLP endpoint is configured.
        """
        return self.otlp_endpoint is not None

    def public_snapshot(self) -> dict[str, object]:
        """Return observability settings safe to expose.

        Returns:
            Public observability metadata.
        """
        return {
            "enabled": self.enabled,
            "service_name": self.service_name,
            "otlp_endpoint_configured": self.otlp_endpoint is not None,
        }


@dataclass(frozen=True)
class DatabaseSettings:
    """Database backend settings for namespace integrations.

    Attributes:
        provider: Preferred database provider for portal backends.
        sqlalchemy_url: Optional SQLAlchemy database URL for portable engines.
        oracle_dsn: Oracle database DSN.
        oracle_user: Oracle database username.
        oracle_password: Oracle database password.
        oracle_pool_min: SQLAlchemy pool size for Oracle engines.
        oracle_pool_max: Maximum Oracle checked-out connections including overflow.
    """

    provider: DatabaseProviderName = "oracle"
    sqlalchemy_url: str | None = None
    oracle_dsn: str | None = None
    oracle_user: str | None = None
    oracle_password: str | None = None
    oracle_pool_min: int = 1
    oracle_pool_max: int = 4

    @property
    def oracle_configured(self) -> bool:
        """Report whether Oracle connection settings are complete.

        Returns:
            True when the Oracle provider has DSN, user, and password values.
        """
        return bool(self.oracle_dsn and self.oracle_user and self.oracle_password)

    @property
    def sqlalchemy_configured(self) -> bool:
        """Report whether a SQLAlchemy engine can be created.

        Returns:
            True when either a SQLAlchemy URL or complete Oracle settings exist.
        """
        return self.provider != "none" and bool(
            self.sqlalchemy_url or (self.provider == "oracle" and self.oracle_configured)
        )

    def public_snapshot(self) -> dict[str, object]:
        """Return database settings safe to expose through development tools.

        Returns:
            Public database backend metadata with secrets omitted.
        """
        return {
            "provider": self.provider,
            "oracle_preferred": self.provider == "oracle",
            "sqlalchemy_enforced": True,
            "sqlalchemy_configured": self.sqlalchemy_configured,
            "sqlalchemy_url_configured": self.sqlalchemy_url is not None,
            "oracle_configured": self.oracle_configured,
            "oracle_dsn_configured": self.oracle_dsn is not None,
            "oracle_user_configured": self.oracle_user is not None,
            "oracle_pool_min": self.oracle_pool_min,
            "oracle_pool_max": self.oracle_pool_max,
        }


@dataclass(frozen=True)
class LangChainMongoDBSettings:
    """LangChain MongoDB connector settings for namespace integrations.

    Attributes:
        connection_string: Optional MongoDB connection URI.
        database_name: Optional default database for connector helpers.
        collections: Hard-coded collection aliases for connector helpers.
        vector_search_index: Default Atlas Vector Search index name.
    """

    connection_string: str | None = None
    database_name: str | None = None
    collections: Mapping[LangChainMongoDBCollectionName, str] = field(
        default_factory=lambda: dict(DEFAULT_LANGCHAIN_MONGODB_COLLECTIONS)
    )
    vector_search_index: str = DEFAULT_LANGCHAIN_MONGODB_VECTOR_INDEX

    @property
    def configured(self) -> bool:
        """Report whether LangChain MongoDB connectors can be registered.

        Returns:
            True when a MongoDB connection URI is configured.
        """
        return self.connection_string is not None

    def collection_name(self, collection: LangChainMongoDBCollectionName) -> str:
        """Return the hard-coded MongoDB collection name for an alias.

        Args:
            collection: Collection alias to resolve.

        Returns:
            The MongoDB collection name assigned to the alias.
        """
        return self.collections[collection]

    def namespace(self, collection: LangChainMongoDBCollectionName = "documents") -> str | None:
        """Return the configured MongoDB namespace for a hard-coded collection alias.

        Args:
            collection: Collection alias to resolve.

        Returns:
            A `database.collection` namespace, or None when incomplete.
        """
        if self.database_name is None:
            return None
        return f"{self.database_name}.{self.collection_name(collection)}"

    @property
    def vector_search_configured(self) -> bool:
        """Report whether the default vector-search helper has enough metadata.

        Returns:
            True when the connection string and default namespace are configured.
        """
        return self.configured and self.namespace("documents") is not None

    def public_snapshot(self) -> dict[str, object]:
        """Return LangChain MongoDB settings safe to expose.

        Returns:
            Public connector metadata with the MongoDB URI omitted.
        """
        return {
            "configured": self.configured,
            "connection_string_configured": self.connection_string is not None,
            "database_configured": self.database_name is not None,
            "collections": dict(sorted(self.collections.items())),
            "vector_search_configured": self.vector_search_configured,
            "vector_search_index": self.vector_search_index,
        }


@dataclass(frozen=True)
class Settings:
    """Runtime settings grouped by namespace or provider boundary.

    Attributes:
        openai: Settings used by OpenAI-backed namespaces.
        health: Settings used by the health namespace.
        auth: Authentication settings used by HTTP production transports.
        authorization: Authorization policy applied by production middleware.
        middleware: Cross-cutting production middleware settings.
        http: HTTP and ASGI deployment settings.
        namespace_discovery: Namespace discovery behavior.
        observability: Observability export metadata.
        database: Preferred database backend settings.
        langchain_mongodb: LangChain MongoDB connector settings.
    """

    openai: OpenAISettings
    health: HealthSettings = field(default_factory=HealthSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    authorization: AuthorizationSettings = field(default_factory=AuthorizationSettings)
    middleware: MiddlewareSettings = field(default_factory=MiddlewareSettings)
    http: HttpSettings = field(default_factory=HttpSettings)
    namespace_discovery: NamespaceDiscoverySettings = field(
        default_factory=NamespaceDiscoverySettings
    )
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    langchain_mongodb: LangChainMongoDBSettings = field(default_factory=LangChainMongoDBSettings)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, override: bool = False) -> "Settings":
        """Build settings from environment variables and an optional `.env` file.

        Args:
            env_file: Optional path to a dotenv file. When omitted, `.env` is resolved from
                the current working directory, then the project root.
            override: Whether dotenv values should override existing environment values.

        Returns:
            Settings populated from the existing environment-variable contract.
        """
        load_dotenv(_resolve_env_file(env_file), override=override)

        return cls(
            openai=OpenAISettings(
                api_key=_optional_env("OPENAI_API_KEY"),
                large_language_model=os.getenv("OPENAI_LARGE_LANGUAGE_MODEL", "gpt-5.5"),
                small_language_model=os.getenv("OPENAI_SMALL_LANGUAGE_MODEL", "gpt-5.5-mini"),
                embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
            ),
            health=HealthSettings(
                enabled=_bool_env("MCP_PORTAL_HEALTH_ENABLED", default=True),
            ),
            auth=AuthSettings(
                provider=_auth_provider_env("MCP_PORTAL_AUTH_PROVIDER", default="none"),
                required_scopes=_csv_env("MCP_PORTAL_AUTH_REQUIRED_SCOPES"),
                static_token=_optional_env("MCP_PORTAL_AUTH_STATIC_TOKEN"),
                static_client_id=os.getenv("MCP_PORTAL_AUTH_STATIC_CLIENT_ID", "mcp-portal-static"),
                static_scopes=_csv_env("MCP_PORTAL_AUTH_STATIC_SCOPES"),
                jwt_public_key=_optional_env("MCP_PORTAL_AUTH_JWT_PUBLIC_KEY"),
                jwt_jwks_uri=_optional_env("MCP_PORTAL_AUTH_JWT_JWKS_URI"),
                jwt_issuer=_optional_env("MCP_PORTAL_AUTH_JWT_ISSUER"),
                jwt_audience=_optional_env("MCP_PORTAL_AUTH_JWT_AUDIENCE"),
                jwt_algorithm=os.getenv("MCP_PORTAL_AUTH_JWT_ALGORITHM", "RS256"),
            ),
            authorization=AuthorizationSettings(
                tag_scopes=_tag_scope_env(
                    "MCP_PORTAL_AUTHZ_TAG_SCOPES",
                    default=DEFAULT_TAG_SCOPE_RULES,
                ),
            ),
            middleware=MiddlewareSettings(
                enabled=_bool_env("MCP_PORTAL_MIDDLEWARE_ENABLED", default=False),
                structured_logging=_bool_env("MCP_PORTAL_STRUCTURED_LOGGING", default=True),
                include_payload_length=_bool_env("MCP_PORTAL_LOG_PAYLOAD_LENGTHS", default=True),
                rate_limit_per_second=_float_env("MCP_PORTAL_RATE_LIMIT_PER_SECOND", default=25.0),
                rate_limit_burst=_int_env("MCP_PORTAL_RATE_LIMIT_BURST", default=50),
                response_max_bytes=_int_env("MCP_PORTAL_RESPONSE_MAX_BYTES", default=1_000_000),
            ),
            http=HttpSettings(
                path=os.getenv("MCP_PORTAL_HTTP_PATH", "/mcp"),
                health_path=os.getenv("MCP_PORTAL_HEALTH_PATH", "/healthz"),
                json_response=_optional_bool_env("MCP_PORTAL_JSON_RESPONSE"),
                stateless=_optional_bool_env("MCP_PORTAL_STATELESS_HTTP"),
            ),
            namespace_discovery=NamespaceDiscoverySettings(
                strict=_bool_env("MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT", default=False),
            ),
            observability=ObservabilitySettings(
                service_name=os.getenv("OTEL_SERVICE_NAME", "mcp-portal"),
                otlp_endpoint=_optional_env("OTEL_EXPORTER_OTLP_ENDPOINT"),
            ),
            database=DatabaseSettings(
                provider=_database_provider_env("MCP_PORTAL_DATABASE_PROVIDER", default="oracle"),
                sqlalchemy_url=_optional_env("MCP_PORTAL_DATABASE_SQLALCHEMY_URL"),
                oracle_dsn=_optional_env("MCP_PORTAL_ORACLE_DSN"),
                oracle_user=_optional_env("MCP_PORTAL_ORACLE_USER"),
                oracle_password=_optional_env("MCP_PORTAL_ORACLE_PASSWORD"),
                oracle_pool_min=_int_env("MCP_PORTAL_ORACLE_POOL_MIN", default=1),
                oracle_pool_max=_int_env("MCP_PORTAL_ORACLE_POOL_MAX", default=4),
            ),
            langchain_mongodb=LangChainMongoDBSettings(
                connection_string=_optional_env("MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING"),
                database_name=_optional_env("MCP_PORTAL_LANGCHAIN_MONGODB_DATABASE"),
                vector_search_index=(
                    _optional_env("MCP_PORTAL_LANGCHAIN_MONGODB_VECTOR_SEARCH_INDEX")
                    or DEFAULT_LANGCHAIN_MONGODB_VECTOR_INDEX
                ),
            ),
        )

    @property
    def openai_api_key(self) -> str | None:
        """Return the configured OpenAI API key.

        Returns:
            The optional OpenAI API key.
        """
        return self.openai.api_key

    @property
    def openai_large_language_model(self) -> str:
        """Return the configured large language model.

        Returns:
            The model name for larger language-model tasks.
        """
        return self.openai.large_language_model

    @property
    def openai_small_language_model(self) -> str:
        """Return the configured small language model.

        Returns:
            The model name for smaller language-model tasks.
        """
        return self.openai.small_language_model

    @property
    def openai_embedding_model(self) -> str:
        """Return the configured embedding model.

        Returns:
            The model name for embedding tasks.
        """
        return self.openai.embedding_model

    @property
    def has_openai_api_key(self) -> bool:
        """Report whether a non-placeholder OpenAI API key is configured.

        Returns:
            True when `OPENAI_API_KEY` is set to a non-placeholder value.
        """
        return self.openai.has_api_key

    def namespace_enabled(self, name: str) -> bool:
        """Report whether a namespace should mount its tools.

        Args:
            name: Namespace prefix.

        Returns:
            True when tools for the namespace should be mounted.
        """
        if name == "health":
            return self.health.enabled
        return True

    def public_snapshot(self) -> dict[str, dict[str, object]]:
        """Return non-secret settings safe to expose through development tools.

        Returns:
            Grouped public runtime settings.
        """
        return {
            "openai": self.openai.public_snapshot(),
            "health": self.health.public_snapshot(),
            "auth": self.auth.public_snapshot(),
            "authorization": self.authorization.public_snapshot(),
            "middleware": self.middleware.public_snapshot(),
            "http": self.http.public_snapshot(),
            "namespace_discovery": self.namespace_discovery.public_snapshot(),
            "observability": self.observability.public_snapshot(),
            "database": self.database.public_snapshot(),
            "langchain_mongodb": self.langchain_mongodb.public_snapshot(),
        }


def _resolve_env_file(env_file: str | Path | None) -> Path:
    """Resolve the dotenv file path used for local development settings.

    Args:
        env_file: Optional path provided by the caller.

    Returns:
        The explicit dotenv path, current working directory `.env`, or project-root `.env`.
    """
    if env_file is not None:
        return Path(env_file)

    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env

    return PROJECT_ROOT / ".env"


def _optional_env(name: str) -> str | None:
    """Read an optional environment variable as a stripped non-empty string.

    Args:
        name: Environment variable name to read.

    Returns:
        The stripped value, or None when the variable is unset or blank.
    """
    value = os.getenv(name)
    if value is None:
        return None

    value = value.strip()
    return value or None


def _bool_env(name: str, *, default: bool) -> bool:
    """Read an optional boolean environment variable.

    Args:
        name: Environment variable name to read.
        default: Value returned when the environment variable is unset or blank.

    Returns:
        Parsed boolean value.
    """
    value = _optional_env(name)
    if value is None:
        return default

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    return default


def _optional_bool_env(name: str) -> bool | None:
    """Read an optional boolean environment variable.

    Args:
        name: Environment variable name to read.

    Returns:
        The parsed boolean value, or None when the variable is unset or invalid.
    """
    value = _optional_env(name)
    if value is None:
        return None

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    return None


def _int_env(name: str, *, default: int) -> int:
    """Read an optional integer environment variable.

    Args:
        name: Environment variable name to read.
        default: Value returned when parsing fails or the variable is absent.

    Returns:
        The parsed integer or the default.
    """
    value = _optional_env(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, *, default: float) -> float:
    """Read an optional floating-point environment variable.

    Args:
        name: Environment variable name to read.
        default: Value returned when parsing fails or the variable is absent.

    Returns:
        The parsed float or the default.
    """
    value = _optional_env(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def _csv_env(name: str) -> tuple[str, ...]:
    """Read a comma-or-space separated environment variable.

    Args:
        name: Environment variable name to read.

    Returns:
        A tuple of stripped non-empty values.
    """
    value = _optional_env(name)
    if value is None:
        return ()

    return tuple(part for part in value.replace(",", " ").split() if part)


def _auth_provider_env(name: str, *, default: AuthProviderName) -> AuthProviderName:
    """Read an authentication provider name.

    Args:
        name: Environment variable name to read.
        default: Provider returned when the value is absent or unsupported.

    Returns:
        A supported authentication provider name.
    """
    value = (_optional_env(name) or default).lower()
    if value in {"none", "static", "jwt"}:
        return value
    return default


def _database_provider_env(name: str, *, default: DatabaseProviderName) -> DatabaseProviderName:
    """Read a database provider name.

    Args:
        name: Environment variable name to read.
        default: Provider returned when the value is absent or unsupported.

    Returns:
        A supported database provider name.
    """
    value = (_optional_env(name) or default).lower()
    if value in {"none", "oracle", "sqlalchemy"}:
        return value
    return default


def _tag_scope_env(
    name: str,
    *,
    default: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """Read semicolon-separated tag authorization rules.

    Args:
        name: Environment variable name to read.
        default: Rules returned when the value is absent or invalid.

    Returns:
        Mapping of component tags to required scopes.
    """
    value = _optional_env(name)
    if value is None:
        return dict(default)

    rules: dict[str, tuple[str, ...]] = {}
    for raw_entry in value.split(";"):
        entry = raw_entry.strip()
        if not entry:
            continue

        separator = "=" if "=" in entry else ":"
        if separator not in entry:
            return dict(default)

        tag, raw_scopes = entry.split(separator, maxsplit=1)
        tag = tag.strip()
        scopes = tuple(scope for scope in raw_scopes.replace(",", " ").split() if scope)
        if not tag or not scopes:
            return dict(default)
        rules[tag] = scopes

    return rules or dict(default)
