from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.redaction import Redactor

Clock = Callable[[], datetime]
NamespaceFactory = Callable[["NamespaceContext"], FastMCP]
NamespaceHealthCheck = Callable[["NamespaceContext"], "NamespaceStatus"]
NamespaceDebugFactory = Callable[["NamespaceContext"], "NamespaceDebugPanel"]
NamespaceState = Literal["ok", "warning", "error", "disabled"]


@dataclass(frozen=True)
class NamespaceContext:
    """Runtime services shared with a namespace.

    Attributes:
        name: Namespace prefix used when mounting tools.
        settings: Shared runtime settings.
        logger: Logger scoped to this namespace.
        redactor: Redactor used before exposing diagnostics.
        clients: Registry of external client factories.
        clock: Time provider used by tools and tests.
    """

    name: str
    settings: Settings
    logger: logging.Logger
    redactor: Redactor
    clients: ClientFactories
    clock: Clock

    def now(self) -> datetime:
        """Return the current time from the namespace clock.

        Returns:
            The current timezone-aware datetime.
        """
        return self.clock()

    def public_snapshot(self, value: Any) -> Any:
        """Redact a diagnostic value before exposing it.

        Args:
            value: Diagnostic value to redact.

        Returns:
            A public-safe copy of the value.
        """
        return self.redactor.redact(value)


@dataclass(frozen=True)
class NamespaceStatus:
    """Public status reported by a namespace.

    Attributes:
        state: Machine-readable namespace state.
        message: Human-readable status summary.
        details: Optional redacted-safe diagnostic details.
    """

    state: NamespaceState
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize status details after dataclass initialization."""
        object.__setattr__(self, "details", dict(self.details))

    def to_public_dict(self, redactor: Redactor) -> dict[str, Any]:
        """Return this status as a redacted dictionary.

        Args:
            redactor: Redactor used for diagnostic details.

        Returns:
            Public status metadata.
        """
        return {
            "state": self.state,
            "message": self.message,
            "details": redactor.redact(self.details),
        }


@dataclass(frozen=True)
class NamespaceDebugPanel:
    """Debug payload contributed by a namespace.

    Attributes:
        title: Display title for the debug panel.
        summary: Short human-readable panel summary.
        snapshot: Namespace-specific diagnostic payload.
    """

    title: str
    summary: str
    snapshot: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize debug snapshots after dataclass initialization."""
        object.__setattr__(self, "snapshot", dict(self.snapshot))

    def to_public_dict(self, redactor: Redactor) -> dict[str, Any]:
        """Return this debug panel as a redacted dictionary.

        Args:
            redactor: Redactor used for diagnostic payloads.

        Returns:
            Public debug panel metadata.
        """
        return {
            "title": self.title,
            "summary": self.summary,
            "snapshot": redactor.redact(self.snapshot),
        }


@dataclass(frozen=True)
class Namespace:
    """Definition for a mounted FastMCP namespace.

    Attributes:
        name: Prefix used when mounting the namespace into the parent server.
        create: Factory that builds the namespace child server from shared context.
        description: Human-readable namespace purpose.
        tags: Stable metadata tags for filtering and documentation.
        health_check: Optional callback that reports namespace status.
        debug: Optional callback that contributes namespace debug details.
    """

    name: str
    create: NamespaceFactory
    description: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    health_check: NamespaceHealthCheck | None = None
    debug: NamespaceDebugFactory | None = None

    def __post_init__(self) -> None:
        """Normalize namespace metadata after dataclass initialization."""
        object.__setattr__(self, "tags", frozenset(self.tags))
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"Invalid namespace name {self.name!r}")


@dataclass(frozen=True)
class NamespaceRuntime:
    """Pair a namespace manifest with its runtime context.

    Attributes:
        namespace: Namespace manifest.
        context: Runtime context built for the namespace.
    """

    namespace: Namespace
    context: NamespaceContext


_NAMESPACE_REGISTRY: dict[str, Namespace] = {}
_DISCOVERY_ERRORS: dict[str, str] = {}
_DISCOVERED = False


def register_namespace(
    name: str,
    *,
    description: str = "",
    tags: Iterable[str] = (),
    health_check: NamespaceHealthCheck | None = None,
    debug: NamespaceDebugFactory | None = None,
) -> Callable[[NamespaceFactory], NamespaceFactory]:
    """Create a decorator that registers a default namespace factory.

    Args:
        name: Prefix used when mounting the namespace into the parent server.
        description: Human-readable namespace purpose.
        tags: Stable metadata tags for filtering and documentation.
        health_check: Optional callback that reports namespace status.
        debug: Optional callback that contributes namespace debug details.

    Returns:
        A decorator that records the namespace manifest and returns the factory unchanged.
    """

    def decorator(create: NamespaceFactory) -> NamespaceFactory:
        """Register the decorated factory in the default namespace registry.

        Args:
            create: Factory that builds the namespace child server from shared context.

        Returns:
            The original factory, unchanged.

        Raises:
            ValueError: If another factory has already registered the same namespace name.
        """
        existing = _NAMESPACE_REGISTRY.get(name)
        if existing is not None and existing.create is not create:
            raise ValueError(f"Namespace {name!r} is already registered")

        _NAMESPACE_REGISTRY[name] = Namespace(
            name=name,
            create=create,
            description=description,
            tags=frozenset(tags),
            health_check=health_check,
            debug=debug,
        )
        return create

    return decorator


def build_namespace_runtimes(
    namespaces: Sequence[Namespace],
    settings: Settings,
    *,
    clients: ClientFactories | None = None,
    redactor: Redactor | None = None,
    clock: Clock | None = None,
) -> tuple[NamespaceRuntime, ...]:
    """Build runtime contexts for a group of namespaces.

    Args:
        namespaces: Namespace manifests to prepare.
        settings: Runtime settings shared by namespaces.
        clients: Optional shared client factory registry.
        redactor: Optional shared redactor.
        clock: Optional shared clock.

    Returns:
        Runtime objects ready for mounting and diagnostics.
    """
    shared_clients = clients or default_client_factories(settings)
    shared_redactor = redactor or Redactor.from_secrets(
        (
            settings.openai.api_key,
            settings.auth.static_token,
            settings.auth.jwt_public_key,
            settings.database.sqlalchemy_url,
            settings.database.oracle_password,
            settings.mongodb.connection_string,
        )
    )

    return tuple(
        NamespaceRuntime(
            namespace=namespace,
            context=build_namespace_context(
                namespace,
                settings,
                clients=shared_clients,
                redactor=shared_redactor,
                clock=clock,
            ),
        )
        for namespace in namespaces
    )


def build_namespace_context(
    namespace: Namespace,
    settings: Settings,
    *,
    clients: ClientFactories,
    redactor: Redactor,
    clock: Clock | None = None,
) -> NamespaceContext:
    """Build the runtime context for one namespace.

    Args:
        namespace: Namespace manifest.
        settings: Runtime settings shared by namespaces.
        clients: Shared client factory registry.
        redactor: Shared diagnostic redactor.
        clock: Optional namespace clock.

    Returns:
        A namespace-scoped runtime context.
    """
    return NamespaceContext(
        name=namespace.name,
        settings=settings,
        logger=logging.getLogger(f"mcp_portal.namespaces.{namespace.name}"),
        redactor=redactor,
        clients=clients,
        clock=clock or utc_now,
    )


def utc_now() -> datetime:
    """Return the current UTC datetime.

    Returns:
        The current timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


def _discover_namespace_modules(*, strict: bool = False) -> None:
    """Import namespace modules so their registration decorators run.

    Args:
        strict: Whether import failures should be raised instead of recorded.
    """
    global _DISCOVERED

    if _DISCOVERED:
        return

    logger = logging.getLogger(__name__)
    _DISCOVERY_ERRORS.clear()
    for module_name in _iter_namespace_module_names():
        try:
            importlib.import_module(module_name)
        except ImportError as error:
            if strict:
                raise
            _DISCOVERY_ERRORS[module_name] = f"{type(error).__name__}: {error}"
            logger.warning("Skipping namespace module %s: %s", module_name, error)

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


def iter_namespace_discovery_errors() -> dict[str, str]:
    """Return namespace import errors recorded during discovery.

    Returns:
        Mapping of module names to public import error summaries.
    """
    _discover_namespace_modules()
    return dict(sorted(_DISCOVERY_ERRORS.items()))


def iter_namespaces(*, strict: bool = False) -> tuple[Namespace, ...]:
    """Return the namespaces mounted by the default server.

    Args:
        strict: Whether namespace import failures should stop discovery.

    Returns:
        The default namespace registry in deterministic order.
    """
    _discover_namespace_modules(strict=strict)
    return tuple(_NAMESPACE_REGISTRY[name] for name in sorted(_NAMESPACE_REGISTRY))
