"""Immutable models for MCP Portal configuration domains."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from mcp_portal.config.constants import (
    AuthProviderName,
    DatabaseProviderName,
    DEFAULT_AZURE_OPENAI_TOKEN_SCOPE,
    DEFAULT_MONGODB_COLLECTIONS,
    DEFAULT_MONGODB_VECTOR_INDEX,
    DEFAULT_TAG_SCOPE_RULES,
    MongoDBCollectionName,
    OPENAI_API_KEY_PLACEHOLDER,
)


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
        multi_instance: Whether deployment runs more than one portal process.
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
    multi_instance: bool = False
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
            "multi_instance": self.multi_instance,
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
