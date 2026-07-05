from __future__ import annotations

import os
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

import mcp_portal.clients as clients_module
import mcp_portal.namespaces as namespace_registry
from mcp_portal.asgi import create_app
from mcp_portal.auth import create_auth_provider, create_authorization_checks
from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import (
    AuthSettings,
    DatabaseSettings,
    HttpSettings,
    MiddlewareSettings,
    ObservabilitySettings,
)
from mcp_portal.contracts import generate_tool_contract_manifest
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.middleware import create_production_middleware
from mcp_portal.observability import configure_observability_environment
from mcp_portal.server import create_mcp
from mcp_portal.testing import create_test_settings


async def test_static_auth_provider_verifies_configured_token() -> None:
    """Verify static auth is available for local production smoke tests."""
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(
            provider="static",
            required_scopes=("portal",),
            static_token="local-token",
            static_scopes=("portal", "admin"),
        ),
    )

    provider = create_auth_provider(settings)
    assert provider is not None
    access_token = await provider.verify_token("local-token")

    assert access_token is not None
    assert access_token.client_id == "mcp-portal-static"
    assert access_token.scopes == ["portal", "admin"]


def test_jwt_auth_provider_requires_verification_material() -> None:
    """Verify JWT auth fails fast when no key source is configured."""
    settings = replace(create_test_settings(), auth=AuthSettings(provider="jwt"))

    with pytest.raises(ConfigurationPortalError, match="JWT authentication requires"):
        create_auth_provider(settings)


def test_jwt_auth_provider_accepts_symmetric_key_configuration() -> None:
    """Verify JWT auth can be configured from a static shared secret."""
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="jwt", jwt_public_key="secret", jwt_algorithm="HS256"),
    )

    assert create_auth_provider(settings) is not None


def test_authorization_checks_follow_tag_scope_settings() -> None:
    """Verify tag-to-scope authorization checks are built from settings."""
    settings = create_test_settings()

    checks = create_authorization_checks(settings)

    assert len(checks) == 4


def test_production_middleware_can_be_forced_on_and_off() -> None:
    """Verify production middleware composition follows the enable flag."""
    settings = replace(
        create_test_settings(),
        middleware=MiddlewareSettings(
            enabled=True,
            rate_limit_per_second=0,
            response_max_bytes=0,
        ),
    )

    assert create_production_middleware(settings, enabled=False) == ()
    middleware = create_production_middleware(settings, enabled=True)

    assert [type(item).__name__ for item in middleware] == [
        "ErrorHandlingMiddleware",
        "AuthMiddleware",
        "StructuredLoggingMiddleware",
        "TimingMiddleware",
    ]


async def test_lifecycle_managed_clients_are_reused_and_closed() -> None:
    """Verify shared client factories reuse and close clients."""

    class ClosableClient:
        """Small closeable client for lifecycle tests."""

        def __init__(self) -> None:
            """Initialize close state."""
            self.closed = False

        async def aclose(self) -> None:
            """Close the test client asynchronously."""
            self.closed = True

    registry = ClientFactories().with_factory("example", ClosableClient, shared=True)
    first = registry.create("example")
    second = registry.create("example")

    await registry.aclose()

    assert first is second
    assert first.closed is True

    class DisposableEngine:
        """Small SQLAlchemy-like engine for disposal tests."""

        def __init__(self) -> None:
            """Initialize disposal state."""
            self.disposed = False

        def dispose(self) -> None:
            """Dispose the test engine."""
            self.disposed = True

    engine_registry = ClientFactories().with_factory("database", DisposableEngine, shared=True)
    engine = engine_registry.create("database")

    await engine_registry.aclose()

    assert engine.disposed is True


def test_oracle_backend_factory_is_registered_when_configured(monkeypatch) -> None:
    """Verify Oracle config creates a SQLAlchemy database engine factory."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(
            provider="oracle",
            oracle_dsn="db.example/orclpdb1",
            oracle_user="portal",
            oracle_password="secret",
        ),
    )
    captured = {}

    def fake_create_engine(url, **kwargs):
        """Capture SQLAlchemy engine construction options."""
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"engine": "sqlalchemy"}

    monkeypatch.setattr(
        clients_module,
        "_import_sqlalchemy_create_engine",
        lambda: fake_create_engine,
    )
    registry = default_client_factories(settings)
    engine = registry.create("database")

    assert registry.get("database") is not None
    assert registry.get("oracle") is None
    assert engine == {"engine": "sqlalchemy"}
    assert captured == {
        "url": "oracle+oracledb://",
        "kwargs": {
            "pool_pre_ping": True,
            "pool_size": 1,
            "max_overflow": 3,
            "connect_args": {
                "user": "portal",
                "password": "secret",
                "dsn": "db.example/orclpdb1",
            },
        },
    }


def test_sqlalchemy_url_database_factory_is_portable(monkeypatch) -> None:
    """Verify a generic SQLAlchemy URL can be used for non-Oracle engines."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(
            provider="sqlalchemy",
            sqlalchemy_url="sqlite:///portable.db",
            oracle_pool_min=2,
            oracle_pool_max=5,
        ),
    )
    captured = {}

    def fake_create_engine(url, **kwargs):
        """Capture SQLAlchemy URL engine construction options."""
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"engine": "portable"}

    monkeypatch.setattr(
        clients_module,
        "_import_sqlalchemy_create_engine",
        lambda: fake_create_engine,
    )
    registry = default_client_factories(settings)

    assert registry.create("database") == {"engine": "portable"}
    assert captured == {
        "url": "sqlite:///portable.db",
        "kwargs": {
            "pool_pre_ping": True,
            "pool_size": 2,
            "max_overflow": 3,
        },
    }


def test_database_factory_requires_sqlalchemy_dependency(monkeypatch) -> None:
    """Verify configured database access fails through the SQLAlchemy boundary."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(sqlalchemy_url="sqlite:///portable.db"),
    )
    monkeypatch.setattr(
        clients_module,
        "_import_sqlalchemy_create_engine",
        lambda: (_ for _ in ()).throw(ImportError("missing sqlalchemy")),
    )
    registry = default_client_factories(settings)

    with pytest.raises(ConfigurationPortalError, match="SQLAlchemy dependency"):
        registry.create("database")


async def test_tool_contract_manifest_fingerprints_health_tools() -> None:
    """Verify tool contract fingerprints are generated for mounted namespace tools."""
    server = create_mcp(create_test_settings(), include_debug_ui=False)

    manifest = await generate_tool_contract_manifest(server)

    assert any("health_ping" in key for key in manifest)
    assert all(len(fingerprint) == 64 for fingerprint in manifest.values())


def test_production_asgi_app_exposes_health_route() -> None:
    """Verify the production ASGI entrypoint exposes an operational health route."""
    settings = replace(
        create_test_settings(),
        http=HttpSettings(path="/mcp", health_path="/healthz"),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "mcp-portal",
        "mcp_path": "/mcp",
        "oracle_preferred": True,
        "sqlalchemy_enforced": True,
        "database_configured": False,
        "oracle_configured": False,
    }


def test_observability_environment_uses_settings(monkeypatch) -> None:
    """Verify OpenTelemetry environment defaults are populated from settings."""
    settings = replace(
        create_test_settings(),
        observability=ObservabilitySettings(
            service_name="portal-prod",
            otlp_endpoint="http://otel.example:4317",
        ),
    )
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    configure_observability_environment(settings)

    assert os.environ["OTEL_SERVICE_NAME"] == "portal-prod"
    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel.example:4317"


def test_namespace_discovery_records_optional_import_errors(monkeypatch) -> None:
    """Verify optional namespace import failures are recorded in non-strict mode."""
    monkeypatch.setattr(namespace_registry, "_NAMESPACE_REGISTRY", {})
    monkeypatch.setattr(namespace_registry, "_DISCOVERED", False)
    monkeypatch.setattr(namespace_registry, "_DISCOVERY_ERRORS", {})
    monkeypatch.setattr(
        namespace_registry,
        "_iter_namespace_module_names",
        lambda: ["mcp_portal.namespaces.optional"],
    )

    def fail_import(module_name: str) -> object:
        """Simulate an optional dependency import failure."""
        raise ImportError(f"missing dependency for {module_name}")

    monkeypatch.setattr(namespace_registry.importlib, "import_module", fail_import)

    assert namespace_registry.iter_namespaces() == ()
    assert namespace_registry.iter_namespace_discovery_errors() == {
        "mcp_portal.namespaces.optional": (
            "ImportError: missing dependency for mcp_portal.namespaces.optional"
        )
    }

    monkeypatch.setattr(namespace_registry, "_DISCOVERED", False)
    with pytest.raises(ImportError):
        namespace_registry.iter_namespaces(strict=True)
