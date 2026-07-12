"""Test production authentication, integrations, lifecycle, and ASGI setup."""

from __future__ import annotations

import base64
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import mcp_portal.clients as clients_module
import mcp_portal.auth as auth_module
import mcp_portal.namespaces as namespace_registry
from mcp_portal.asgi import create_app
from mcp_portal.auth import create_auth_provider, create_authorization_checks
from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import (
    AuthSettings,
    DatabaseSettings,
    HttpSettings,
    MongoDBSettings,
    MiddlewareSettings,
    ObservabilitySettings,
)
from mcp_portal.contracts import generate_tool_contract_manifest
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.middleware import create_production_middleware
from mcp_portal.observability import configure_observability_environment
import mcp_portal.server as server_module
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


def test_ldap_auth_provider_requires_encrypted_directory_connection(monkeypatch) -> None:
    """Verify cleartext LDAP credentials are rejected unless StartTLS is enabled."""
    monkeypatch.setattr(auth_module, "_require_optional_dependency", lambda *_: None)
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(
            provider="ldap",
            ldap_uri="ldap://directory.example",
            ldap_user_dn_template="uid={username},dc=example,dc=com",
        ),
    )

    with pytest.raises(ConfigurationPortalError, match="requires LDAPS"):
        create_auth_provider(settings)


async def test_ldap_auth_provider_verifies_basic_credentials(monkeypatch) -> None:
    """Verify LDAP credentials are decoded, bound, and mapped to configured scopes."""
    monkeypatch.setattr(auth_module, "_require_optional_dependency", lambda *_: None)
    monkeypatch.setattr(
        auth_module,
        "_verify_ldap_credentials",
        lambda settings, username, password: username == "alice" and password == "secret",
    )
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(
            provider="ldap",
            ldap_uri="ldaps://directory.example",
            ldap_user_dn_template="uid={username},dc=example,dc=com",
            ldap_scopes=("portal", "write"),
        ),
    )
    provider = create_auth_provider(settings)
    assert provider is not None
    credentials = base64.b64encode(b"alice:secret").decode()

    access_token = await provider.verify_token(f"ldap:{credentials}")

    assert access_token is not None
    assert access_token.subject == "alice"
    assert access_token.scopes == ["portal", "write"]
    assert access_token.claims["auth_method"] == "ldap"


async def test_combined_auth_provider_accepts_kerberos_ticket(monkeypatch) -> None:
    """Verify combined mode accepts Kerberos tickets and maps the principal."""
    monkeypatch.setattr(auth_module, "_require_optional_dependency", lambda *_: None)
    monkeypatch.setattr(
        auth_module,
        "_verify_kerberos_ticket",
        lambda settings, ticket: ("alice@EXAMPLE.COM", b"server-token"),
    )
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(
            provider="ldap_kerberos",
            ldap_uri="ldaps://directory.example",
            ldap_base_dn="dc=example,dc=com",
            kerberos_hostname="portal.example.com",
            kerberos_scopes=("portal",),
        ),
    )
    provider = create_auth_provider(settings)
    assert provider is not None
    ticket = base64.b64encode(b"service-ticket").decode()

    access_token = await provider.verify_token(f"kerberos:{ticket}")

    assert access_token is not None
    assert access_token.subject == "alice@EXAMPLE.COM"
    assert access_token.scopes == ["portal"]
    assert access_token.claims["auth_method"] == "kerberos"


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        (AuthSettings(provider="ldap"), "requires MCP_PORTAL_AUTH_LDAP_URI"),
        (
            AuthSettings(provider="ldap", ldap_uri="ldaps://directory.example"),
            "requires MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE",
        ),
        (
            AuthSettings(
                provider="ldap",
                ldap_uri="ldaps://directory.example",
                ldap_user_dn_template="uid=alice,dc=example,dc=com",
            ),
            "must contain {username}",
        ),
        (
            AuthSettings(
                provider="ldap",
                ldap_uri="ldaps://directory.example",
                ldap_base_dn="dc=example,dc=com",
                ldap_search_filter="(uid=alice)",
            ),
            "SEARCH_FILTER must contain {username}",
        ),
        (
            AuthSettings(
                provider="ldap",
                ldap_uri="ldaps://directory.example",
                ldap_base_dn="dc=example,dc=com",
                ldap_bind_dn="cn=portal,dc=example,dc=com",
            ),
            "must be configured together",
        ),
        (AuthSettings(provider="kerberos"), "requires MCP_PORTAL_AUTH_KERBEROS_HOSTNAME"),
    ],
)
def test_enterprise_auth_rejects_incomplete_configuration(
    monkeypatch, auth: AuthSettings, message: str
) -> None:
    """Verify incomplete enterprise provider settings fail at startup."""
    monkeypatch.setattr(auth_module, "_require_optional_dependency", lambda *_: None)

    with pytest.raises(ConfigurationPortalError, match=message):
        create_auth_provider(replace(create_test_settings(), auth=auth))


async def test_enterprise_provider_rejects_malformed_and_failed_credentials(monkeypatch) -> None:
    """Verify malformed tokens and failed backend verification are rejected."""
    monkeypatch.setattr(auth_module, "_require_optional_dependency", lambda *_: None)
    monkeypatch.setattr(auth_module, "_verify_ldap_credentials", lambda *_: False)
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(
            provider="ldap_kerberos",
            ldap_uri="ldaps://directory.example",
            ldap_base_dn="dc=example,dc=com",
            kerberos_hostname="portal.example.com",
        ),
    )
    provider = create_auth_provider(settings)
    assert provider is not None

    assert await provider.verify_token("missing-scheme") is None
    assert await provider.verify_token("ldap:not-base64") is None
    assert await provider.verify_token(f"ldap:{base64.b64encode(b'alice:bad').decode()}") is None
    assert await provider.verify_token("unknown:value") is None
    assert await provider.verify_token("kerberos:not-base64") is None


async def test_enterprise_scheme_middleware_translates_and_challenges() -> None:
    """Verify standard Basic/Negotiate headers and mutual-auth challenges are adapted."""
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="ldap_kerberos"),
    )
    provider = auth_module.EnterpriseAuthProvider(settings)
    captured_scopes: list[dict] = []

    async def app(scope, receive, send) -> None:
        captured_scopes.append(scope)
        if b"ldap:" in dict(scope.get("headers", ())).get(b"authorization", b""):
            auth_module._kerberos_response_token.set(b"server-token")
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = auth_module.EnterpriseAuthSchemeMiddleware(app, provider)

    async def receive() -> dict:
        return {"type": "http.request"}

    responses: list[dict] = []

    async def send(message: dict) -> None:
        responses.append(message)

    await middleware(
        {"type": "http", "headers": [(b"authorization", b"Basic credentials")]},
        receive,
        send,
    )
    response_headers = responses[0]["headers"]
    assert dict(captured_scopes[0]["headers"])[b"authorization"] == b"Bearer ldap:credentials"
    assert (b"www-authenticate", b"Negotiate c2VydmVyLXRva2Vu") in response_headers
    assert (b"www-authenticate", b'Basic realm="mcp-portal"') in response_headers

    responses.clear()
    await middleware(
        {"type": "http", "headers": [(b"x-test", b"1"), (b"authorization", b"Negotiate ticket")]},
        receive,
        send,
    )
    assert dict(captured_scopes[1]["headers"])[b"authorization"] == b"Bearer kerberos:ticket"
    assert (b"www-authenticate", b"Negotiate") in responses[0]["headers"]

    await middleware({"type": "websocket"}, receive, send)
    assert captured_scopes[2]["type"] == "websocket"


def test_sdk_auth_settings_normalize_portal_configuration() -> None:
    """Verify SDK auth settings are derived for authenticated HTTP servers."""
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(
            provider="static",
            required_scopes=("portal",),
            static_token="local-token",
        ),
        http=HttpSettings(path="mcp"),
    )

    auth_settings = server_module._sdk_auth_settings(settings)

    assert auth_settings is not None
    assert str(auth_settings.issuer_url) == "http://localhost/"
    assert str(auth_settings.resource_server_url) == "http://localhost/mcp"
    assert auth_settings.required_scopes == ["portal"]


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


def test_langchain_mongodb_factory_is_independent_of_database_provider(monkeypatch) -> None:
    """Verify LangChain MongoDB connectors do not depend on SQLAlchemy provider config."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(provider="none"),
        mongodb=MongoDBSettings(
            connection_string="mongodb://cluster.example",
            database_name="portal",
            vector_search_index="portal_vector",
        ),
    )
    captured = {}

    class FakeVectorSearch:
        """Capture vector-search construction options."""

        @classmethod
        def from_connection_string(cls, **kwargs):
            """Return construction options for assertion."""
            captured["vector_search"] = kwargs
            return {"connector": "vector_search"}

    class FakeChatMessageHistory:
        """Capture chat history construction options."""

        def __init__(self, **kwargs) -> None:
            """Capture constructor options."""
            captured["chat_history"] = kwargs

    fake_module = SimpleNamespace(
        MongoDBAtlasVectorSearch=FakeVectorSearch,
        MongoDBChatMessageHistory=FakeChatMessageHistory,
        MongoDBCache=object,
        MongoDBAtlasSemanticCache=object,
    )
    monkeypatch.setattr(clients_module, "_import_langchain_mongodb", lambda: fake_module)

    registry = default_client_factories(settings)
    connector = registry.create("langchain_mongodb")
    vector_store = connector.vector_search(embedding="embedding")
    history = connector.chat_message_history(session_id="session-1")

    assert registry.get("database") is None
    assert registry.get("langchain_mongodb") is not None
    assert vector_store == {"connector": "vector_search"}
    assert isinstance(history, FakeChatMessageHistory)
    assert captured == {
        "vector_search": {
            "connection_string": "mongodb://cluster.example",
            "namespace": "portal.documents",
            "embedding": "embedding",
            "index_name": "portal_vector",
        },
        "chat_history": {
            "connection_string": "mongodb://cluster.example",
            "session_id": "session-1",
            "database_name": "portal",
            "collection_name": "chat_history",
        },
    }


def test_langchain_mongodb_factory_requires_optional_dependency(monkeypatch) -> None:
    """Verify configured MongoDB connector access fails through its optional boundary."""
    settings = replace(
        create_test_settings(),
        mongodb=MongoDBSettings(connection_string="mongodb://cluster.example"),
    )
    monkeypatch.setattr(
        clients_module,
        "_import_langchain_mongodb",
        lambda: (_ for _ in ()).throw(ImportError("missing langchain-mongodb")),
    )
    registry = default_client_factories(settings)

    with pytest.raises(ConfigurationPortalError, match="langchain-mongodb dependency"):
        registry.create("langchain_mongodb")


def test_langchain_mongodb_connector_helpers_use_configured_defaults(monkeypatch) -> None:
    """Verify connector helper methods pass configured MongoDB defaults."""
    settings = MongoDBSettings(
        connection_string="mongodb://cluster.example",
        database_name="portal",
        vector_search_index="portal_vector",
    )
    connector = clients_module.MongoDBConnectors(settings)
    captured = {}

    class FakeCache:
        """Capture cache construction options."""

        def __init__(self, **kwargs) -> None:
            """Capture constructor options."""
            captured["cache"] = kwargs

    class FakeSemanticCache:
        """Capture semantic-cache construction options."""

        def __init__(self, **kwargs) -> None:
            """Capture constructor options."""
            captured["semantic_cache"] = kwargs

    class FakeLoader:
        """Capture loader construction options."""

        @classmethod
        def from_connection_string(cls, **kwargs):
            """Return construction options for assertion."""
            captured["loader"] = kwargs
            return {"connector": "loader"}

    class FakeDocStore:
        """Capture docstore construction options."""

        @classmethod
        def from_connection_string(cls, **kwargs):
            """Return construction options for assertion."""
            captured["doc_store"] = kwargs
            return {"connector": "doc_store"}

    class FakeAgentDatabase:
        """Capture agent database construction options."""

        @classmethod
        def from_connection_string(cls, **kwargs):
            """Return construction options for assertion."""
            captured["agent_database"] = kwargs
            return {"connector": "agent_database"}

    fake_module = SimpleNamespace(
        MongoDBAtlasVectorSearch=object,
        MongoDBChatMessageHistory=object,
        MongoDBCache=FakeCache,
        MongoDBAtlasSemanticCache=FakeSemanticCache,
    )
    monkeypatch.setattr(clients_module, "_import_langchain_mongodb", lambda: fake_module)
    monkeypatch.setattr(clients_module, "_import_langchain_mongodb_loader", lambda: FakeLoader)
    monkeypatch.setattr(
        clients_module,
        "_import_langchain_mongodb_doc_store",
        lambda: FakeDocStore,
    )
    monkeypatch.setattr(
        clients_module,
        "_import_langchain_mongodb_agent_database",
        lambda: FakeAgentDatabase,
    )

    assert isinstance(connector.cache(), FakeCache)
    assert isinstance(connector.semantic_cache(embedding="embedding"), FakeSemanticCache)
    assert connector.loader() == {"connector": "loader"}
    assert connector.doc_store() == {"connector": "doc_store"}
    assert connector.agent_database() == {"connector": "agent_database"}
    assert captured == {
        "cache": {
            "connection_string": "mongodb://cluster.example",
            "database_name": "portal",
            "collection_name": "cache",
        },
        "semantic_cache": {
            "connection_string": "mongodb://cluster.example",
            "embedding": "embedding",
            "database_name": "portal",
            "collection_name": "semantic_cache",
            "index_name": "portal_vector",
        },
        "loader": {
            "connection_string": "mongodb://cluster.example",
            "db_name": "portal",
            "collection_name": "documents",
        },
        "doc_store": {
            "connection_string": "mongodb://cluster.example",
            "namespace": "portal.documents",
        },
        "agent_database": {
            "connection_string": "mongodb://cluster.example",
            "database": "portal",
        },
    }


def test_langchain_mongodb_connector_reports_missing_required_settings() -> None:
    """Verify helper methods fail clearly when required MongoDB settings are absent."""
    connector = clients_module.MongoDBConnectors(
        MongoDBSettings(connection_string="mongodb://cluster.example")
    )

    with pytest.raises(ConfigurationPortalError, match="requires a database"):
        connector.vector_search(embedding="embedding")

    with pytest.raises(ConfigurationPortalError, match="database name"):
        connector.agent_database()

    missing_uri_connector = clients_module.MongoDBConnectors(MongoDBSettings())
    with pytest.raises(ConfigurationPortalError, match="CONNECTION_STRING"):
        _ = missing_uri_connector.connection_string

    configured_connector = clients_module.MongoDBConnectors(
        MongoDBSettings(
            connection_string="mongodb://cluster.example",
            database_name="portal",
        )
    )
    with pytest.raises(ConfigurationPortalError, match="collection alias"):
        configured_connector.loader(collection="unknown")


async def test_tool_contract_manifest_fingerprints_health_tools() -> None:
    """Verify tool contract fingerprints are generated for mounted namespace tools."""
    server = create_mcp(create_test_settings())

    manifest = await generate_tool_contract_manifest(server)

    assert any("health_ping" in key for key in manifest)
    assert all(len(fingerprint) == 64 for fingerprint in manifest.values())


@pytest.mark.asyncio
async def test_production_asgi_app_exposes_health_route() -> None:
    """Verify the production ASGI entrypoint exposes an operational health route."""
    settings = replace(
        create_test_settings(),
        http=HttpSettings(path="/mcp", health_path="/healthz"),
    )

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)),
        base_url="http://test",
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "service": "mcp-portal"}


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
