from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastmcp import FastMCP

from mcp_portal.config import Settings
from . import health

NamespaceFactory = Callable[[Settings], FastMCP]


@dataclass(frozen=True)
class Namespace:
    """Definition for a mounted FastMCP namespace.

    Attributes:
        name: Prefix used when mounting the namespace into the parent server.
        create: Factory that builds the namespace child server from shared settings.
    """

    name: str
    create: NamespaceFactory


DEFAULT_NAMESPACES: tuple[Namespace, ...] = (
    Namespace("health", health.create_server),
)


def iter_namespaces() -> tuple[Namespace, ...]:
    """Return the namespaces mounted by the default server.

    Returns:
        The default namespace registry.
    """
    return DEFAULT_NAMESPACES
