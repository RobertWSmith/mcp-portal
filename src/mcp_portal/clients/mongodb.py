"""LangChain MongoDB connector adapters and optional dependency loading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_portal.config import MongoDBCollectionName, MongoDBSettings, Settings
from mcp_portal.errors import ConfigurationPortalError


@dataclass(frozen=True)
class MongoDBConnectors:
    """Convenience factory for MongoDB integration objects.

    The connector keeps MongoDB support independent from the SQLAlchemy database
    provider switch. Namespaces supply embeddings or per-use overrides as needed.

    Attributes:
        settings: Runtime settings for MongoDB connectors.
    """

    settings: MongoDBSettings

    @property
    def connection_string(self) -> str:
        """Return the configured MongoDB connection URI.

        Returns:
            The configured MongoDB connection URI.

        Raises:
            ConfigurationPortalError: If the MongoDB URI is missing.
        """
        if self.settings.connection_string is None:
            raise ConfigurationPortalError(
                "MongoDB connectors require MCP_PORTAL_MONGODB_CONNECTION_STRING.",
                details={"client": "mongodb"},
            )
        return self.settings.connection_string

    @property
    def module(self) -> Any:
        """Return the imported `langchain_mongodb` package.

        Returns:
            The imported `langchain_mongodb` module.
        """
        return _import_langchain_mongodb()

    def vector_search(
        self,
        embedding: Any,
        *,
        database_name: str | None = None,
        collection: MongoDBCollectionName = "documents",
        index_name: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create a MongoDB Atlas Vector Search store.

        Args:
            embedding: LangChain embeddings object to use for vector search.
            database_name: Optional database override.
            collection: Hard-coded collection alias to use.
            index_name: Optional Atlas Vector Search index override.
            kwargs: Additional `MongoDBAtlasVectorSearch` keyword arguments.

        Returns:
            A `langchain_mongodb.MongoDBAtlasVectorSearch` instance.
        """
        if index_name is not None:
            kwargs["index_name"] = index_name
        else:
            kwargs.setdefault("index_name", self.settings.vector_search_index)

        return self.module.MongoDBAtlasVectorSearch.from_connection_string(
            connection_string=self.connection_string,
            namespace=self._namespace(
                database_name=database_name,
                collection=collection,
                required_for="vector search",
            ),
            embedding=embedding,
            **kwargs,
        )

    def chat_message_history(
        self,
        session_id: str,
        *,
        database_name: str | None = None,
        collection: MongoDBCollectionName = "chat_history",
        **kwargs: Any,
    ) -> Any:
        """Create a MongoDB-backed LangChain chat message history.

        Args:
            session_id: Chat session identifier.
            database_name: Optional database override.
            collection: Hard-coded collection alias to use.
            kwargs: Additional `MongoDBChatMessageHistory` keyword arguments.

        Returns:
            A `langchain_mongodb.MongoDBChatMessageHistory` instance.
        """
        self._apply_database_collection_kwargs(
            kwargs,
            database_name=database_name,
            collection=collection,
        )
        return self.module.MongoDBChatMessageHistory(
            connection_string=self.connection_string,
            session_id=session_id,
            **kwargs,
        )

    def cache(
        self,
        *,
        database_name: str | None = None,
        collection: MongoDBCollectionName = "cache",
        **kwargs: Any,
    ) -> Any:
        """Create a MongoDB-backed LangChain cache.

        Args:
            database_name: Optional database override.
            collection: Hard-coded collection alias to use.
            kwargs: Additional `MongoDBCache` keyword arguments.

        Returns:
            A `langchain_mongodb.MongoDBCache` instance.
        """
        self._apply_database_collection_kwargs(
            kwargs,
            database_name=database_name,
            collection=collection,
        )
        return self.module.MongoDBCache(
            connection_string=self.connection_string,
            **kwargs,
        )

    def semantic_cache(
        self,
        embedding: Any,
        *,
        database_name: str | None = None,
        collection: MongoDBCollectionName = "semantic_cache",
        index_name: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create a MongoDB Atlas semantic cache.

        Args:
            embedding: LangChain embeddings object to use for cache lookup.
            database_name: Optional database override.
            collection: Hard-coded collection alias to use.
            index_name: Optional Atlas Vector Search index override.
            kwargs: Additional `MongoDBAtlasSemanticCache` keyword arguments.

        Returns:
            A `langchain_mongodb.MongoDBAtlasSemanticCache` instance.
        """
        self._apply_database_collection_kwargs(
            kwargs,
            database_name=database_name,
            collection=collection,
        )
        if index_name is not None:
            kwargs["index_name"] = index_name
        else:
            kwargs.setdefault("index_name", self.settings.vector_search_index)

        return self.module.MongoDBAtlasSemanticCache(
            connection_string=self.connection_string,
            embedding=embedding,
            **kwargs,
        )

    def loader(
        self,
        *,
        database_name: str | None = None,
        collection: MongoDBCollectionName = "documents",
        **kwargs: Any,
    ) -> Any:
        """Create a MongoDB document loader.

        Args:
            database_name: Optional database override.
            collection: Hard-coded collection alias to use.
            kwargs: Additional `MongoDBLoader` keyword arguments.

        Returns:
            A `langchain_mongodb.loaders.MongoDBLoader` instance.
        """
        selected_database, selected_collection = self._database_collection(
            database_name=database_name,
            collection=collection,
            required_for="loader",
        )
        return _import_langchain_mongodb_loader().from_connection_string(
            connection_string=self.connection_string,
            db_name=selected_database,
            collection_name=selected_collection,
            **kwargs,
        )

    def doc_store(
        self,
        *,
        database_name: str | None = None,
        collection: MongoDBCollectionName = "documents",
        **kwargs: Any,
    ) -> Any:
        """Create a MongoDB-backed LangChain docstore.

        Args:
            database_name: Optional database override.
            collection: Hard-coded collection alias to use.
            kwargs: Additional `MongoDBDocStore` keyword arguments.

        Returns:
            A `langchain_mongodb.docstores.MongoDBDocStore` instance.
        """
        return _import_langchain_mongodb_doc_store().from_connection_string(
            connection_string=self.connection_string,
            namespace=self._namespace(
                database_name=database_name,
                collection=collection,
                required_for="docstore",
            ),
            **kwargs,
        )

    def agent_database(
        self,
        *,
        database_name: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create a LangChain MongoDB agent-toolkit database wrapper.

        Args:
            database_name: Optional database override.
            kwargs: Additional `MongoDBDatabase` keyword arguments.

        Returns:
            A `langchain_mongodb.agent_toolkit.database.MongoDBDatabase` instance.

        Raises:
            ConfigurationPortalError: If no database name is configured or supplied.
        """
        selected_database = database_name or self.settings.database_name
        if selected_database is None:
            raise ConfigurationPortalError(
                "LangChain MongoDB agent database requires a database name.",
                details={
                    "client": "langchain_mongodb",
                    "database_configured": False,
                },
            )

        return _import_langchain_mongodb_agent_database().from_connection_string(
            connection_string=self.connection_string,
            database=selected_database,
            **kwargs,
        )

    def _namespace(
        self,
        *,
        database_name: str | None,
        collection: MongoDBCollectionName,
        required_for: str,
    ) -> str:
        """Resolve a MongoDB namespace for namespace-based helpers.

        Args:
            database_name: Optional database override.
            collection: Hard-coded collection alias to resolve.
            required_for: Human-readable helper name for error messages.

        Returns:
            A `database.collection` namespace.
        """
        database, selected_collection = self._database_collection(
            database_name=database_name,
            collection=collection,
            required_for=required_for,
        )
        return f"{database}.{selected_collection}"

    def _database_collection(
        self,
        *,
        database_name: str | None,
        collection: MongoDBCollectionName,
        required_for: str,
    ) -> tuple[str, str]:
        """Resolve configured database and collection names.

        Args:
            database_name: Optional database override.
            collection: Hard-coded collection alias to resolve.
            required_for: Human-readable helper name for error messages.

        Returns:
            The selected database and collection names.

        Raises:
            ConfigurationPortalError: If the database or collection alias is missing.
        """
        database = database_name or self.settings.database_name
        if database is None:
            raise ConfigurationPortalError(
                f"LangChain MongoDB {required_for} requires a database.",
                details={
                    "client": "langchain_mongodb",
                    "database_configured": False,
                    "collection": collection,
                },
            )

        return database, self._collection_name(collection)

    def _apply_database_collection_kwargs(
        self,
        kwargs: dict[str, Any],
        *,
        database_name: str | None,
        collection: MongoDBCollectionName,
    ) -> None:
        """Apply configured database and collection values to constructor kwargs.

        Args:
            kwargs: Mutable keyword-argument mapping to update.
            database_name: Optional database override.
            collection: Hard-coded collection alias to resolve.
        """
        selected_database = database_name or self.settings.database_name
        if selected_database is not None:
            kwargs.setdefault("database_name", selected_database)
        kwargs.setdefault("collection_name", self._collection_name(collection))

    def _collection_name(self, collection: MongoDBCollectionName) -> str:
        """Resolve a hard-coded collection alias to a MongoDB collection name.

        Args:
            collection: Collection alias to resolve.

        Returns:
            The MongoDB collection name assigned to the alias.

        Raises:
            ConfigurationPortalError: If the alias is not configured.
        """
        try:
            return self.settings.collection_name(collection)
        except KeyError as error:
            raise ConfigurationPortalError(
                "LangChain MongoDB collection alias is not configured.",
                details={
                    "client": "langchain_mongodb",
                    "collection": collection,
                    "configured_collections": sorted(self.settings.collections),
                },
                cause=error,
            ) from error


def _create_langchain_mongodb_connectors(settings: Settings) -> MongoDBConnectors:
    """Create LangChain MongoDB connector helpers from runtime settings.

    Args:
        settings: Runtime settings containing MongoDB connector metadata.

    Returns:
        A helper object that creates LangChain MongoDB integration objects.

    Raises:
        ConfigurationPortalError: If the optional dependency or URI is unavailable.
    """
    if not settings.mongodb.configured:
        raise ConfigurationPortalError(
            "LangChain MongoDB connectors require MCP_PORTAL_MONGODB_CONNECTION_STRING.",
            details={"client": "langchain_mongodb"},
        )

    try:
        _import_langchain_mongodb()
    except ImportError as error:
        raise ConfigurationPortalError(
            "LangChain MongoDB connectors are configured but the optional "
            "langchain-mongodb dependency is missing.",
            details={"extra": "mongodb", "client": "langchain_mongodb"},
            cause=error,
        ) from error

    return MongoDBConnectors(settings.mongodb)


def _import_langchain_mongodb() -> Any:
    """Import the LangChain MongoDB integration package lazily.

    Returns:
        The imported `langchain_mongodb` module.
    """
    import langchain_mongodb

    return langchain_mongodb


def _import_langchain_mongodb_loader() -> Any:
    """Import the LangChain MongoDB loader lazily.

    Returns:
        The `MongoDBLoader` class.
    """
    from langchain_mongodb.loaders import MongoDBLoader

    return MongoDBLoader


def _import_langchain_mongodb_doc_store() -> Any:
    """Import the LangChain MongoDB docstore lazily.

    Returns:
        The `MongoDBDocStore` class.
    """
    from langchain_mongodb.docstores import MongoDBDocStore

    return MongoDBDocStore


def _import_langchain_mongodb_agent_database() -> Any:
    """Import the LangChain MongoDB agent database wrapper lazily.

    Returns:
        The `MongoDBDatabase` class.
    """
    from langchain_mongodb.agent_toolkit.database import MongoDBDatabase

    return MongoDBDatabase
