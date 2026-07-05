from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp_portal.errors import ConfigurationPortalError

ClientFactory = Callable[[], Any]


@dataclass(frozen=True)
class ClientFactories:
    """Registry of lazily constructed external clients.

    Attributes:
        factories: Mapping from client names to zero-argument factories.
    """

    factories: Mapping[str, ClientFactory] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize factory mappings after dataclass initialization."""
        object.__setattr__(self, "factories", dict(self.factories))

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
        return self.require(name, namespace=namespace)()

    def with_factory(self, name: str, factory: ClientFactory) -> "ClientFactories":
        """Return a copy with one additional client factory.

        Args:
            name: Client factory name.
            factory: Zero-argument factory for the client.

        Returns:
            A new registry containing the added factory.
        """
        factories = dict(self.factories)
        factories[name] = factory
        return ClientFactories(factories)


def default_client_factories() -> ClientFactories:
    """Create the default external client registry.

    Returns:
        An empty client registry ready for namespace-specific injection.
    """
    return ClientFactories()
