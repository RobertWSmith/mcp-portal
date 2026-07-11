from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.types import Annotations, Icon, ToolAnnotations

from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.credentials import CredentialBroker, RejectingCredentialBroker
from mcp_portal.egress import EgressPolicy
from mcp_portal.redaction import Redactor
from mcp_portal.security import InvocationContext, current_invocation
from mcp_portal.tasks import MemoryTaskStore
from mcp_portal.tenancy import (
    TenantMongoDBConnectors,
    TenantScope,
    TenantSQLExecutor,
    TenantTaskService,
)

Clock = Callable[[], datetime]
NamespaceHealthCheck = Callable[["NamespaceContext"], "NamespaceStatus"]
NamespaceDebugFactory = Callable[["NamespaceContext"], "NamespaceDebugPanel"]
NamespaceState = Literal["ok", "warning", "error", "disabled"]


@dataclass(frozen=True)
class NamespaceContext:
    """Runtime services shared with a namespace.

    Attributes:
        name: Namespace prefix used when mounting provider components.
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
    egress: EgressPolicy
    credentials: CredentialBroker
    tasks: MemoryTaskStore

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

    def invocation(self) -> InvocationContext:
        """Return trusted invocation state for tenant-aware namespace operations.

        Returns:
            Current request's trusted invocation context.
        """
        invocation = current_invocation()
        if invocation is None:
            raise RuntimeError("Invocation context is unavailable outside a tool request")
        return invocation

    def tenant_scope(self) -> TenantScope:
        """Return storage partitions derived from verified invocation identity.

        Returns:
            Trusted tenant partition helper.
        """
        return TenantScope.from_invocation(
            self.invocation(), require_tenant=self.settings.enterprise.require_tenant
        )

    def tenant_tasks(self) -> TenantTaskService:
        """Return a task façade bound to the verified owner and tenant.

        Returns:
            Invocation-bound task service.
        """
        return TenantTaskService(self.tasks, self.tenant_scope())

    def tenant_sql(self) -> TenantSQLExecutor:
        """Return SQL execution checks bound to the verified tenant partition.

        Returns:
            Tenant-aware SQLAlchemy execution helper.
        """
        return TenantSQLExecutor(self.tenant_scope())

    def mongodb(self) -> TenantMongoDBConnectors:
        """Return tenant-partitioned LangChain MongoDB connectors.

        Returns:
            Tenant-safe MongoDB connector façade.
        """
        connectors = self.clients.create("langchain_mongodb", namespace=self.name)
        return TenantMongoDBConnectors(connectors, self.tenant_scope())

    def outbound_url(self, url: str) -> str:
        """Validate an outbound URL against the namespace egress policy.

        Args:
            url: Candidate HTTPS destination.

        Returns:
            Approved normalized destination.
        """
        return self.egress.validate_url(url)

    async def downstream_credential(self, audience: str) -> str:
        """Request an audience-bound credential for the verified caller.

        Args:
            audience: Exact downstream HTTPS resource URI.

        Returns:
            Broker-issued downstream credential.
        """
        approved_audience = self.outbound_url(audience)
        return await self.credentials.credential_for(self.invocation().identity, approved_audience)


@dataclass(frozen=True)
class ToolContribution:
    """Declarative tool registration owned by a namespace provider.

    Attributes:
        function: Callable implementing the tool.
        name: Optional unqualified MCP tool name.
        title: Optional human-readable title.
        description: Optional public description.
        annotations: Standard MCP tool behavior hints.
        icons: Optional client-display icons.
        meta: Portal-specific governance metadata.
        structured_output: Optional structured-output override.
    """

    function: Callable[..., Any]
    name: str | None = None
    title: str | None = None
    description: str | None = None
    annotations: ToolAnnotations | None = None
    icons: tuple[Icon, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)
    structured_output: bool | None = None


@dataclass(frozen=True)
class ResourceContribution:
    """Declarative resource or resource-template registration.

    Attributes:
        function: Callable that reads or renders the resource.
        uri: Stable resource URI or URI template.
        name: Optional unqualified display name.
        title: Optional human-readable title.
        description: Optional public description.
        mime_type: Optional content MIME type.
        icons: Optional client-display icons.
        annotations: Standard MCP resource hints.
        meta: Portal-specific governance metadata.
    """

    function: Callable[..., Any]
    uri: str
    name: str | None = None
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    icons: tuple[Icon, ...] = ()
    annotations: Annotations | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_template(self) -> bool:
        """Report whether the contribution URI contains template parameters.

        Returns:
            True when the URI contains a template placeholder.
        """
        return "{" in self.uri and "}" in self.uri


@dataclass(frozen=True)
class PromptContribution:
    """Declarative prompt registration owned by a namespace provider.

    Attributes:
        function: Callable that renders the prompt.
        name: Optional unqualified MCP prompt name.
        title: Optional human-readable title.
        description: Optional public description.
        icons: Optional client-display icons.
    """

    function: Callable[..., Any]
    name: str | None = None
    title: str | None = None
    description: str | None = None
    icons: tuple[Icon, ...] = ()


class NamespaceProvider:
    """Collect every MCP primitive contributed by one namespace.

    Namespace modules use this object instead of constructing a child FastMCP server.
    The portal can therefore mount tools, resources, resource templates, and prompts
    through public server registration APIs while applying one governance boundary.
    """

    def __init__(self, title: str) -> None:
        """Initialize an empty provider.

        Args:
            title: Human-readable provider name used in diagnostics and tests.
        """
        self.title = title
        self._tools: list[ToolContribution] = []
        self._resources: list[ResourceContribution] = []
        self._prompts: list[PromptContribution] = []

    @property
    def tools(self) -> tuple[ToolContribution, ...]:
        """Return tool contributions in registration order.

        Returns:
            Immutable tool contribution sequence.
        """
        return tuple(self._tools)

    @property
    def resources(self) -> tuple[ResourceContribution, ...]:
        """Return resources and resource templates in registration order.

        Returns:
            Immutable resource contribution sequence.
        """
        return tuple(self._resources)

    @property
    def prompts(self) -> tuple[PromptContribution, ...]:
        """Return prompt contributions in registration order.

        Returns:
            Immutable prompt contribution sequence.
        """
        return tuple(self._prompts)

    def tool(
        self,
        name: str | None = None,
        *,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: Iterable[Icon] = (),
        meta: Mapping[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a namespace tool contribution.

        Args:
            name: Optional unqualified tool name.
            title: Optional human-readable title.
            description: Optional public description override.
            annotations: Standard MCP tool behavior hints.
            icons: Optional client-display icons.
            meta: Portal-specific governance metadata.
            structured_output: Optional structured-output override.

        Returns:
            Decorator that records the contributed callable.
        """

        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            """Record a tool callable.

            Args:
                function: Tool implementation to contribute.

            Returns:
                The original callable unchanged.
            """
            self._tools.append(
                ToolContribution(
                    function=function,
                    name=name,
                    title=title,
                    description=description,
                    annotations=annotations,
                    icons=tuple(icons),
                    meta=dict(meta or {}),
                    structured_output=structured_output,
                )
            )
            return function

        return decorator

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        icons: Iterable[Icon] = (),
        annotations: Annotations | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a static resource or URI-template contribution.

        Args:
            uri: Stable resource URI or URI template.
            name: Optional unqualified resource name.
            title: Optional human-readable title.
            description: Optional public description.
            mime_type: Optional content MIME type.
            icons: Optional client-display icons.
            annotations: Standard MCP resource hints.
            meta: Portal-specific governance metadata.

        Returns:
            Decorator that records the contributed callable.
        """

        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            """Record a resource callable.

            Args:
                function: Resource reader or renderer to contribute.

            Returns:
                The original callable unchanged.
            """
            self._resources.append(
                ResourceContribution(
                    function=function,
                    uri=uri,
                    name=name,
                    title=title,
                    description=description,
                    mime_type=mime_type,
                    icons=tuple(icons),
                    annotations=annotations,
                    meta=dict(meta or {}),
                )
            )
            return function

        return decorator

    def prompt(
        self,
        name: str | None = None,
        *,
        title: str | None = None,
        description: str | None = None,
        icons: Iterable[Icon] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a user-controlled prompt contribution.

        Args:
            name: Optional unqualified prompt name.
            title: Optional human-readable title.
            description: Optional public description.
            icons: Optional client-display icons.

        Returns:
            Decorator that records the contributed callable.
        """

        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            """Record a prompt callable.

            Args:
                function: Prompt renderer to contribute.

            Returns:
                The original callable unchanged.
            """
            self._prompts.append(
                PromptContribution(
                    function=function,
                    name=name,
                    title=title,
                    description=description,
                    icons=tuple(icons),
                )
            )
            return function

        return decorator


NamespaceFactory = Callable[["NamespaceContext"], NamespaceProvider]


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
    owner: str = "platform"
    version: str = "1.0.0"
    maturity: Literal["experimental", "beta", "stable", "deprecated"] = "stable"
    data_classification: str = "internal"
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float | None = None
    dependencies: tuple[str, ...] = ()
    deprecation_date: str | None = None
    replacement: str | None = None

    def __post_init__(self) -> None:
        """Normalize namespace metadata after dataclass initialization."""
        object.__setattr__(self, "tags", frozenset(self.tags))
        object.__setattr__(self, "required_scopes", frozenset(self.required_scopes))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
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
    owner: str = "platform",
    version: str = "1.0.0",
    maturity: Literal["experimental", "beta", "stable", "deprecated"] = "stable",
    data_classification: str = "internal",
    required_scopes: Iterable[str] = (),
    timeout_seconds: float | None = None,
    dependencies: Iterable[str] = (),
    deprecation_date: str | None = None,
    replacement: str | None = None,
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
            owner=owner,
            version=version,
            maturity=maturity,
            data_classification=data_classification,
            required_scopes=frozenset(required_scopes),
            timeout_seconds=timeout_seconds,
            dependencies=tuple(dependencies),
            deprecation_date=deprecation_date,
            replacement=replacement,
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
    egress: EgressPolicy | None = None,
    credentials: CredentialBroker | None = None,
    tasks: MemoryTaskStore | None = None,
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
            settings.azure_identity.client_secret,
            settings.auth.static_token,
            settings.auth.jwt_public_key,
            settings.auth.ldap_bind_password,
            settings.database.sqlalchemy_url,
            settings.database.oracle_password,
            settings.mongodb.connection_string,
        )
    )
    shared_egress = egress or EgressPolicy(
        allowed_hosts=frozenset(host.lower() for host in settings.enterprise.egress_allowed_hosts)
    )
    shared_credentials = credentials or RejectingCredentialBroker()
    shared_tasks = tasks or MemoryTaskStore(
        max_ttl_seconds=settings.enterprise.task_max_ttl_seconds,
        max_per_owner=settings.enterprise.task_max_concurrent_per_subject,
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
                egress=shared_egress,
                credentials=shared_credentials,
                tasks=shared_tasks,
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
    egress: EgressPolicy | None = None,
    credentials: CredentialBroker | None = None,
    tasks: MemoryTaskStore | None = None,
) -> NamespaceContext:
    """Build the runtime context for one namespace.

    Args:
        namespace: Namespace manifest.
        settings: Runtime settings shared by namespaces.
        clients: Shared client factory registry.
        redactor: Shared diagnostic redactor.
        clock: Optional namespace clock.
        egress: Optional outbound destination policy.
        credentials: Optional downstream credential broker.
        tasks: Optional authorization-bound task store.

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
        egress=egress or EgressPolicy(),
        credentials=credentials or RejectingCredentialBroker(),
        tasks=tasks
        or MemoryTaskStore(
            max_ttl_seconds=settings.enterprise.task_max_ttl_seconds,
            max_per_owner=settings.enterprise.task_max_concurrent_per_subject,
        ),
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
