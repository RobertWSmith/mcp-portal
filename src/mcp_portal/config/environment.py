"""Parse environment variables into typed configuration models."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from mcp_portal.config.constants import (
    DEFAULT_AZURE_OPENAI_TOKEN_SCOPE,
    DEFAULT_MONGODB_VECTOR_INDEX,
    DEFAULT_TAG_SCOPE_RULES,
    PROJECT_ROOT,
    AuthProviderName,
    DatabaseProviderName,
    EnvironmentVariable,
    ModelProviderName,
)
from mcp_portal.config.models import (
    AuthorizationSettings,
    AuthSettings,
    AzureIdentitySettings,
    AzureOpenAISettings,
    DatabaseSettings,
    EnterpriseSettings,
    HttpSettings,
    MiddlewareSettings,
    MongoDBSettings,
    NamespaceDiscoverySettings,
    ObservabilitySettings,
    OpenAISettings,
)


def _openai_settings_from_env() -> OpenAISettings:
    """Build direct OpenAI settings from the environment.

    Returns:
        Direct OpenAI provider settings.
    """
    return OpenAISettings(
        api_key=_optional_env(EnvironmentVariable.OPENAI_API_KEY),
        large_language_model=os.getenv(EnvironmentVariable.OPENAI_LARGE_LANGUAGE_MODEL, "gpt-5.5"),
        small_language_model=os.getenv(
            EnvironmentVariable.OPENAI_SMALL_LANGUAGE_MODEL, "gpt-5.5-mini"
        ),
        embedding_model=os.getenv(
            EnvironmentVariable.OPENAI_EMBEDDING_MODEL, "text-embedding-3-large"
        ),
    )


def _azure_openai_settings_from_env() -> AzureOpenAISettings:
    """Build Azure OpenAI settings from the environment.

    Returns:
        Azure OpenAI provider settings.
    """
    return AzureOpenAISettings(
        endpoint=_optional_env(EnvironmentVariable.AZURE_OPENAI_ENDPOINT),
        api_version=_optional_env(EnvironmentVariable.AZURE_OPENAI_API_VERSION),
        token_scope=(
            _optional_env(EnvironmentVariable.AZURE_OPENAI_TOKEN_SCOPE)
            or DEFAULT_AZURE_OPENAI_TOKEN_SCOPE
        ),
        large_language_model_deployment=_optional_env(
            EnvironmentVariable.AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT
        ),
        small_language_model_deployment=_optional_env(
            EnvironmentVariable.AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT
        ),
        embedding_model_deployment=_optional_env(
            EnvironmentVariable.AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT
        ),
    )


def _azure_identity_settings_from_env() -> AzureIdentitySettings:
    """Build Azure identity settings from the environment.

    Returns:
        Azure identity settings.
    """
    return AzureIdentitySettings(
        tenant_id=_optional_env(EnvironmentVariable.AZURE_TENANT_ID),
        client_id=_optional_env(EnvironmentVariable.AZURE_CLIENT_ID),
        client_secret=_optional_env(EnvironmentVariable.AZURE_CLIENT_SECRET),
    )


def _auth_settings_from_env() -> AuthSettings:
    """Build authentication settings from the environment.

    Returns:
        Portal authentication settings.
    """
    return AuthSettings(
        provider=_auth_provider_env(EnvironmentVariable.MCP_PORTAL_AUTH_PROVIDER, default="none"),
        required_linux_groups=_csv_env(EnvironmentVariable.MCP_PORTAL_AUTH_REQUIRED_LINUX_GROUPS),
        required_scopes=_csv_env(EnvironmentVariable.MCP_PORTAL_AUTH_REQUIRED_SCOPES),
        static_token=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_STATIC_TOKEN),
        static_client_id=os.getenv(
            EnvironmentVariable.MCP_PORTAL_AUTH_STATIC_CLIENT_ID, "mcp-portal-static"
        ),
        static_scopes=_csv_env(EnvironmentVariable.MCP_PORTAL_AUTH_STATIC_SCOPES),
        jwt_public_key=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_JWT_PUBLIC_KEY),
        jwt_jwks_uri=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_JWT_JWKS_URI),
        jwt_issuer=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_JWT_ISSUER),
        jwt_audience=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_JWT_AUDIENCE),
        jwt_algorithm=os.getenv(EnvironmentVariable.MCP_PORTAL_AUTH_JWT_ALGORITHM, "RS256"),
        resource_server_url=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_RESOURCE_SERVER_URL),
        ldap_uri=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_URI),
        ldap_base_dn=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_BASE_DN),
        ldap_user_dn_template=_optional_env(
            EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE
        ),
        ldap_search_filter=(
            _optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER)
            or "(uid={username})"
        ),
        ldap_bind_dn=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_BIND_DN),
        ldap_bind_password=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD),
        ldap_start_tls=_bool_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_START_TLS, default=False),
        ldap_ca_cert_file=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_CA_CERT_FILE),
        ldap_connect_timeout=_float_env(
            EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_CONNECT_TIMEOUT, default=5.0
        ),
        ldap_scopes=_csv_env(EnvironmentVariable.MCP_PORTAL_AUTH_LDAP_SCOPES),
        kerberos_hostname=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_KERBEROS_HOSTNAME),
        kerberos_service=(
            _optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_KERBEROS_SERVICE) or "HTTP"
        ),
        kerberos_keytab=_optional_env(EnvironmentVariable.MCP_PORTAL_AUTH_KERBEROS_KEYTAB),
        kerberos_scopes=_csv_env(EnvironmentVariable.MCP_PORTAL_AUTH_KERBEROS_SCOPES),
    )


def _authorization_settings_from_env() -> AuthorizationSettings:
    """Build authorization settings from the environment.

    Returns:
        Portal authorization settings.
    """
    return AuthorizationSettings(
        tag_scopes=_tag_scope_env(
            EnvironmentVariable.MCP_PORTAL_AUTHZ_TAG_SCOPES,
            default=DEFAULT_TAG_SCOPE_RULES,
        ),
        namespace_scopes=_tag_scope_env(
            EnvironmentVariable.MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES,
            default={},
        ),
        namespace_linux_groups=_tag_scope_env(
            EnvironmentVariable.MCP_PORTAL_AUTHZ_NAMESPACE_LINUX_GROUPS,
            default={},
        ),
    )


def _middleware_settings_from_env() -> MiddlewareSettings:
    """Build middleware settings from the environment.

    Returns:
        Portal middleware settings.
    """
    return MiddlewareSettings(
        enabled=_bool_env(EnvironmentVariable.MCP_PORTAL_MIDDLEWARE_ENABLED, default=False),
        structured_logging=_bool_env(
            EnvironmentVariable.MCP_PORTAL_STRUCTURED_LOGGING, default=True
        ),
        include_payload_length=_bool_env(
            EnvironmentVariable.MCP_PORTAL_LOG_PAYLOAD_LENGTHS, default=True
        ),
        rate_limit_per_second=_float_env(
            EnvironmentVariable.MCP_PORTAL_RATE_LIMIT_PER_SECOND, default=25.0
        ),
        rate_limit_burst=_int_env(EnvironmentVariable.MCP_PORTAL_RATE_LIMIT_BURST, default=50),
        response_max_bytes=_int_env(
            EnvironmentVariable.MCP_PORTAL_RESPONSE_MAX_BYTES, default=1_000_000
        ),
    )


def _http_settings_from_env() -> HttpSettings:
    """Build HTTP transport settings from the environment.

    Returns:
        Portal HTTP settings.
    """
    return HttpSettings(
        path=os.getenv(EnvironmentVariable.MCP_PORTAL_HTTP_PATH, "/mcp"),
        health_path=os.getenv(EnvironmentVariable.MCP_PORTAL_HEALTH_PATH, "/healthz"),
        readiness_path=os.getenv(EnvironmentVariable.MCP_PORTAL_READINESS_PATH, "/readyz"),
        json_response=_optional_bool_env(EnvironmentVariable.MCP_PORTAL_JSON_RESPONSE),
        stateless=_optional_bool_env(EnvironmentVariable.MCP_PORTAL_STATELESS_HTTP),
    )


def _enterprise_settings_from_env() -> EnterpriseSettings:
    """Build enterprise execution settings from the environment.

    Returns:
        Portal enterprise settings.
    """
    return EnterpriseSettings(
        require_auth=_bool_env(
            EnvironmentVariable.MCP_PORTAL_PRODUCTION_REQUIRE_AUTH, default=False
        ),
        multi_instance=_bool_env(EnvironmentVariable.MCP_PORTAL_MULTI_INSTANCE, default=False),
        require_tenant=_bool_env(EnvironmentVariable.MCP_PORTAL_REQUIRE_TENANT, default=False),
        tenant_claim=(_optional_env(EnvironmentVariable.MCP_PORTAL_TENANT_CLAIM) or "tenant_id"),
        audit_enabled=_bool_env(EnvironmentVariable.MCP_PORTAL_AUDIT_ENABLED, default=True),
        tool_timeout_seconds=_float_env(
            EnvironmentVariable.MCP_PORTAL_TOOL_TIMEOUT_SECONDS, default=45.0
        ),
        tool_timeout_overrides=_number_map_env(
            EnvironmentVariable.MCP_PORTAL_TOOL_TIMEOUT_OVERRIDES, parser=float
        ),
        max_concurrent_requests=_int_env(
            EnvironmentVariable.MCP_PORTAL_MAX_CONCURRENT_REQUESTS, default=100
        ),
        tool_concurrency_limits=_number_map_env(
            EnvironmentVariable.MCP_PORTAL_TOOL_CONCURRENCY_LIMITS, parser=int
        ),
        downstream_timeout_seconds=_float_env(
            EnvironmentVariable.MCP_PORTAL_DOWNSTREAM_TIMEOUT_SECONDS, default=45.0
        ),
        circuit_breaker_failure_threshold=_int_env(
            EnvironmentVariable.MCP_PORTAL_CIRCUIT_BREAKER_FAILURE_THRESHOLD, default=5
        ),
        circuit_breaker_recovery_seconds=_float_env(
            EnvironmentVariable.MCP_PORTAL_CIRCUIT_BREAKER_RECOVERY_SECONDS, default=30.0
        ),
        task_max_ttl_seconds=_int_env(
            EnvironmentVariable.MCP_PORTAL_TASK_MAX_TTL_SECONDS, default=3600
        ),
        task_max_concurrent_per_subject=_int_env(
            EnvironmentVariable.MCP_PORTAL_TASK_MAX_CONCURRENT_PER_SUBJECT, default=10
        ),
        egress_allowed_hosts=_csv_env(EnvironmentVariable.MCP_PORTAL_EGRESS_ALLOWED_HOSTS),
        namespace_allowlist=_csv_env(EnvironmentVariable.MCP_PORTAL_NAMESPACE_ALLOWLIST),
    )


def _namespace_discovery_settings_from_env() -> NamespaceDiscoverySettings:
    """Build namespace-discovery settings from the environment.

    Returns:
        Namespace discovery settings.
    """
    return NamespaceDiscoverySettings(
        strict=_bool_env(EnvironmentVariable.MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT, default=False)
    )


def _observability_settings_from_env() -> ObservabilitySettings:
    """Build observability settings from the environment.

    Returns:
        Portal observability settings.
    """
    return ObservabilitySettings(
        service_name=os.getenv(EnvironmentVariable.OTEL_SERVICE_NAME, "mcp-portal"),
        otlp_endpoint=_optional_env(EnvironmentVariable.OTEL_EXPORTER_OTLP_ENDPOINT),
        metrics_enabled=_bool_env(EnvironmentVariable.MCP_PORTAL_METRICS_ENABLED, default=True),
        cost_accounting_enabled=_bool_env(
            EnvironmentVariable.MCP_PORTAL_COST_ACCOUNTING_ENABLED, default=True
        ),
        include_tenant_metrics=_bool_env(
            EnvironmentVariable.MCP_PORTAL_METRICS_INCLUDE_TENANT, default=False
        ),
        cost_currency=(
            _optional_env(EnvironmentVariable.MCP_PORTAL_COST_CURRENCY) or "USD"
        ).upper(),
        pricing_version=_optional_env(EnvironmentVariable.MCP_PORTAL_PRICING_VERSION),
    )


def _database_settings_from_env() -> DatabaseSettings:
    """Build relational database settings from the environment.

    Returns:
        Portal relational database settings.
    """
    return DatabaseSettings(
        provider=_database_provider_env(
            EnvironmentVariable.MCP_PORTAL_DATABASE_PROVIDER, default="oracle"
        ),
        sqlalchemy_url=_optional_env(EnvironmentVariable.MCP_PORTAL_DATABASE_SQLALCHEMY_URL),
        oracle_dsn=_optional_env(EnvironmentVariable.MCP_PORTAL_ORACLE_DSN),
        oracle_user=_optional_env(EnvironmentVariable.MCP_PORTAL_ORACLE_USER),
        oracle_password=_optional_env(EnvironmentVariable.MCP_PORTAL_ORACLE_PASSWORD),
        oracle_pool_min=_int_env(EnvironmentVariable.MCP_PORTAL_ORACLE_POOL_MIN, default=1),
        oracle_pool_max=_int_env(EnvironmentVariable.MCP_PORTAL_ORACLE_POOL_MAX, default=4),
    )


def _mongodb_settings_from_env() -> MongoDBSettings:
    """Build MongoDB connector settings from the environment.

    Returns:
        Portal MongoDB settings.
    """
    return MongoDBSettings(
        connection_string=_optional_env(EnvironmentVariable.MCP_PORTAL_MONGODB_CONNECTION_STRING),
        database_name=_optional_env(EnvironmentVariable.MCP_PORTAL_MONGODB_DATABASE),
        vector_search_index=(
            _optional_env(EnvironmentVariable.MCP_PORTAL_MONGODB_VECTOR_SEARCH_INDEX)
            or DEFAULT_MONGODB_VECTOR_INDEX
        ),
    )


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

    normalized = value.lower().strip()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    return None


def _bool_env(name: str, *, default: bool) -> bool:
    """Read an optional boolean environment variable.

    Args:
        name: Environment variable name to read.
        default: Value returned when the environment variable is unset or blank.

    Returns:
        Parsed boolean value.
    """
    value = _optional_bool_env(name)
    if value is None:
        return default
    return value


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


def _number_map_env(
    name: str,
    *,
    parser: Callable[[str], float | int],
) -> dict[str, float | int]:
    """Read semicolon-separated ``name=value`` numeric overrides.

    Invalid entries make the complete optional override map empty so deployments do
    not silently apply only part of an intended policy.

    Args:
        name: Environment variable name to read.
        parser: Numeric parser such as ``int`` or ``float``.

    Returns:
        Parsed name-to-number overrides, or an empty mapping when invalid.
    """
    value = _optional_env(name)
    if value is None:
        return {}

    result: dict[str, float | int] = {}
    try:
        for raw_entry in value.split(";"):
            entry = raw_entry.strip()
            if not entry or "=" not in entry:
                return {}
            key, raw_number = entry.split("=", maxsplit=1)
            key = key.strip()
            if not key:
                return {}
            result[key] = parser(raw_number.strip())
    except ValueError:
        return {}
    return result


def _auth_provider_env(name: str, *, default: AuthProviderName) -> AuthProviderName:
    """Read an authentication provider name.

    Args:
        name: Environment variable name to read.
        default: Provider returned when the value is absent or unsupported.

    Returns:
        A supported authentication provider name.
    """
    value = (_optional_env(name) or default).lower()
    if value in {"ldap+kerberos", "kerberos+ldap"}:
        return "ldap_kerberos"
    if value in {"none", "static", "jwt", "ldap", "kerberos", "ldap_kerberos"}:
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


def _model_provider_env(name: str, *, default: ModelProviderName) -> ModelProviderName:
    """Read a model provider name.

    Args:
        name: Environment variable name to read.
        default: Provider returned when the value is absent or unsupported.

    Returns:
        A supported model provider name.
    """
    value = (_optional_env(name) or default).lower().replace("-", "_")
    if value in {"openai", "azure_openai"}:
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
