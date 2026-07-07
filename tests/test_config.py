from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_portal.config import Settings, _bool_env, _optional_env, _resolve_env_file

PORTAL_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_LARGE_LANGUAGE_MODEL",
    "OPENAI_SMALL_LANGUAGE_MODEL",
    "OPENAI_EMBEDDING_MODEL",
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
    "MCP_PORTAL_AUTHZ_TAG_SCOPES",
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
    "MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING",
    "MCP_PORTAL_LANGCHAIN_MONGODB_DATABASE",
    "MCP_PORTAL_LANGCHAIN_MONGODB_VECTOR_SEARCH_INDEX",
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
                "MCP_PORTAL_HEALTH_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    for name in PORTAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file)

    assert settings.openai_api_key == "from-file"
    assert settings.namespace_enabled("health") is False
    snapshot = settings.public_snapshot()
    assert snapshot["openai"] == {
        "has_api_key": True,
        "large_language_model": "large-from-file",
        "small_language_model": "small-from-file",
        "embedding_model": "embedding-from-file",
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
    assert snapshot["langchain_mongodb"] == {
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
                "MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING=mongodb+srv://user:secret@cluster.example/test",
                "MCP_PORTAL_LANGCHAIN_MONGODB_DATABASE=portal",
                "MCP_PORTAL_LANGCHAIN_MONGODB_VECTOR_SEARCH_INDEX=portal_vector",
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
    assert settings.langchain_mongodb.configured is True
    assert settings.langchain_mongodb.connection_string == (
        "mongodb+srv://user:secret@cluster.example/test"
    )
    assert settings.langchain_mongodb.database_name == "portal"
    assert settings.langchain_mongodb.collection_name("documents") == "documents"
    assert settings.langchain_mongodb.collection_name("chat_history") == "chat_history"
    assert settings.langchain_mongodb.namespace() == "portal.documents"
    assert settings.langchain_mongodb.vector_search_index == "portal_vector"
    assert settings.langchain_mongodb.vector_search_configured is True
    assert settings.observability.enabled is True
    assert settings.observability.service_name == "portal-prod"


def test_settings_defaults_and_placeholder_key(monkeypatch) -> None:
    """Verify defaults are used and placeholder keys are not treated as configured."""
    for name in PORTAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "your-api-key")

    settings = Settings.from_env(env_file=Path("does-not-exist.env"))

    assert settings.has_openai_api_key is False
    snapshot = settings.public_snapshot()
    assert snapshot["openai"] == {
        "has_api_key": False,
        "large_language_model": "gpt-5.5",
        "small_language_model": "gpt-5.5-mini",
        "embedding_model": "text-embedding-3-large",
    }
    assert snapshot["health"] == {"enabled": True}
    assert snapshot["auth"]["enabled"] is False
    assert snapshot["authorization"]["tag_scopes"]["admin"] == ["admin"]
    assert snapshot["middleware"]["enabled"] is False
    assert snapshot["http"]["path"] == "/mcp"
    assert snapshot["namespace_discovery"] == {"strict": False}
    assert snapshot["database"]["oracle_preferred"] is True
    assert snapshot["langchain_mongodb"]["configured"] is False


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
