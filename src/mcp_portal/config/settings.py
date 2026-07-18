"""Aggregate configuration and production validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from mcp_portal.config.constants import EnvironmentVariable, ModelProviderName
from mcp_portal.config.environment import (
    _auth_settings_from_env,
    _authorization_settings_from_env,
    _azure_identity_settings_from_env,
    _azure_openai_settings_from_env,
    _bool_env,
    _database_settings_from_env,
    _enterprise_settings_from_env,
    _http_settings_from_env,
    _middleware_settings_from_env,
    _model_provider_env,
    _mongodb_settings_from_env,
    _namespace_discovery_settings_from_env,
    _observability_settings_from_env,
    _openai_settings_from_env,
    _resolve_env_file,
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


def _production_url_problem(label: str, value: str) -> str | None:
    """Return a production-safety problem for an externally configured URL.

    Args:
        label: Human-readable configuration label.
        value: Configured URL value.

    Returns:
        Safety problem text, or None when the URL is safe.
    """
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return f"{label} must be an absolute HTTP(S) URL"
    if parsed.username or parsed.password or parsed.fragment:
        return f"{label} must not contain credentials or a fragment"
    if parsed.scheme != "https" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return f"{label} must use HTTPS outside loopback"
    return None


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

        if self.auth.provider in {"oauth", "jwt"}:
            problems.extend(self._production_jwt_problems())

        if self.auth.provider == "oauth":
            problems.extend(self._production_oauth_problems())

        if self.auth.provider == "static":
            problems.append("static bearer tokens are not allowed in production")

        problems.extend(self._production_auth_url_problems())
        return problems

    def _production_jwt_problems(self) -> list[str]:
        """Return verification problems shared by JWT and OAuth modes.

        Returns:
            Human-readable JWT verification problems.
        """
        problems: list[str] = []
        if not self.auth.jwt_issuer:
            problems.append("JWT issuer is required")
        if not self.auth.jwt_audience:
            problems.append("JWT audience is required")
        if not self.auth.resource_server_url:
            problems.append("canonical resource server URL is required")
        if self.auth.jwt_algorithm.startswith("HS"):
            problems.append("symmetric JWT algorithms are not allowed in production")
        if self.auth.jwt_clock_skew_seconds < 0:
            problems.append("JWT clock skew must be non-negative")
        return problems

    def _production_oauth_problems(self) -> list[str]:
        """Return OAuth discovery and audience-binding problems.

        Returns:
            Human-readable OAuth configuration problems.
        """
        problems: list[str] = []
        if not self.auth.authorization_server_url:
            problems.append("OAuth authorization server URL is required")
        if not self.auth.jwt_jwks_uri:
            problems.append("OAuth requires a JWKS URI for signing-key rotation")
        if self.auth.jwt_public_key:
            problems.append("OAuth does not allow a static JWT verification key")
        if (
            self.auth.jwt_audience
            and self.auth.resource_server_url
            and self.auth.jwt_audience != self.auth.resource_server_url
        ):
            problems.append("OAuth JWT audience must equal the canonical resource server URL")
        if self.auth.resource_server_url:
            resource_path = urlsplit(self.auth.resource_server_url).path.rstrip("/")
            expected_path = f"/{self.http.path.strip('/')}"
            if not resource_path.endswith(expected_path):
                problems.append("resource server URL must end in the configured MCP HTTP path")
        return problems

    def _production_auth_url_problems(self) -> list[str]:
        """Return safety problems for authentication URLs.

        Returns:
            Human-readable authentication URL problems.
        """
        configured_urls = (
            ("resource server URL", self.auth.resource_server_url),
            ("authorization server URL", self.auth.authorization_server_url),
            ("JWT issuer URL", self.auth.jwt_issuer),
            ("JWT JWKS URI", self.auth.jwt_jwks_uri),
        )
        problems: list[str] = []
        for label, value in configured_urls:
            if value:
                problem = _production_url_problem(label, value)
                if problem:
                    problems.append(problem)
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
        supported_classifications = {"public", "internal", "confidential", "restricted"}
        if any(
            classification not in supported_classifications
            for classification in self.enterprise.execution_remote_classifications
        ):
            problems.append("execution-cell remote classifications must be supported")

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
