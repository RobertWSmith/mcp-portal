from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp_portal.config import Settings
from mcp_portal.errors import ConfigurationPortalError

ClientFactory = Callable[[], Any]


@dataclass(frozen=True)
class ClientFactories:
    """Registry of lazily constructed external clients.

    Attributes:
        factories: Mapping from client names to zero-argument factories.
        shared_factories: Client names whose created objects are reused until shutdown.
    """

    factories: Mapping[str, ClientFactory] = field(default_factory=dict)
    shared_factories: frozenset[str] = field(default_factory=frozenset)
    _shared_clients: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Normalize factory mappings after dataclass initialization."""
        object.__setattr__(self, "factories", dict(self.factories))
        object.__setattr__(self, "shared_factories", frozenset(self.shared_factories))

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

    def with_factory(
        self,
        name: str,
        factory: ClientFactory,
        *,
        shared: bool = False,
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

        return ClientFactories(factories, frozenset(shared_factories))


def default_client_factories(settings: Settings | None = None) -> ClientFactories:
    """Create the default external client registry.

    Args:
        settings: Optional runtime settings used to register configured backends.

    Returns:
        A client registry ready for namespace-specific injection.
    """
    factories = ClientFactories()
    if settings is not None and settings.database.provider != "none":
        if settings.database.sqlalchemy_configured:
            factories = factories.with_factory(
                "database",
                lambda: _create_sqlalchemy_engine(settings),
                shared=True,
            )

    return factories


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
