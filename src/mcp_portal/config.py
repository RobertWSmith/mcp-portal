from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AuthProviderName = Literal["none", "static", "jwt", "ldap", "kerberos", "ldap_kerberos"]
DatabaseProviderName = Literal["none", "oracle", "sqlalchemy"]
ModelProviderName = Literal["openai", "azure_openai"]
MongoDBCollectionName = Literal[
    "documents",
    "chat_history",
    "cache",
    "semantic_cache",
]
DEFAULT_MONGODB_COLLECTIONS: dict[MongoDBCollectionName, str] = {
    "documents": "documents",
    "chat_history": "chat_history",
    "cache": "cache",
    "semantic_cache": "semantic_cache",
}
DEFAULT_MONGODB_VECTOR_INDEX = "vector_index"
DEFAULT_TAG_SCOPE_RULES: dict[str, tuple[str, ...]] = {
    "admin": ("admin",),
    "destructive": ("admin",),
    "external": ("external",),
    "write": ("write",),
}
OPENAI_API_KEY_PLACEHOLDER = "your-api-key"
DEFAULT_AZURE_OPENAI_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class EnvironmentVariable(StrEnum):
    """Environment variables consumed by :meth:`Settings.from_env`."""

    AZURE_CLIENT_ID = "AZURE_CLIENT_ID"
    AZURE_CLIENT_SECRET = "AZURE_CLIENT_SECRET"
    AZURE_OPENAI_API_VERSION = "AZURE_OPENAI_API_VERSION"
    AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT = "AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT"
    AZURE_OPENAI_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
    AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT = "AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT"
    AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT = "AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT"
    AZURE_OPENAI_TOKEN_SCOPE = "AZURE_OPENAI_TOKEN_SCOPE"
    AZURE_TENANT_ID = "AZURE_TENANT_ID"
    MCP_PORTAL_AUDIT_ENABLED = "MCP_PORTAL_AUDIT_ENABLED"
    MCP_PORTAL_AUTH_JWT_ALGORITHM = "MCP_PORTAL_AUTH_JWT_ALGORITHM"
    MCP_PORTAL_AUTH_JWT_AUDIENCE = "MCP_PORTAL_AUTH_JWT_AUDIENCE"
    MCP_PORTAL_AUTH_JWT_ISSUER = "MCP_PORTAL_AUTH_JWT_ISSUER"
    MCP_PORTAL_AUTH_JWT_JWKS_URI = "MCP_PORTAL_AUTH_JWT_JWKS_URI"
    MCP_PORTAL_AUTH_JWT_PUBLIC_KEY = "MCP_PORTAL_AUTH_JWT_PUBLIC_KEY"
    MCP_PORTAL_AUTH_KERBEROS_HOSTNAME = "MCP_PORTAL_AUTH_KERBEROS_HOSTNAME"
    MCP_PORTAL_AUTH_KERBEROS_KEYTAB = "MCP_PORTAL_AUTH_KERBEROS_KEYTAB"
    MCP_PORTAL_AUTH_KERBEROS_SCOPES = "MCP_PORTAL_AUTH_KERBEROS_SCOPES"
    MCP_PORTAL_AUTH_KERBEROS_SERVICE = "MCP_PORTAL_AUTH_KERBEROS_SERVICE"
    MCP_PORTAL_AUTH_LDAP_BASE_DN = "MCP_PORTAL_AUTH_LDAP_BASE_DN"
    MCP_PORTAL_AUTH_LDAP_BIND_DN = "MCP_PORTAL_AUTH_LDAP_BIND_DN"
    MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD = "MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD"
    MCP_PORTAL_AUTH_LDAP_CA_CERT_FILE = "MCP_PORTAL_AUTH_LDAP_CA_CERT_FILE"
    MCP_PORTAL_AUTH_LDAP_CONNECT_TIMEOUT = "MCP_PORTAL_AUTH_LDAP_CONNECT_TIMEOUT"
    MCP_PORTAL_AUTH_LDAP_SCOPES = "MCP_PORTAL_AUTH_LDAP_SCOPES"
    MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER = "MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER"
    MCP_PORTAL_AUTH_LDAP_START_TLS = "MCP_PORTAL_AUTH_LDAP_START_TLS"
    MCP_PORTAL_AUTH_LDAP_URI = "MCP_PORTAL_AUTH_LDAP_URI"
    MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE = "MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE"
    MCP_PORTAL_AUTH_PROVIDER = "MCP_PORTAL_AUTH_PROVIDER"
    MCP_PORTAL_AUTH_REQUIRED_SCOPES = "MCP_PORTAL_AUTH_REQUIRED_SCOPES"
    MCP_PORTAL_AUTH_RESOURCE_SERVER_URL = "MCP_PORTAL_AUTH_RESOURCE_SERVER_URL"
    MCP_PORTAL_AUTH_STATIC_CLIENT_ID = "MCP_PORTAL_AUTH_STATIC_CLIENT_ID"
    MCP_PORTAL_AUTH_STATIC_SCOPES = "MCP_PORTAL_AUTH_STATIC_SCOPES"
    MCP_PORTAL_AUTH_STATIC_TOKEN = "MCP_PORTAL_AUTH_STATIC_TOKEN"
    MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES = "MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES"
    MCP_PORTAL_AUTHZ_TAG_SCOPES = "MCP_PORTAL_AUTHZ_TAG_SCOPES"
    MCP_PORTAL_CIRCUIT_BREAKER_FAILURE_THRESHOLD = "MCP_PORTAL_CIRCUIT_BREAKER_FAILURE_THRESHOLD"
    MCP_PORTAL_CIRCUIT_BREAKER_RECOVERY_SECONDS = "MCP_PORTAL_CIRCUIT_BREAKER_RECOVERY_SECONDS"
    MCP_PORTAL_COST_ACCOUNTING_ENABLED = "MCP_PORTAL_COST_ACCOUNTING_ENABLED"
    MCP_PORTAL_COST_CURRENCY = "MCP_PORTAL_COST_CURRENCY"
    MCP_PORTAL_DATABASE_PROVIDER = "MCP_PORTAL_DATABASE_PROVIDER"
    MCP_PORTAL_DATABASE_SQLALCHEMY_URL = "MCP_PORTAL_DATABASE_SQLALCHEMY_URL"
    MCP_PORTAL_DOWNSTREAM_TIMEOUT_SECONDS = "MCP_PORTAL_DOWNSTREAM_TIMEOUT_SECONDS"
    MCP_PORTAL_EGRESS_ALLOWED_HOSTS = "MCP_PORTAL_EGRESS_ALLOWED_HOSTS"
    MCP_PORTAL_HEALTH_ENABLED = "MCP_PORTAL_HEALTH_ENABLED"
    MCP_PORTAL_HEALTH_PATH = "MCP_PORTAL_HEALTH_PATH"
    MCP_PORTAL_HTTP_PATH = "MCP_PORTAL_HTTP_PATH"
    MCP_PORTAL_JSON_RESPONSE = "MCP_PORTAL_JSON_RESPONSE"
    MCP_PORTAL_LOG_PAYLOAD_LENGTHS = "MCP_PORTAL_LOG_PAYLOAD_LENGTHS"
    MCP_PORTAL_MAX_CONCURRENT_REQUESTS = "MCP_PORTAL_MAX_CONCURRENT_REQUESTS"
    MCP_PORTAL_METRICS_ENABLED = "MCP_PORTAL_METRICS_ENABLED"
    MCP_PORTAL_METRICS_INCLUDE_TENANT = "MCP_PORTAL_METRICS_INCLUDE_TENANT"
    MCP_PORTAL_MIDDLEWARE_ENABLED = "MCP_PORTAL_MIDDLEWARE_ENABLED"
    MCP_PORTAL_MODEL_PROVIDER = "MCP_PORTAL_MODEL_PROVIDER"
    MCP_PORTAL_MONGODB_CONNECTION_STRING = "MCP_PORTAL_MONGODB_CONNECTION_STRING"
    MCP_PORTAL_MONGODB_DATABASE = "MCP_PORTAL_MONGODB_DATABASE"
    MCP_PORTAL_MONGODB_VECTOR_SEARCH_INDEX = "MCP_PORTAL_MONGODB_VECTOR_SEARCH_INDEX"
    MCP_PORTAL_NAMESPACE_ALLOWLIST = "MCP_PORTAL_NAMESPACE_ALLOWLIST"
    MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT = "MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT"
    MCP_PORTAL_ORACLE_DSN = "MCP_PORTAL_ORACLE_DSN"
    MCP_PORTAL_ORACLE_PASSWORD = "MCP_PORTAL_ORACLE_PASSWORD"
    MCP_PORTAL_ORACLE_POOL_MAX = "MCP_PORTAL_ORACLE_POOL_MAX"
    MCP_PORTAL_ORACLE_POOL_MIN = "MCP_PORTAL_ORACLE_POOL_MIN"
    MCP_PORTAL_ORACLE_USER = "MCP_PORTAL_ORACLE_USER"
    MCP_PORTAL_PRICING_VERSION = "MCP_PORTAL_PRICING_VERSION"
    MCP_PORTAL_PRODUCTION_REQUIRE_AUTH = "MCP_PORTAL_PRODUCTION_REQUIRE_AUTH"
    MCP_PORTAL_RATE_LIMIT_BURST = "MCP_PORTAL_RATE_LIMIT_BURST"
    MCP_PORTAL_RATE_LIMIT_PER_SECOND = "MCP_PORTAL_RATE_LIMIT_PER_SECOND"
    MCP_PORTAL_READINESS_PATH = "MCP_PORTAL_READINESS_PATH"
    MCP_PORTAL_REQUIRE_TENANT = "MCP_PORTAL_REQUIRE_TENANT"
    MCP_PORTAL_RESPONSE_MAX_BYTES = "MCP_PORTAL_RESPONSE_MAX_BYTES"
    MCP_PORTAL_STATELESS_HTTP = "MCP_PORTAL_STATELESS_HTTP"
    MCP_PORTAL_STRUCTURED_LOGGING = "MCP_PORTAL_STRUCTURED_LOGGING"
    MCP_PORTAL_TASK_MAX_CONCURRENT_PER_SUBJECT = "MCP_PORTAL_TASK_MAX_CONCURRENT_PER_SUBJECT"
    MCP_PORTAL_TASK_MAX_TTL_SECONDS = "MCP_PORTAL_TASK_MAX_TTL_SECONDS"
    MCP_PORTAL_TENANT_CLAIM = "MCP_PORTAL_TENANT_CLAIM"
    MCP_PORTAL_TOOL_CONCURRENCY_LIMITS = "MCP_PORTAL_TOOL_CONCURRENCY_LIMITS"
    MCP_PORTAL_TOOL_TIMEOUT_OVERRIDES = "MCP_PORTAL_TOOL_TIMEOUT_OVERRIDES"
    MCP_PORTAL_TOOL_TIMEOUT_SECONDS = "MCP_PORTAL_TOOL_TIMEOUT_SECONDS"
    OPENAI_API_KEY = "OPENAI_API_KEY"
    OPENAI_EMBEDDING_MODEL = "OPENAI_EMBEDDING_MODEL"
    OPENAI_LARGE_LANGUAGE_MODEL = "OPENAI_LARGE_LANGUAGE_MODEL"
    OPENAI_SMALL_LANGUAGE_MODEL = "OPENAI_SMALL_LANGUAGE_MODEL"
    OTEL_EXPORTER_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
    OTEL_SERVICE_NAME = "OTEL_SERVICE_NAME"


ENVIRONMENT_VARIABLE_NAMES = frozenset(variable.value for variable in EnvironmentVariable)


@dataclass(frozen=True)
class OpenAISettings:
    """OpenAI platform runtime settings.

    Attributes:
        api_key: Optional OpenAI platform API key.
        large_language_model: OpenAI model name for larger language-model tasks.
        small_language_model: OpenAI model name for smaller language-model tasks.
        embedding_model: OpenAI model name for embedding tasks.
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
        return bool(self.api_key and self.api_key != OPENAI_API_KEY_PLACEHOLDER)

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
class AzureOpenAISettings:
    """Azure OpenAI runtime settings.

    Attributes:
        endpoint: Azure OpenAI resource endpoint.
        api_version: Azure OpenAI API version used by SDK clients.
        token_scope: Azure resource scope requested from Azure Identity credentials.
        large_language_model_deployment: Deployment name for larger language-model tasks.
        small_language_model_deployment: Deployment name for smaller language-model tasks.
        embedding_model_deployment: Deployment name for embedding tasks.
    """

    endpoint: str | None = None
    api_version: str | None = None
    token_scope: str = DEFAULT_AZURE_OPENAI_TOKEN_SCOPE
    large_language_model_deployment: str | None = None
    small_language_model_deployment: str | None = None
    embedding_model_deployment: str | None = None

    @property
    def deployments_configured(self) -> bool:
        """Report whether all model-role deployment names are configured.

        Returns:
            True when large, small, and embedding deployment names are set.
        """
        return bool(
            self.large_language_model_deployment
            and self.small_language_model_deployment
            and self.embedding_model_deployment
        )

    @property
    def configured(self) -> bool:
        """Report whether Azure OpenAI has enough metadata for model calls.

        Returns:
            True when endpoint, API version, token scope, and deployment names are set.
        """
        return bool(
            self.endpoint and self.api_version and self.token_scope and self.deployments_configured
        )

    def public_snapshot(self) -> dict[str, object]:
        """Return Azure OpenAI settings safe to expose through development tools.

        Returns:
            Public Azure OpenAI metadata with secrets and endpoint values omitted.
        """
        return {
            "auth_mode": "azure_identity",
            "configured": self.configured,
            "endpoint_configured": self.endpoint is not None,
            "api_version": self.api_version,
            "api_version_configured": self.api_version is not None,
            "token_scope": self.token_scope,
            "deployments_configured": self.deployments_configured,
            "large_language_model_deployment": self.large_language_model_deployment,
            "small_language_model_deployment": self.small_language_model_deployment,
            "embedding_model_deployment": self.embedding_model_deployment,
        }


@dataclass(frozen=True)
class AzureIdentitySettings:
    """Azure Identity environment settings used by Azure SDK credentials.

    Attributes:
        tenant_id: Optional Azure tenant id for service-principal auth.
        client_id: Optional Azure client/application id for service-principal auth.
        client_secret: Optional Azure client secret for service-principal auth.
    """

    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None

    @property
    def service_principal_configured(self) -> bool:
        """Report whether service-principal environment credentials are complete.

        Returns:
            True when tenant id, client id, and client secret are configured.
        """
        return bool(self.tenant_id and self.client_id and self.client_secret)

    def public_snapshot(self) -> dict[str, object]:
        """Return Azure Identity settings safe to expose publicly.

        Returns:
            Public Azure Identity metadata with secrets omitted.
        """
        return {
            "service_principal_configured": self.service_principal_configured,
            "tenant_id_configured": self.tenant_id is not None,
            "client_id_configured": self.client_id is not None,
            "client_secret_configured": self.client_secret is not None,
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
        ldap_uri: LDAP or LDAPS server URI.
        ldap_base_dn: Directory search base used to resolve usernames.
        ldap_user_dn_template: Optional direct user DN template containing ``{username}``.
        ldap_search_filter: LDAP search filter containing ``{username}``.
        ldap_bind_dn: Optional service-account DN used for directory searches.
        ldap_bind_password: Optional service-account password used for directory searches.
        ldap_start_tls: Whether to upgrade an LDAP connection with StartTLS.
        ldap_ca_cert_file: Optional CA bundle used to verify the directory certificate.
        ldap_connect_timeout: LDAP network and operation timeout in seconds.
        ldap_scopes: Scopes granted to LDAP-authenticated principals.
        kerberos_hostname: Hostname portion of the HTTP service principal.
        kerberos_service: Service portion of the HTTP service principal.
        kerberos_keytab: Optional keytab path used by the Kerberos acceptor.
        kerberos_scopes: Scopes granted to Kerberos-authenticated principals.
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
    resource_server_url: str | None = None
    ldap_uri: str | None = None
    ldap_base_dn: str | None = None
    ldap_user_dn_template: str | None = None
    ldap_search_filter: str = "(uid={username})"
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_start_tls: bool = False
    ldap_ca_cert_file: str | None = None
    ldap_connect_timeout: float = 5.0
    ldap_scopes: tuple[str, ...] = ()
    kerberos_hostname: str | None = None
    kerberos_service: str = "HTTP"
    kerberos_keytab: str | None = None
    kerberos_scopes: tuple[str, ...] = ()

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
            "resource_server_url_configured": self.resource_server_url is not None,
            "ldap_uri_configured": self.ldap_uri is not None,
            "ldap_base_dn_configured": self.ldap_base_dn is not None,
            "ldap_user_dn_template_configured": self.ldap_user_dn_template is not None,
            "ldap_bind_dn_configured": self.ldap_bind_dn is not None,
            "ldap_bind_password_configured": self.ldap_bind_password is not None,
            "ldap_start_tls": self.ldap_start_tls,
            "ldap_ca_cert_file_configured": self.ldap_ca_cert_file is not None,
            "ldap_scopes": list(self.ldap_scopes),
            "kerberos_hostname_configured": self.kerberos_hostname is not None,
            "kerberos_service": self.kerberos_service,
            "kerberos_keytab_configured": self.kerberos_keytab is not None,
            "kerberos_scopes": list(self.kerberos_scopes),
        }


@dataclass(frozen=True)
class AuthorizationSettings:
    """Authorization policy mapped from tags and namespaces to required scopes.

    Attributes:
        tag_scopes: Mapping of FastMCP component tags to required OAuth scopes.
        namespace_scopes: Deployment-level scopes required to discover or use a namespace.
    """

    tag_scopes: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_TAG_SCOPE_RULES)
    )
    namespace_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """Report whether tag-based authorization rules exist.

        Returns:
            True when at least one tag-to-scope rule is configured.
        """
        return bool(self.tag_scopes or self.namespace_scopes)

    def public_snapshot(self) -> dict[str, object]:
        """Return authorization policy safe to expose through development tools.

        Returns:
            Public tag-to-scope authorization metadata.
        """
        return {
            "enabled": self.enabled,
            "tag_scopes": {tag: list(scopes) for tag, scopes in sorted(self.tag_scopes.items())},
            "namespace_scopes": {
                namespace: list(scopes)
                for namespace, scopes in sorted(self.namespace_scopes.items())
            },
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
        health_path: Unauthenticated liveness endpoint path.
        readiness_path: Unauthenticated dependency-readiness endpoint path.
        json_response: Optional FastMCP JSON response mode.
        stateless: Optional FastMCP stateless HTTP mode.
    """

    path: str = "/mcp"
    health_path: str = "/healthz"
    readiness_path: str = "/readyz"
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
            "readiness_path": self.readiness_path,
            "json_response": self.json_response,
            "stateless": self.stateless,
        }


@dataclass(frozen=True)
class EnterpriseSettings:
    """Cross-cutting enterprise control-plane settings.

    These settings deliberately describe policy boundaries rather than individual
    integrations so namespaces can remain portable.

    Attributes:
        require_auth: Whether hardened production startup requires authentication.
        tenant_claim: Verified token claim used for tenant partitioning.
        audit_enabled: Whether request lifecycle audit events are emitted.
        tool_timeout_seconds: Default maximum tool execution time.
        tool_timeout_overrides: Fully-qualified tool-specific deadline overrides.
        max_concurrent_requests: Maximum in-process concurrent tool calls.
        tool_concurrency_limits: Fully-qualified per-tool concurrency limits.
        downstream_timeout_seconds: Default deadline for downstream operations.
        circuit_breaker_failure_threshold: Consecutive failures that open a circuit.
        circuit_breaker_recovery_seconds: Cooldown before a half-open probe.
        task_max_ttl_seconds: Maximum task result retention period.
        task_max_concurrent_per_subject: Maximum working tasks for one owner.
        egress_allowed_hosts: Approved outbound DNS hostnames.
    """

    require_auth: bool = False
    require_tenant: bool = False
    tenant_claim: str = "tenant_id"
    audit_enabled: bool = True
    tool_timeout_seconds: float = 45.0
    tool_timeout_overrides: dict[str, float] = field(default_factory=dict)
    max_concurrent_requests: int = 100
    tool_concurrency_limits: dict[str, int] = field(default_factory=dict)
    downstream_timeout_seconds: float = 45.0
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_seconds: float = 30.0
    task_max_ttl_seconds: int = 3600
    task_max_concurrent_per_subject: int = 10
    egress_allowed_hosts: tuple[str, ...] = ()
    namespace_allowlist: tuple[str, ...] = ()

    def public_snapshot(self) -> dict[str, object]:
        """Return non-secret enterprise posture metadata.

        Returns:
            Enterprise posture metadata without secret values.
        """
        return {
            "require_auth": self.require_auth,
            "require_tenant": self.require_tenant,
            "tenant_claim": self.tenant_claim,
            "audit_enabled": self.audit_enabled,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "tool_timeout_overrides": dict(sorted(self.tool_timeout_overrides.items())),
            "max_concurrent_requests": self.max_concurrent_requests,
            "tool_concurrency_limits": dict(sorted(self.tool_concurrency_limits.items())),
            "downstream_timeout_seconds": self.downstream_timeout_seconds,
            "circuit_breaker_failure_threshold": self.circuit_breaker_failure_threshold,
            "circuit_breaker_recovery_seconds": self.circuit_breaker_recovery_seconds,
            "task_max_ttl_seconds": self.task_max_ttl_seconds,
            "task_max_concurrent_per_subject": self.task_max_concurrent_per_subject,
            "egress_allowlist_configured": bool(self.egress_allowed_hosts),
            "namespace_allowlist": list(self.namespace_allowlist),
        }

    def tool_timeout(self, name: str, meta: Mapping[str, object] | None = None) -> float:
        """Resolve a tool deadline using deployment, tool, then global precedence.

        Args:
            name: Fully-qualified tool name.
            meta: Optional governed tool metadata.

        Returns:
            Positive execution deadline in seconds.
        """
        if name in self.tool_timeout_overrides:
            return self.tool_timeout_overrides[name]
        metadata_value = (meta or {}).get("timeout_seconds")
        if isinstance(metadata_value, (int, float)) and not isinstance(metadata_value, bool):
            return float(metadata_value)
        return self.tool_timeout_seconds

    def tool_concurrency(self, name: str, meta: Mapping[str, object] | None = None) -> int:
        """Resolve a per-tool concurrency limit using deployment override precedence.

        Args:
            name: Fully-qualified tool name.
            meta: Optional governed tool metadata.

        Returns:
            Positive maximum concurrent invocation count.
        """
        if name in self.tool_concurrency_limits:
            return self.tool_concurrency_limits[name]
        metadata_value = (meta or {}).get("max_concurrency")
        if isinstance(metadata_value, int) and not isinstance(metadata_value, bool):
            return metadata_value
        return self.max_concurrent_requests


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
        metrics_enabled: Whether runtime metrics are emitted.
        cost_accounting_enabled: Whether detailed usage records are emitted.
        include_tenant_metrics: Whether tenant ID may be used as a metric dimension.
        cost_currency: Default currency for namespace usage records.
        pricing_version: Optional pricing table or contract version.
    """

    service_name: str = "mcp-portal"
    otlp_endpoint: str | None = None
    metrics_enabled: bool = True
    cost_accounting_enabled: bool = True
    include_tenant_metrics: bool = False
    cost_currency: str = "USD"
    pricing_version: str | None = None

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
            "metrics_enabled": self.metrics_enabled,
            "cost_accounting_enabled": self.cost_accounting_enabled,
            "include_tenant_metrics": self.include_tenant_metrics,
            "cost_currency": self.cost_currency,
            "pricing_version": self.pricing_version,
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
class MongoDBSettings:
    """MongoDB connector settings for namespace integrations.

    Attributes:
        connection_string: Optional MongoDB connection URI.
        database_name: Optional default database for connector helpers.
        collections: Hard-coded collection aliases for connector helpers.
        vector_search_index: Default Atlas Vector Search index name.
    """

    connection_string: str | None = None
    database_name: str | None = None
    collections: Mapping[MongoDBCollectionName, str] = field(
        default_factory=lambda: dict(DEFAULT_MONGODB_COLLECTIONS)
    )
    vector_search_index: str = DEFAULT_MONGODB_VECTOR_INDEX

    @property
    def configured(self) -> bool:
        """Report whether MongoDB connectors can be registered.

        Returns:
            True when a MongoDB connection URI is configured.
        """
        return self.connection_string is not None

    def collection_name(self, collection: MongoDBCollectionName) -> str:
        """Return the hard-coded MongoDB collection name for an alias.

        Args:
            collection: Collection alias to resolve.

        Returns:
            The MongoDB collection name assigned to the alias.
        """
        return self.collections[collection]

    def namespace(self, collection: MongoDBCollectionName = "documents") -> str | None:
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
        """Return MongoDB settings safe to expose.

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
        openai: Settings for direct OpenAI platform calls.
        model_provider: Active model provider used by generic model settings.
        azure_openai: Settings for Azure OpenAI model calls.
        azure_identity: Azure Identity environment settings.
        health: Settings used by the health namespace.
        auth: Authentication settings used by HTTP production transports.
        authorization: Authorization policy applied by production middleware.
        middleware: Cross-cutting production middleware settings.
        http: HTTP and ASGI deployment settings.
        namespace_discovery: Namespace discovery behavior.
        observability: Observability export metadata.
        database: Preferred database backend settings.
        mongodb: MongoDB connector settings.
    """

    openai: OpenAISettings
    model_provider: ModelProviderName = "openai"
    azure_openai: AzureOpenAISettings = field(default_factory=AzureOpenAISettings)
    azure_identity: AzureIdentitySettings = field(default_factory=AzureIdentitySettings)
    health: HealthSettings = field(default_factory=HealthSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    authorization: AuthorizationSettings = field(default_factory=AuthorizationSettings)
    middleware: MiddlewareSettings = field(default_factory=MiddlewareSettings)
    http: HttpSettings = field(default_factory=HttpSettings)
    enterprise: EnterpriseSettings = field(default_factory=EnterpriseSettings)
    namespace_discovery: NamespaceDiscoverySettings = field(
        default_factory=NamespaceDiscoverySettings
    )
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    mongodb: MongoDBSettings = field(default_factory=MongoDBSettings)

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
            openai=_openai_settings_from_env(),
            model_provider=_model_provider_env(
                EnvironmentVariable.MCP_PORTAL_MODEL_PROVIDER, default="openai"
            ),
            azure_openai=_azure_openai_settings_from_env(),
            azure_identity=_azure_identity_settings_from_env(),
            health=HealthSettings(
                enabled=_bool_env(EnvironmentVariable.MCP_PORTAL_HEALTH_ENABLED, default=True),
            ),
            auth=_auth_settings_from_env(),
            authorization=_authorization_settings_from_env(),
            middleware=_middleware_settings_from_env(),
            http=_http_settings_from_env(),
            enterprise=_enterprise_settings_from_env(),
            namespace_discovery=_namespace_discovery_settings_from_env(),
            observability=_observability_settings_from_env(),
            database=_database_settings_from_env(),
            mongodb=_mongodb_settings_from_env(),
        )

    @property
    def openai_api_key(self) -> str | None:
        """Return the configured OpenAI platform API key.

        Returns:
            The optional OpenAI platform API key.
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
        """Report whether a non-placeholder OpenAI platform API key is configured.

        Returns:
            True when `OPENAI_API_KEY` is set to a non-placeholder value.
        """
        return self.openai.has_api_key

    @property
    def model_provider_configured(self) -> bool:
        """Report whether the active model provider has required settings.

        Returns:
            True when the selected provider has enough non-secret metadata for model calls.
        """
        if self.model_provider == "azure_openai":
            return self.azure_openai.configured

        return self.openai.has_api_key

    @property
    def large_language_model(self) -> str:
        """Return the active provider's large language model or deployment name.

        Returns:
            The configured large model identifier for the active model provider.
        """
        if (
            self.model_provider == "azure_openai"
            and self.azure_openai.large_language_model_deployment
        ):
            return self.azure_openai.large_language_model_deployment

        return self.openai.large_language_model

    @property
    def small_language_model(self) -> str:
        """Return the active provider's small language model or deployment name.

        Returns:
            The configured small model identifier for the active model provider.
        """
        if (
            self.model_provider == "azure_openai"
            and self.azure_openai.small_language_model_deployment
        ):
            return self.azure_openai.small_language_model_deployment

        return self.openai.small_language_model

    @property
    def embedding_model(self) -> str:
        """Return the active provider's embedding model or deployment name.

        Returns:
            The configured embedding model identifier for the active model provider.
        """
        if self.model_provider == "azure_openai" and self.azure_openai.embedding_model_deployment:
            return self.azure_openai.embedding_model_deployment

        return self.openai.embedding_model

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

    def validate_production(self) -> None:
        """Reject unsafe combinations when hardened production mode is requested."""
        from mcp_portal.errors import ConfigurationPortalError

        problems = [*self._production_auth_problems(), *self._production_runtime_problems()]
        if problems:
            raise ConfigurationPortalError(
                "Production configuration is unsafe.", details={"problems": problems}
            )

    def _production_auth_problems(self) -> list[str]:
        """Return unsafe production authentication settings.

        Returns:
            Human-readable authentication configuration problems.
        """
        problems: list[str] = []
        if self.enterprise.require_auth and not self.auth.enabled:
            problems.append("authentication is required but no provider is configured")
        if self.enterprise.require_tenant and not self.auth.enabled:
            problems.append("tenant isolation requires an authentication provider")

        if self.auth.provider == "jwt":
            if not self.auth.jwt_issuer:
                problems.append("JWT issuer is required")
            if not self.auth.jwt_audience:
                problems.append("JWT audience is required")
            if not self.auth.resource_server_url:
                problems.append("canonical resource server URL is required")

        if self.auth.resource_server_url:
            parsed = urlsplit(self.auth.resource_server_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                problems.append("resource server URL must be an absolute HTTP(S) URL")
            elif parsed.scheme != "https" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                problems.append("resource server URL must use HTTPS outside loopback")

        return problems

    def _production_runtime_problems(self) -> list[str]:
        """Return unsafe production execution-control settings.

        Returns:
            Human-readable runtime configuration problems.
        """
        problems: list[str] = []

        if self.enterprise.tool_timeout_seconds <= 0:
            problems.append("tool timeout must be positive")
        if any(value <= 0 for value in self.enterprise.tool_timeout_overrides.values()):
            problems.append("tool timeout overrides must be positive")
        if self.enterprise.max_concurrent_requests <= 0:
            problems.append("maximum concurrent requests must be positive")
        if any(value <= 0 for value in self.enterprise.tool_concurrency_limits.values()):
            problems.append("tool concurrency limits must be positive")
        if self.enterprise.downstream_timeout_seconds <= 0:
            problems.append("downstream timeout must be positive")
        if self.enterprise.circuit_breaker_failure_threshold <= 0:
            problems.append("circuit-breaker failure threshold must be positive")
        if self.enterprise.circuit_breaker_recovery_seconds <= 0:
            problems.append("circuit-breaker recovery time must be positive")

        return problems

    def public_snapshot(self) -> dict[str, dict[str, object]]:
        """Return non-secret settings safe to expose through development tools.

        Returns:
            Grouped public runtime settings.
        """
        return {
            "model_provider": {
                "provider": self.model_provider,
                "configured": self.model_provider_configured,
                "auth_mode": (
                    "azure_identity" if self.model_provider == "azure_openai" else "api_key"
                ),
                "large_language_model": self.large_language_model,
                "small_language_model": self.small_language_model,
                "embedding_model": self.embedding_model,
            },
            "openai": self.openai.public_snapshot(),
            "azure_openai": self.azure_openai.public_snapshot(),
            "azure_identity": self.azure_identity.public_snapshot(),
            "health": self.health.public_snapshot(),
            "auth": self.auth.public_snapshot(),
            "authorization": self.authorization.public_snapshot(),
            "middleware": self.middleware.public_snapshot(),
            "http": self.http.public_snapshot(),
            "enterprise": self.enterprise.public_snapshot(),
            "namespace_discovery": self.namespace_discovery.public_snapshot(),
            "observability": self.observability.public_snapshot(),
            "database": self.database.public_snapshot(),
            "mongodb": self.mongodb.public_snapshot(),
        }


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
