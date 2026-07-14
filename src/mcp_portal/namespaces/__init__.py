"""Discover, register, and build isolated MCP namespace providers."""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.types import Annotations, Icon, ToolAnnotations

from mcp_portal.clients import ClientFactories, default_client_factories
from mcp_portal.config import Settings
from mcp_portal.credentials import CredentialBroker, RejectingCredentialBroker
from mcp_portal.egress import EgressPolicy
from mcp_portal.observability import create_telemetry_recorder
from mcp_portal.redaction import Redactor
from mcp_portal.security import InvocationContext, current_invocation
from mcp_portal.services import (
    NamespaceDependencies as NamespaceDependencies,
    PortalServices,
)
from mcp_portal.tasks import MemoryTaskStore, TaskStore
from mcp_portal.telemetry import TelemetryRecorder, UsageMeasurement, UsageRecord
from mcp_portal.tenancy import (
    TenantMongoDBConnectors,
    TenantScope,
    TenantSQLExecutor,
    TenantTaskService,
)

Clock = Callable[[], datetime]
NamespaceHealthCheck = Callable[["NamespaceContext"], "NamespaceStatus"]
NamespaceState = Literal["ok", "warning", "error", "disabled"]
_SEMANTIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+].+)?$")
_DATA_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})
BUILTIN_NAMESPACE_MODULES = ("mcp_portal.namespaces.health",)
NAMESPACE_ENTRY_POINT_GROUP = "mcp_portal.namespaces"


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
    tasks: TaskStore
    telemetry: TelemetryRecorder

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
        return TenantMongoDBConnectors(
            self.clients.create("langchain_mongodb", namespace=self.name), self.tenant_scope()
        )

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
        return await self.credentials.credential_for(
            self.invocation().identity, self.outbound_url(audience)
        )

    async def downstream(
        self,
        client: str,
        operation: Callable[[], Any | Awaitable[Any]],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Execute a downstream call with timeout and circuit-breaker protection.

        Args:
            client: Registered dependency name used for readiness and breaker state.
            operation: Zero-argument sync or async downstream operation.
            timeout_seconds: Optional deadline override for this operation.

        Returns:
            The downstream operation result.
        """
        return await self.clients.execute(
            client,
            operation,
            timeout_seconds=timeout_seconds,
        )

    async def record_usage(
        self,
        measurement: UsageMeasurement,
    ) -> UsageRecord:
        """Capture tenant-scoped usage and estimated cost for this invocation.

        Args:
            measurement: Namespace-reported usage fields.

        Returns:
            The immutable usage record sent to telemetry sinks.
        """
        record = UsageRecord.create(
            self.invocation(),
            UsageMeasurement(
                namespace=self.name,
                provider=measurement.provider,
                service=measurement.service,
                operation=measurement.operation,
                quantity=measurement.quantity,
                unit=measurement.unit,
                sku=measurement.sku,
                estimated_cost=measurement.estimated_cost,
                currency=measurement.currency or self.settings.observability.cost_currency,
                pricing_version=(
                    measurement.pricing_version or self.settings.observability.pricing_version
                ),
            ),
        )
        await self.telemetry.record_usage(record)
        return record


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

    def tool(  # noqa: PLR0913 - declarative API mirrors MCP tool fields
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

    def resource(  # noqa: PLR0913 - declarative API mirrors MCP resource fields
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
class Namespace:
    """Definition for a mounted FastMCP namespace.

    Attributes:
        name: Prefix used when mounting the namespace into the parent server.
        create: Factory that builds the namespace child server from shared context.
        description: Human-readable namespace purpose.
        tags: Stable metadata tags for filtering and documentation.
        health_check: Optional callback that reports namespace status.
    """

    name: str
    create: NamespaceFactory
    description: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    health_check: NamespaceHealthCheck | None = None
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
class NamespaceMetadata:
    """Metadata supplied when registering a namespace factory.

    Attributes:
        name: Prefix used when mounting the namespace.
        description: Human-readable namespace purpose.
        tags: Stable metadata tags.
        health_check: Optional namespace health callback.
        owner: Owning team or service.
        version: Namespace contract version.
        maturity: Namespace lifecycle maturity.
        data_classification: Highest expected data classification.
        required_scopes: Code-owned baseline access scopes.
        timeout_seconds: Optional namespace timeout metadata.
        dependencies: Registered external dependency names.
        deprecation_date: Optional planned deprecation date.
        replacement: Optional replacement namespace name.
    """

    name: str
    description: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    health_check: NamespaceHealthCheck | None = None
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
        """Normalize iterable metadata after dataclass initialization."""
        object.__setattr__(self, "tags", frozenset(self.tags))
        object.__setattr__(self, "required_scopes", frozenset(self.required_scopes))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))


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
    metadata: NamespaceMetadata | str,
) -> Callable[[NamespaceFactory], NamespaceFactory]:
    """Create a decorator that registers a default namespace factory.

    Args:
        metadata: Namespace metadata, or a name for a default metadata record.

    Returns:
        A decorator that records the namespace manifest and returns the factory unchanged.
    """

    definition = NamespaceMetadata(metadata) if isinstance(metadata, str) else metadata

    def decorator(create: NamespaceFactory) -> NamespaceFactory:
        """Register the decorated factory in the default namespace registry.

        Args:
            create: Factory that builds the namespace child server from shared context.

        Returns:
            The original factory, unchanged.

        Raises:
            ValueError: If another factory has already registered the same namespace name.
        """
        existing = _NAMESPACE_REGISTRY.get(definition.name)
        if existing is not None and existing.create is not create:
            raise ValueError(f"Namespace {definition.name!r} is already registered")

        _NAMESPACE_REGISTRY[definition.name] = Namespace(
            name=definition.name,
            create=create,
            description=definition.description,
            tags=definition.tags,
            health_check=definition.health_check,
            owner=definition.owner,
            version=definition.version,
            maturity=definition.maturity,
            data_classification=definition.data_classification,
            required_scopes=definition.required_scopes,
            timeout_seconds=definition.timeout_seconds,
            dependencies=definition.dependencies,
            deprecation_date=definition.deprecation_date,
            replacement=definition.replacement,
        )
        return create

    return decorator


def build_namespace_runtimes(
    namespaces: Sequence[Namespace],
    settings: Settings,
    dependencies: PortalServices | None = None,
) -> tuple[NamespaceRuntime, ...]:
    """Build runtime contexts for a group of namespaces.

    Args:
        namespaces: Namespace manifests to prepare.
        settings: Runtime settings shared by namespaces.
        dependencies: Optional shared namespace service adapters.

    Returns:
        Runtime objects ready for mounting and diagnostics.
    """
    dependencies = dependencies or PortalServices()
    shared_clients = dependencies.clients or default_client_factories(settings)
    shared_redactor = dependencies.redactor or Redactor.from_secrets(
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
    shared_egress = dependencies.egress_policy or EgressPolicy(
        allowed_hosts=frozenset(host.lower() for host in settings.enterprise.egress_allowed_hosts)
    )
    shared_credentials = dependencies.credential_broker or RejectingCredentialBroker()
    shared_tasks = dependencies.task_store or MemoryTaskStore(
        max_ttl_seconds=settings.enterprise.task_max_ttl_seconds,
        max_per_owner=settings.enterprise.task_max_concurrent_per_subject,
    )
    shared_telemetry = dependencies.telemetry or create_telemetry_recorder(settings)
    runtime_dependencies = replace(
        dependencies,
        clients=shared_clients,
        redactor=shared_redactor,
        clock=dependencies.clock,
        egress_policy=shared_egress,
        credential_broker=shared_credentials,
        task_store=shared_tasks,
        telemetry=shared_telemetry,
    )

    return tuple(
        NamespaceRuntime(
            namespace=namespace,
            context=build_namespace_context(
                namespace,
                settings,
                runtime_dependencies,
            ),
        )
        for namespace in namespaces
    )


def build_namespace_context(
    namespace: Namespace,
    settings: Settings,
    dependencies: PortalServices,
) -> NamespaceContext:
    """Build the runtime context for one namespace.

    Args:
        namespace: Namespace manifest.
        settings: Runtime settings shared by namespaces.
        dependencies: Shared namespace service adapters.

    Returns:
        A namespace-scoped runtime context.
    """
    return NamespaceContext(
        name=namespace.name,
        settings=settings,
        logger=logging.getLogger(f"mcp_portal.namespaces.{namespace.name}"),
        redactor=dependencies.redactor or Redactor(),
        clients=dependencies.clients or default_client_factories(settings),
        clock=dependencies.clock or utc_now,
        egress=dependencies.egress_policy or EgressPolicy(),
        credentials=dependencies.credential_broker or RejectingCredentialBroker(),
        tasks=dependencies.task_store
        or MemoryTaskStore(
            max_ttl_seconds=settings.enterprise.task_max_ttl_seconds,
            max_per_owner=settings.enterprise.task_max_concurrent_per_subject,
        ),
        telemetry=dependencies.telemetry or create_telemetry_recorder(settings),
    )


def utc_now() -> datetime:
    """Return the current UTC datetime.

    Returns:
        The current timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


def _discover_namespace_modules(*, strict: bool = False) -> None:
    """Load explicit built-ins and trusted namespace entry points.

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

    _load_namespace_entry_points(strict=strict, logger=logger)

    _DISCOVERED = True


def _load_namespace_entry_points(*, strict: bool, logger: logging.Logger) -> None:
    """Load namespace manifests from the trusted Python entry-point group.

    Args:
        strict: Whether plugin failures should stop discovery.
        logger: Logger receiving safe discovery diagnostics.
    """
    for entry_point in importlib.metadata.entry_points(group=NAMESPACE_ENTRY_POINT_GROUP):
        key = f"entrypoint:{entry_point.name}"
        try:
            loaded = entry_point.load()
            namespace = (
                loaded() if callable(loaded) and not isinstance(loaded, Namespace) else loaded
            )
            if namespace is None:
                continue
            if not isinstance(namespace, Namespace):
                raise TypeError(
                    "namespace entry point must load a Namespace or zero-argument factory"
                )
            existing = _NAMESPACE_REGISTRY.get(namespace.name)
            if existing is not None and existing != namespace:
                raise ValueError(f"Namespace {namespace.name!r} is already registered")
            _NAMESPACE_REGISTRY[namespace.name] = namespace
        except Exception as error:
            if strict:
                raise
            _DISCOVERY_ERRORS[key] = f"{type(error).__name__}: {error}"
            logger.warning("Skipping namespace entry point %s: %s", entry_point.name, error)


def _iter_namespace_module_names() -> list[str]:
    """Return the explicit built-in namespace modules.

    Returns:
        Stable built-in module names.
    """
    return list(BUILTIN_NAMESPACE_MODULES)


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


def validate_namespace_metadata(namespace: Namespace) -> tuple[str, ...]:  # noqa: C901
    """Validate one namespace manifest for governed production contribution.

    Development callers may continue to use manifest defaults. This validator is intended
    for CI and production admission, where ownership and lifecycle metadata must be explicit.

    Args:
        namespace: Namespace manifest to validate.

    Returns:
        Human-readable validation errors, or an empty tuple when valid.
    """
    errors: list[str] = []
    prefix = f"namespace {namespace.name!r}"
    if not namespace.description.strip():
        errors.append(f"{prefix} must declare a description")
    if not namespace.tags:
        errors.append(f"{prefix} must declare at least one catalog tag")
    if not namespace.owner.strip() or namespace.owner == "platform":
        errors.append(f"{prefix} must declare a specific owner")
    if not _SEMANTIC_VERSION.fullmatch(namespace.version):
        errors.append(f"{prefix} version must use semantic versioning")
    if namespace.data_classification not in _DATA_CLASSIFICATIONS:
        errors.append(
            f"{prefix} data classification must be one of "
            f"{', '.join(sorted(_DATA_CLASSIFICATIONS))}"
        )
    if namespace.timeout_seconds is None or namespace.timeout_seconds <= 0:
        errors.append(f"{prefix} must declare a positive timeout")
    if any(not dependency.strip() for dependency in namespace.dependencies):
        errors.append(f"{prefix} dependencies must not contain blank names")
    if len(set(namespace.dependencies)) != len(namespace.dependencies):
        errors.append(f"{prefix} dependencies must be unique")
    if namespace.maturity == "deprecated":
        if not namespace.deprecation_date:
            errors.append(f"{prefix} must declare a deprecation date")
        if not namespace.replacement:
            errors.append(f"{prefix} must declare a replacement")
    elif namespace.deprecation_date or namespace.replacement:
        errors.append(f"{prefix} may use deprecation metadata only when deprecated")
    return tuple(errors)


def validate_namespaces(namespaces: Sequence[Namespace]) -> tuple[str, ...]:
    """Validate a complete namespace catalog for strict CI admission.

    Args:
        namespaces: Complete catalog of namespace manifests.

    Returns:
        Human-readable catalog and manifest validation errors.
    """
    errors: list[str] = []
    names = [namespace.name for namespace in namespaces]
    if len(set(names)) != len(names):
        errors.append("namespace catalog contains duplicate names")
    for namespace in namespaces:
        errors.extend(validate_namespace_metadata(namespace))
    return tuple(errors)
