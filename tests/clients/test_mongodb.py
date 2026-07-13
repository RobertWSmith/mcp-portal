"""Test MongoDB client factory and connector adapters."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

import mcp_portal.clients.mongodb as mongodb_module
from mcp_portal.clients import default_client_factories
from mcp_portal.config import DatabaseSettings, MongoDBSettings
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.testing import create_test_settings

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
    monkeypatch.setattr(mongodb_module, "_import_langchain_mongodb", lambda: fake_module)

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
        mongodb_module,
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
    connector = mongodb_module.MongoDBConnectors(settings)
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
    monkeypatch.setattr(mongodb_module, "_import_langchain_mongodb", lambda: fake_module)
    monkeypatch.setattr(mongodb_module, "_import_langchain_mongodb_loader", lambda: FakeLoader)
    monkeypatch.setattr(
        mongodb_module,
        "_import_langchain_mongodb_doc_store",
        lambda: FakeDocStore,
    )
    monkeypatch.setattr(
        mongodb_module,
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
    connector = mongodb_module.MongoDBConnectors(
        MongoDBSettings(connection_string="mongodb://cluster.example")
    )

    with pytest.raises(ConfigurationPortalError, match="requires a database"):
        connector.vector_search(embedding="embedding")

    with pytest.raises(ConfigurationPortalError, match="database name"):
        connector.agent_database()

    missing_uri_connector = mongodb_module.MongoDBConnectors(MongoDBSettings())
    with pytest.raises(ConfigurationPortalError, match="CONNECTION_STRING"):
        _ = missing_uri_connector.connection_string

    configured_connector = mongodb_module.MongoDBConnectors(
        MongoDBSettings(
            connection_string="mongodb://cluster.example",
            database_name="portal",
        )
    )
    with pytest.raises(ConfigurationPortalError, match="collection alias"):
        configured_connector.loader(collection="unknown")

