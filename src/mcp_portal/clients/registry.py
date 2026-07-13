"""Lifecycle-managed client registry with resilience and readiness controls."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp_portal.config import Settings
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

