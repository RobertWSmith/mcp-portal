"""Define the opt-in out-of-process namespace provider boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.server.providers.proxy import ProxyProvider

from mcp_portal.namespaces import Namespace


@dataclass(frozen=True)
class RemoteNamespaceProvider:
    """Proxy one namespace to an independently deployed MCP server.

    Attributes:
        client_factory: Factory creating an authenticated FastMCP client.
        cache_ttl_seconds: Duration for caching the remote component catalog.
    """

    client_factory: Callable[[], Client[Any]]
    cache_ttl_seconds: float | None = 300.0

    @classmethod
    def from_transport(
        cls,
        transport: Any,
        *,
        auth: Any = None,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = 300.0,
    ) -> "RemoteNamespaceProvider":
        """Create a remote provider from a FastMCP client transport.

        Args:
            transport: URL, transport, configuration, or remote FastMCP server.
            auth: Optional FastMCP client authentication configuration.
            timeout_seconds: Optional remote request timeout.
            cache_ttl_seconds: Duration for caching the remote catalog.

        Returns:
            Configured remote namespace provider.
        """

        def create_client() -> Client[Any]:
            """Create one remote FastMCP client.

            Returns:
                Client configured for the isolated namespace service.
            """
            return Client(transport, auth=auth, timeout=timeout_seconds)

        return cls(create_client, cache_ttl_seconds)

    def install(self, server: FastMCP, namespace: Namespace) -> None:
        """Install the remote provider using FastMCP's public proxy API.

        Args:
            server: Portal server receiving the proxy provider.
            namespace: Governed local manifest for the remote namespace.
        """
        server.add_provider(
            ProxyProvider(self.client_factory, cache_ttl=self.cache_ttl_seconds),
            namespace=namespace.name,
        )
