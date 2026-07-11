from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_portal.config import Settings, _bool_env, _optional_env, _resolve_env_file

PORTAL_ENV_NAMES = (
    "MCP_PORTAL_MODEL_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_LARGE_LANGUAGE_MODEL",
    "OPENAI_SMALL_LANGUAGE_MODEL",
    "OPENAI_EMBEDDING_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_TOKEN_SCOPE",
    "AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT",
    "AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT",
    "AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "MCP_PORTAL_HEALTH_ENABLED",
    "MCP_PORTAL_AUTH_PROVIDER",
    "MCP_PORTAL_AUTH_REQUIRED_SCOPES",
    "MCP_PORTAL_AUTH_STATIC_TOKEN",
    "MCP_PORTAL_AUTH_STATIC_CLIENT_ID",
    "MCP_PORTAL_AUTH_STATIC_SCOPES",
    "MCP_PORTAL_AUTH_JWT_PUBLIC_KEY",
    "MCP_PORTAL_AUTH_JWT_JWKS_URI",
    "MCP_PORTAL_AUTH_JWT_ISSUER",
    "MCP_PORTAL_AUTH_JWT_AUDIENCE",
    "MCP_PORTAL_AUTH_JWT_ALGORITHM",
    "MCP_PORTAL_AUTH_LDAP_URI",
    "MCP_PORTAL_AUTH_LDAP_BASE_DN",
    "MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE",
    "MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER",
    "MCP_PORTAL_AUTH_LDAP_BIND_DN",
    "MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD",
    "MCP_PORTAL_AUTH_LDAP_START_TLS",
    "MCP_PORTAL_AUTH_LDAP_CA_CERT_FILE",
    "MCP_PORTAL_AUTH_LDAP_CONNECT_TIMEOUT",
    "MCP_PORTAL_AUTH_LDAP_SCOPES",
    "MCP_PORTAL_AUTH_KERBEROS_HOSTNAME",
    "MCP_PORTAL_AUTH_KERBEROS_SERVICE",
    "MCP_PORTAL_AUTH_KERBEROS_KEYTAB",
    "MCP_PORTAL_AUTH_KERBEROS_SCOPES",
    "MCP_PORTAL_AUTHZ_TAG_SCOPES",
    "MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES",
    "MCP_PORTAL_MIDDLEWARE_ENABLED",
    "MCP_PORTAL_STRUCTURED_LOGGING",
    "MCP_PORTAL_LOG_PAYLOAD_LENGTHS",
    "MCP_PORTAL_RATE_LIMIT_PER_SECOND",
    "MCP_PORTAL_RATE_LIMIT_BURST",
    "MCP_PORTAL_RESPONSE_MAX_BYTES",
    "MCP_PORTAL_HTTP_PATH",
    "MCP_PORTAL_HEALTH_PATH",
    "MCP_PORTAL_JSON_RESPONSE",
    "MCP_PORTAL_STATELESS_HTTP",
    "MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT",
    "MCP_PORTAL_DATABASE_PROVIDER",
    "MCP_PORTAL_DATABASE_SQLALCHEMY_URL",
    "MCP_PORTAL_ORACLE_DSN",
    "MCP_PORTAL_ORACLE_USER",
    "MCP_PORTAL_ORACLE_PASSWORD",
    "MCP_PORTAL_ORACLE_POOL_MIN",
    "MCP_PORTAL_ORACLE_POOL_MAX",
    "MCP_PORTAL_MONGODB_CONNECTION_STRING",
    "MCP_PORTAL_MONGODB_DATABASE",
    "MCP_PORTAL_MONGODB_VECTOR_SEARCH_INDEX",
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


@pytest.fixture(autouse=True)
def clean_portal_environment():
    """Clear portal environment variables around config tests."""
    original = {name: os.environ.get(name) for name in PORTAL_ENV_NAMES}
    for name in PORTAL_ENV_NAMES:
        os.environ.pop(name, None)

    yield

    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_settings_from_explicit_env_file(tmp_path: Path, monkeypatch) -> None:
    """Verify settings load from an explicit dotenv file."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=from-file",
                "OPENAI_LARGE_LANGUAGE_MODEL=large-from-file",
                "OPENAI_SMALL_LANGUAGE_MODEL=small-from-file",
                "OPENAI_EMBEDDING_MODEL=embedding-from-file",
                "MCP_PORTAL_MODEL_PROVIDER=azure_openai",
                "AZURE_OPENAI_ENDPOINT=https://portal-test.openai.azure.com/",
                "AZURE_OPENAI_API_VERSION=2025-01-01",
                "AZURE_OPENAI_TOKEN_SCOPE=https://example.azure/.default",
                "AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT=azure-large-from-file",
                "AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT=azure-small-from-file",
                "AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT=azure-embedding-from-file",
                "AZURE_TENANT_ID=tenant-from-file",
                "AZURE_CLIENT_ID=client-from-file",
                "AZURE_CLIENT_SECRET=secret-from-file",
                "MCP_PORTAL_HEALTH_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    for name in PORTAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file)

    assert settings.openai_api_key == "from-file"
    assert settings.model_provider == "azure_openai"
    assert settings.model_provider_configured is True
    assert settings.large_language_model == "azure-large-from-file"
    assert settings.small_language_model == "azure-small-from-file"
    assert settings.embedding_model == "azure-embedding-from-file"
    assert settings.namespace_enabled("health") is False
    snapshot = settings.public_snapshot()
    assert snapshot["model_provider"] == {
        "provider": "azure_openai",
        "configured": True,
        "auth_mode": "azure_identity",
        "large_language_model": "azure-large-from-file",
        "small_language_model": "azure-small-from-file",
        "embedding_model": "azure-embedding-from-file",
    }
    assert snapshot["openai"] == {
        "has_api_key": True,
        "large_language_model": "large-from-file",
        "small_language_model": "small-from-file",
        "embedding_model": "embedding-from-file",
    }
    assert snapshot["azure_openai"] == {
        "auth_mode": "azure_identity",
        "configured": True,
        "endpoint_configured": True,
        "api_version": "2025-01-01",
        "api_version_configured": True,
        "token_scope": "https://example.azure/.default",
        "deployments_configured": True,
        "large_language_model_deployment": "azure-large-from-file",
        "small_language_model_deployment": "azure-small-from-file",
        "embedding_model_deployment": "azure-embedding-from-file",
    }
    assert snapshot["azure_identity"] == {
        "service_principal_configured": True,
        "tenant_id_configured": True,
        "client_id_configured": True,
        "client_secret_configured": True,
    }
    assert snapshot["health"] == {"enabled": False}
    assert snapshot["auth"]["provider"] == "none"
    assert snapshot["database"] == {
        "provider": "oracle",
        "oracle_preferred": True,
        "sqlalchemy_enforced": True,
        "sqlalchemy_configured": False,
        "sqlalchemy_url_configured": False,
        "oracle_configured": False,
        "oracle_dsn_configured": False,
        "oracle_user_configured": False,
        "oracle_pool_min": 1,
        "oracle_pool_max": 4,
    }
    assert snapshot["mongodb"] == {
        "configured": False,
        "connection_string_configured": False,
        "database_configured": False,
        "collections": {
            "cache": "cache",
            "chat_history": "chat_history",
            "documents": "documents",
            "semantic_cache": "semantic_cache",
        },
        "vector_search_configured": False,
        "vector_search_index": "vector_index",
    }


def test_settings_load_production_options(tmp_path: Path, monkeypatch) -> None:
    """Verify production settings load from environment variables."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MCP_PORTAL_AUTH_PROVIDER=jwt",
                "MCP_PORTAL_AUTH_REQUIRED_SCOPES=portal.read,portal.write",
                "MCP_PORTAL_AUTH_JWT_JWKS_URI=https://issuer.example/.well-known/jwks.json",
                "MCP_PORTAL_AUTH_JWT_ISSUER=https://issuer.example",
                "MCP_PORTAL_AUTH_JWT_AUDIENCE=mcp-portal",
                "MCP_PORTAL_AUTHZ_TAG_SCOPES=admin=admin;write=portal.write",
                "MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES=finance=finance.read;hr=hr.read hr.audit",
                "MCP_PORTAL_MIDDLEWARE_ENABLED=true",
                "MCP_PORTAL_RATE_LIMIT_PER_SECOND=7.5",
                "MCP_PORTAL_RATE_LIMIT_BURST=11",
                "MCP_PORTAL_RESPONSE_MAX_BYTES=2048",
                "MCP_PORTAL_HTTP_PATH=/api/mcp",
                "MCP_PORTAL_HEALTH_PATH=/ready",
                "MCP_PORTAL_JSON_RESPONSE=true",
                "MCP_PORTAL_STATELESS_HTTP=false",
                "MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT=true",
                "MCP_PORTAL_DATABASE_SQLALCHEMY_URL=sqlite:///portable-test.db",
                "MCP_PORTAL_ORACLE_DSN=db.example/orclpdb1",
                "MCP_PORTAL_ORACLE_USER=portal",
                "MCP_PORTAL_ORACLE_PASSWORD=secret",
                "MCP_PORTAL_ORACLE_POOL_MIN=2",
                "MCP_PORTAL_ORACLE_POOL_MAX=8",
                "MCP_PORTAL_MONGODB_CONNECTION_STRING=mongodb+srv://user:secret@cluster.example/test",
                "MCP_PORTAL_MONGODB_DATABASE=portal",
                "MCP_PORTAL_MONGODB_VECTOR_SEARCH_INDEX=portal_vector",
                "OTEL_SERVICE_NAME=portal-prod",
                "OTEL_EXPORTER_OTLP_ENDPOINT=http://otel.example:4317",
            ]
        ),
        encoding="utf-8",
    )
    for name in PORTAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file, override=True)

    assert settings.auth.provider == "jwt"
    assert settings.auth.required_scopes == ("portal.read", "portal.write")
    assert settings.auth.jwt_jwks_uri == "https://issuer.example/.well-known/jwks.json"
    assert settings.authorization.tag_scopes == {
        "admin": ("admin",),
        "write": ("portal.write",),
    }
    assert settings.authorization.namespace_scopes == {
        "finance": ("finance.read",),
        "hr": ("hr.read", "hr.audit"),
    }
    assert settings.middleware.enabled is True
    assert settings.middleware.rate_limit_per_second == 7.5
    assert settings.middleware.rate_limit_burst == 11
    assert settings.middleware.response_max_bytes == 2048
    assert settings.http.path == "/api/mcp"
    assert settings.http.health_path == "/ready"
    assert settings.http.json_response is True
    assert settings.http.stateless is False
    assert settings.namespace_discovery.strict is True
    assert settings.database.provider == "oracle"
    assert settings.database.sqlalchemy_url == "sqlite:///portable-test.db"
    assert settings.database.sqlalchemy_configured is True
    assert settings.database.oracle_configured is True
    assert settings.database.oracle_pool_min == 2
    assert settings.database.oracle_pool_max == 8
    assert settings.mongodb.configured is True
    assert settings.mongodb.connection_string == ("mongodb+srv://user:secret@cluster.example/test")
    assert settings.mongodb.database_name == "portal"
    assert settings.mongodb.collection_name("documents") == "documents"
    assert settings.mongodb.collection_name("chat_history") == "chat_history"
    assert settings.mongodb.namespace() == "portal.documents"
    assert settings.mongodb.vector_search_index == "portal_vector"
    assert settings.mongodb.vector_search_configured is True
    assert settings.observability.enabled is True
    assert settings.observability.service_name == "portal-prod"


def test_settings_load_combined_ldap_and_kerberos_auth(tmp_path: Path) -> None:
    """Verify both enterprise providers can be enabled from one auth setting."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MCP_PORTAL_AUTH_PROVIDER=ldap+kerberos",
                "MCP_PORTAL_AUTH_LDAP_URI=ldaps://directory.example:636",
                "MCP_PORTAL_AUTH_LDAP_BASE_DN=dc=example,dc=com",
                "MCP_PORTAL_AUTH_LDAP_BIND_DN=cn=portal,dc=example,dc=com",
                "MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD=directory-secret",
                "MCP_PORTAL_AUTH_LDAP_SCOPES=portal,write",
                "MCP_PORTAL_AUTH_KERBEROS_HOSTNAME=portal.example.com",
                "MCP_PORTAL_AUTH_KERBEROS_SERVICE=HTTP",
                "MCP_PORTAL_AUTH_KERBEROS_KEYTAB=/run/secrets/portal.keytab",
                "MCP_PORTAL_AUTH_KERBEROS_SCOPES=portal admin",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env(env_file, override=True)

    assert settings.auth.provider == "ldap_kerberos"
    assert settings.auth.ldap_uri == "ldaps://directory.example:636"
    assert settings.auth.ldap_bind_password == "directory-secret"
    assert settings.auth.ldap_scopes == ("portal", "write")
    assert settings.auth.kerberos_hostname == "portal.example.com"
    assert settings.auth.kerberos_keytab == "/run/secrets/portal.keytab"
    assert settings.auth.kerberos_scopes == ("portal", "admin")
    snapshot = settings.auth.public_snapshot()
    assert snapshot["ldap_bind_password_configured"] is True
    assert snapshot["kerberos_keytab_configured"] is True
    assert "directory-secret" not in str(snapshot)


def test_settings_defaults_and_placeholder_key(monkeypatch) -> None:
    """Verify defaults are used and placeholder keys are not treated as configured."""
    for name in PORTAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "your-api-key")

    settings = Settings.from_env(env_file=Path("does-not-exist.env"))

    assert settings.has_openai_api_key is False
    assert settings.model_provider == "openai"
    assert settings.model_provider_configured is False
    snapshot = settings.public_snapshot()
    assert snapshot["model_provider"] == {
        "provider": "openai",
        "configured": False,
        "auth_mode": "api_key",
        "large_language_model": "gpt-5.5",
        "small_language_model": "gpt-5.5-mini",
        "embedding_model": "text-embedding-3-large",
    }
    assert snapshot["openai"] == {
        "has_api_key": False,
        "large_language_model": "gpt-5.5",
        "small_language_model": "gpt-5.5-mini",
        "embedding_model": "text-embedding-3-large",
    }
    assert snapshot["azure_openai"] == {
        "auth_mode": "azure_identity",
        "configured": False,
        "endpoint_configured": False,
        "api_version": None,
        "api_version_configured": False,
        "token_scope": "https://cognitiveservices.azure.com/.default",
        "deployments_configured": False,
        "large_language_model_deployment": None,
        "small_language_model_deployment": None,
        "embedding_model_deployment": None,
    }
    assert snapshot["azure_identity"] == {
        "service_principal_configured": False,
        "tenant_id_configured": False,
        "client_id_configured": False,
        "client_secret_configured": False,
    }
    assert snapshot["health"] == {"enabled": True}
    assert snapshot["auth"]["enabled"] is False
    assert snapshot["authorization"]["tag_scopes"]["admin"] == ["admin"]
    assert snapshot["authorization"]["namespace_scopes"] == {}
    assert snapshot["middleware"]["enabled"] is False
    assert snapshot["http"]["path"] == "/mcp"
    assert snapshot["namespace_discovery"] == {"strict": False}
    assert snapshot["database"]["oracle_preferred"] is True
    assert snapshot["mongodb"]["configured"] is False


def test_azure_openai_provider_uses_azure_identity_without_service_principal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify Azure OpenAI uses Azure Identity without requiring API-key settings."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MCP_PORTAL_MODEL_PROVIDER=azure_openai",
                "AZURE_OPENAI_ENDPOINT=https://portal-test.openai.azure.com/",
                "AZURE_OPENAI_API_VERSION=2025-01-01",
                "AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT=azure-large",
                "AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT=azure-small",
                "AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT=azure-embedding",
            ]
        ),
        encoding="utf-8",
    )
    for name in PORTAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file)

    assert settings.model_provider == "azure_openai"
    assert settings.model_provider_configured is True
    assert settings.azure_identity.service_principal_configured is False
    assert settings.public_snapshot()["model_provider"] == {
        "provider": "azure_openai",
        "configured": True,
        "auth_mode": "azure_identity",
        "large_language_model": "azure-large",
        "small_language_model": "azure-small",
        "embedding_model": "azure-embedding",
    }


def test_settings_from_env_file_can_override_existing_values(tmp_path: Path, monkeypatch) -> None:
    """Verify explicit override mode lets dotenv values win."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_LARGE_LANGUAGE_MODEL=large-from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_LARGE_LANGUAGE_MODEL", "large-from-env")

    settings = Settings.from_env(env_file, override=True)

    assert settings.openai_large_language_model == "large-from-file"


def test_resolve_env_file_prefers_explicit_path(tmp_path: Path) -> None:
    """Verify explicit dotenv paths are returned unchanged."""
    env_file = tmp_path / "custom.env"

    assert _resolve_env_file(env_file) == env_file


def test_optional_env_strips_blank_values(monkeypatch) -> None:
    """Verify blank optional environment variables normalize to None."""
    monkeypatch.setenv("OPTIONAL_VALUE", "   ")

    assert _optional_env("OPTIONAL_VALUE") is None


def test_optional_env_returns_missing_values(monkeypatch) -> None:
    """Verify missing optional environment variables normalize to None."""
    monkeypatch.delenv("OPTIONAL_VALUE", raising=False)

    assert _optional_env("OPTIONAL_VALUE") is None


def test_bool_env_parses_boolean_values(monkeypatch) -> None:
    """Verify boolean environment values are normalized."""
    monkeypatch.setenv("BOOLEAN_VALUE", "off")

    assert _bool_env("BOOLEAN_VALUE", default=True) is False


def test_bool_env_uses_default_for_invalid_values(monkeypatch) -> None:
    """Verify invalid boolean environment values fall back to the default."""
    monkeypatch.setenv("BOOLEAN_VALUE", "sometimes")

    assert _bool_env("BOOLEAN_VALUE", default=True) is True
