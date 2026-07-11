from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp_portal.config import (
    MongoDBCollectionName,
    MongoDBSettings,
    Settings,
)
from mcp_portal.errors import ConfigurationPortalError, TimeoutPortalError, UpstreamPortalError
from mcp_portal.resilience import CircuitBreakerRegistry, CircuitState
from mcp_portal.telemetry import OpenTelemetryRecorder, TelemetryRecorder

ClientFactory = Callable[[], Any]
ReadinessCheck = Callable[[], Any | Awaitable[Any]]


@dataclass(frozen=True)
class ClientFactories:
    """Registry of lazily constructed external clients.

    Attributes:
        factories: Mapping from client names to zero-argument factories.
        shared_factories: Client names whose created objects are reused until shutdown.
    """

    factories: Mapping[str, ClientFactory] = field(default_factory=dict)
    shared_factories: frozenset[str] = field(default_factory=frozenset)
    readiness_checks: Mapping[str, ReadinessCheck] = field(default_factory=dict)
    circuit_breakers: CircuitBreakerRegistry = field(default_factory=CircuitBreakerRegistry)
    downstream_timeout_seconds: float = 45.0
    telemetry: TelemetryRecorder = field(default_factory=OpenTelemetryRecorder)
    _shared_clients: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Normalize factory mappings after dataclass initialization."""
        object.__setattr__(self, "factories", dict(self.factories))
        object.__setattr__(self, "shared_factories", frozenset(self.shared_factories))
        object.__setattr__(self, "readiness_checks", dict(self.readiness_checks))

    def get(self, name: str) -> ClientFactory | None:
        """Return a client factory if one is registered.

        Args:
            name: Client factory name.

        Returns:
            The factory for `name`, or None when absent.
        """
        return self.factories.get(name)

    def require(self, name: str, *, namespace: str | None = None) -> ClientFactory:
        """Return a required client factory or raise a configuration error.

        Args:
            name: Client factory name.
            namespace: Optional namespace requesting the client.

        Returns:
            The registered client factory.

        Raises:
            ConfigurationPortalError: If no factory exists for `name`.
        """
        factory = self.get(name)
        if factory is None:
            raise ConfigurationPortalError(
                f"Client factory {name!r} is not configured",
                namespace=namespace,
                details={"client": name},
            )
        return factory

    def create(self, name: str, *, namespace: str | None = None) -> Any:
        """Construct a named client from its registered factory.

        Args:
            name: Client factory name.
            namespace: Optional namespace requesting the client.

        Returns:
            A newly constructed client object.
        """
        if name in self.shared_factories:
            return self.shared(name, namespace=namespace)

        return self.require(name, namespace=namespace)()

    def shared(self, name: str, *, namespace: str | None = None) -> Any:
        """Return a lifecycle-managed shared client.

        Args:
            name: Client factory name.
            namespace: Optional namespace requesting the client.

        Returns:
            A shared client instance created on first use.
        """
        if name not in self._shared_clients:
            self._shared_clients[name] = self.require(name, namespace=namespace)()

        return self._shared_clients[name]

    async def aclose(self) -> None:
        """Close lifecycle-managed shared clients.

        Shared clients may expose `aclose()`, `close()`, or SQLAlchemy's `dispose()`.
        Awaitable close results are awaited; synchronous close methods are accepted as-is.
        """
        for client in tuple(self._shared_clients.values()):
            close = (
                getattr(client, "aclose", None)
                or getattr(client, "close", None)
                or getattr(client, "dispose", None)
            )
            if close is None:
                continue

            result = close()
            if inspect.isawaitable(result):
                await result

        self._shared_clients.clear()

    async def execute(
        self,
        name: str,
        operation: Callable[[], Any | Awaitable[Any]],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Execute a downstream operation through its named circuit breaker.

        Namespaces should use this boundary for network and database operations so
        repeated upstream failures stop consuming request capacity.

        Args:
            name: Registered downstream dependency name.
            operation: Zero-argument downstream operation.
            timeout_seconds: Optional operation-specific deadline.

        Returns:
            The downstream operation result.
        """
        self.require(name)
        started = time.perf_counter()
        outcome = "succeeded"
        try:
            return await self.circuit_breakers.execute(
                name,
                operation,
                timeout_seconds=timeout_seconds or self.downstream_timeout_seconds,
            )
        except TimeoutPortalError:
            outcome = "timed_out"
            raise
        except UpstreamPortalError:
            outcome = "rejected"
            raise
        except Exception:
            outcome = "failed"
            raise
        finally:
            self.telemetry.record_downstream_call(
                name,
                outcome=outcome,
                duration_seconds=time.perf_counter() - started,
                circuit_state=self.circuit_breakers.get(name).state.value,
            )

    async def check_readiness(self) -> dict[str, dict[str, Any]]:
        """Run registered dependency probes concurrently through their circuits.

        Returns:
            Dependency names mapped to safe readiness and circuit state.
        """

        async def check(name: str, readiness_check: ReadinessCheck) -> tuple[str, dict[str, Any]]:
            """Run one readiness probe and normalize its public result.

            Args:
                name: Registered dependency name.
                readiness_check: Dependency-specific health operation.

            Returns:
                Dependency name paired with public readiness state.
            """
            try:
                await self.circuit_breakers.execute(
                    name,
                    readiness_check,
                    timeout_seconds=self.downstream_timeout_seconds,
                )
            except Exception as error:
                return name, {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "circuit": self.circuit_breakers.get(name).state.value,
                }
            return name, {
                "status": "ok",
                "circuit": self.circuit_breakers.get(name).state.value,
            }

        results = await asyncio.gather(
            *(
                check(name, readiness_check)
                for name, readiness_check in self.readiness_checks.items()
            )
        )
        statuses = dict(sorted(results))
        for name, snapshot in self.circuit_breakers.snapshot().items():
            statuses.setdefault(
                name,
                {
                    "status": ("ok" if snapshot["state"] == CircuitState.CLOSED.value else "error"),
                    "circuit": snapshot["state"],
                },
            )
        return statuses

    def with_factory(
        self,
        name: str,
        factory: ClientFactory,
        *,
        shared: bool = False,
        readiness_check: ReadinessCheck | None = None,
    ) -> "ClientFactories":
        """Return a copy with one additional client factory.

        Args:
            name: Client factory name.
            factory: Zero-argument factory for the client.
            shared: Whether clients from this factory should be reused until shutdown.

        Returns:
            A new registry containing the added factory.
        """
        factories = dict(self.factories)
        factories[name] = factory
        shared_factories = set(self.shared_factories)
        if shared:
            shared_factories.add(name)
        else:
            shared_factories.discard(name)
        readiness_checks = dict(self.readiness_checks)
        if readiness_check is not None:
            readiness_checks[name] = readiness_check

        return ClientFactories(
            factories,
            frozenset(shared_factories),
            readiness_checks,
            self.circuit_breakers,
            self.downstream_timeout_seconds,
            self.telemetry,
        )

    def with_resilience(
        self,
        settings: Settings,
        *,
        telemetry: TelemetryRecorder | None = None,
    ) -> "ClientFactories":
        """Return a registry configured with deployment circuit-breaker policy.

        Args:
            settings: Deployment settings containing resilience defaults.
            telemetry: Optional shared metrics and accounting recorder.

        Returns:
            A copied registry with a fresh configured breaker registry.
        """
        return ClientFactories(
            self.factories,
            self.shared_factories,
            self.readiness_checks,
            CircuitBreakerRegistry(
                settings.enterprise.circuit_breaker_failure_threshold,
                settings.enterprise.circuit_breaker_recovery_seconds,
            ),
            settings.enterprise.downstream_timeout_seconds,
            telemetry or self.telemetry,
        )


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
        selected_namespace = self._namespace(
            database_name=database_name,
            collection=collection,
            required_for="vector search",
        )
        if index_name is not None:
            kwargs["index_name"] = index_name
        else:
            kwargs.setdefault("index_name", self.settings.vector_search_index)

        return self.module.MongoDBAtlasVectorSearch.from_connection_string(
            connection_string=self.connection_string,
            namespace=selected_namespace,
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
        loader_cls = _import_langchain_mongodb_loader()
        return loader_cls.from_connection_string(
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
        selected_namespace = self._namespace(
            database_name=database_name,
            collection=collection,
            required_for="docstore",
        )
        doc_store_cls = _import_langchain_mongodb_doc_store()
        return doc_store_cls.from_connection_string(
            connection_string=self.connection_string,
            namespace=selected_namespace,
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

        database_cls = _import_langchain_mongodb_agent_database()
        return database_cls.from_connection_string(
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


def default_client_factories(
    settings: Settings | None = None,
    *,
    telemetry: TelemetryRecorder | None = None,
) -> ClientFactories:
    """Create the default external client registry.

    Args:
        settings: Optional runtime settings used to register configured backends.
        telemetry: Optional shared metrics and accounting recorder.

    Returns:
        A client registry ready for namespace-specific injection.
    """
    factories = ClientFactories(telemetry=telemetry or OpenTelemetryRecorder())
    if settings is not None:
        factories = factories.with_resilience(settings, telemetry=telemetry)
    if settings is not None and settings.database.provider != "none":
        if settings.database.sqlalchemy_configured:

            def database_ready() -> None:
                """Execute a minimal SQL query through the configured engine."""
                from sqlalchemy import text

                engine = factories.shared("database")
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))

            factories = factories.with_factory(
                "database",
                lambda: _create_sqlalchemy_engine(settings),
                shared=True,
                readiness_check=database_ready,
            )
    if settings is not None and settings.mongodb.configured:

        def mongodb_ready() -> None:
            """Ping MongoDB without retaining an additional readiness client."""
            from pymongo import MongoClient

            client = MongoClient(
                settings.mongodb.connection_string,
                serverSelectionTimeoutMS=int(settings.enterprise.downstream_timeout_seconds * 1000),
            )
            try:
                client.admin.command("ping")
            finally:
                client.close()

        factories = factories.with_factory(
            "langchain_mongodb",
            lambda: _create_langchain_mongodb_connectors(settings),
            shared=True,
            readiness_check=mongodb_ready,
        )

    return factories


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
            "LangChain MongoDB connectors require "
            "MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING.",
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


def _create_sqlalchemy_engine(settings: Settings) -> Any:
    """Create a SQLAlchemy engine from runtime database settings.

    Args:
        settings: Runtime settings containing database metadata.

    Returns:
        A SQLAlchemy Engine.

    Raises:
        ConfigurationPortalError: If SQLAlchemy or the selected dialect is unavailable.
    """
    try:
        create_engine = _import_sqlalchemy_create_engine()
    except ImportError as error:
        raise ConfigurationPortalError(
            "Database backend is configured but the optional SQLAlchemy dependency is missing.",
            details={"extra": "database", "client": "database"},
            cause=error,
        ) from error

    try:
        url, kwargs = _sqlalchemy_engine_configuration(settings)
        return create_engine(url, **kwargs)
    except ConfigurationPortalError:
        raise
    except Exception as error:
        raise ConfigurationPortalError(
            "SQLAlchemy database engine could not be created.",
            details={
                "client": "database",
                "provider": settings.database.provider,
                "sqlalchemy_url_configured": settings.database.sqlalchemy_url is not None,
                "oracle_configured": settings.database.oracle_configured,
            },
            cause=error,
        ) from error


def _import_sqlalchemy_create_engine() -> Any:
    """Import SQLAlchemy's engine factory lazily.

    Returns:
        The `sqlalchemy.create_engine` callable.
    """
    from sqlalchemy import create_engine

    return create_engine


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


def _sqlalchemy_engine_configuration(settings: Settings) -> tuple[str, dict[str, Any]]:
    """Build SQLAlchemy engine URL and keyword arguments.

    Args:
        settings: Runtime settings containing database metadata.

    Returns:
        A SQLAlchemy URL string and keyword arguments for `create_engine`.

    Raises:
        ConfigurationPortalError: If the database settings are incomplete.
    """
    pool_size = settings.database.oracle_pool_min
    max_overflow = max(0, settings.database.oracle_pool_max - settings.database.oracle_pool_min)
    base_kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
    }

    if settings.database.sqlalchemy_url is not None:
        return settings.database.sqlalchemy_url, base_kwargs

    if settings.database.provider == "oracle" and settings.database.oracle_configured:
        return (
            "oracle+oracledb://",
            {
                **base_kwargs,
                "connect_args": {
                    "user": settings.database.oracle_user,
                    "password": settings.database.oracle_password,
                    "dsn": settings.database.oracle_dsn,
                },
            },
        )

    raise ConfigurationPortalError(
        "Database provider requires MCP_PORTAL_DATABASE_SQLALCHEMY_URL or complete "
        "Oracle settings.",
        details={"provider": settings.database.provider, "client": "database"},
    )
