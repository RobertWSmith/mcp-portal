from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

from fastmcp import FastMCP

from mcp_portal.config import Settings

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


_NAMESPACE_REGISTRY: dict[str, Namespace] = {}
_DISCOVERED = False


def register_namespace(name: str) -> Callable[[NamespaceFactory], NamespaceFactory]:
    """Create a decorator that registers a default namespace factory.

    Args:
        name: Prefix used when mounting the namespace into the parent server.

    Returns:
        A decorator that records the factory and returns it unchanged.
    """

    def decorator(create: NamespaceFactory) -> NamespaceFactory:
        """Register the decorated factory in the default namespace registry.

        Args:
            create: Factory that builds the namespace child server from shared settings.

        Returns:
            The original factory, unchanged.

        Raises:
            ValueError: If another factory has already registered the same namespace name.
        """
        existing = _NAMESPACE_REGISTRY.get(name)
        if existing is not None and existing.create is not create:
            raise ValueError(f"Namespace {name!r} is already registered")

        _NAMESPACE_REGISTRY[name] = Namespace(name, create)
        return create

    return decorator


def _discover_namespace_modules() -> None:
    """Import namespace modules so their registration decorators run."""
    global _DISCOVERED

    if _DISCOVERED:
        return

    for module_name in _iter_namespace_module_names():
        importlib.import_module(module_name)

    _DISCOVERED = True


def _iter_namespace_module_names() -> list[str]:
    """Return importable namespace module names in deterministic order.

    Returns:
        Child module names inside the namespace package.
    """
    module_infos = pkgutil.iter_modules(__path__, prefix=f"{__name__}.")
    return sorted(
        module_info.name
        for module_info in module_infos
        if not module_info.name.rsplit(".", maxsplit=1)[-1].startswith("_")
    )


def iter_namespaces() -> tuple[Namespace, ...]:
    """Return the namespaces mounted by the default server.

    Returns:
        The default namespace registry.
    """
    _discover_namespace_modules()
    return tuple(_NAMESPACE_REGISTRY.values())
