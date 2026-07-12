from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TypeVar

import anyio

from mcp_portal.errors import PermissionPortalError, TimeoutPortalError, UpstreamPortalError

T = TypeVar("T")


class QuotaBackend(Protocol):
    """Pluggable quota backend; production deployments can provide Redis or a gateway."""

    async def consume(self, key: str, rate: float, burst: int) -> bool:
        """Attempt to consume one quota unit.

        Args:
            key: Tenant, subject, and tool quota partition.
            rate: Sustained units replenished per second.
            burst: Maximum available units.

        Returns:
            True when the unit was admitted.
        """
        ...


@dataclass
class _Bucket:
    """Mutable token-bucket state.

    Attributes:
        tokens: Currently available units.
        updated_at: Monotonic timestamp of the last refill.
    """

    tokens: float
    updated_at: float


class MemoryQuotaBackend:
    """Process-local token bucket used as a safe reference implementation."""

    def __init__(self) -> None:
        """Initialize empty quota state and its synchronization lock."""
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def consume(self, key: str, rate: float, burst: int) -> bool:
        """Attempt to consume one process-local quota unit.

        Args:
            key: Tenant, subject, and tool quota partition.
            rate: Sustained units replenished per second.
            burst: Maximum available units.

        Returns:
            True when the unit was admitted.
        """
        if rate <= 0:
            return True
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.setdefault(key, _Bucket(float(burst), now))
            bucket.tokens = min(float(burst), bucket.tokens + ((now - bucket.updated_at) * rate))
            bucket.updated_at = now
            if bucket.tokens < 1:
                return False
            bucket.tokens -= 1
            return True


class AdmissionController:
    """Bound concurrent work and enforce subject/tool quota keys."""

    def __init__(self, maximum_concurrency: int, quota_backend: QuotaBackend | None = None) -> None:
        """Initialize request admission controls.

        Args:
            maximum_concurrency: Maximum concurrent in-process invocations.
            quota_backend: Optional shared quota backend.
        """
        self.capacity = asyncio.Semaphore(maximum_concurrency)
        self._tool_capacities: dict[str, tuple[int, asyncio.Semaphore]] = {}
        self.quota_backend = quota_backend or MemoryQuotaBackend()

    @asynccontextmanager
    async def capacity_for(self, tool_name: str, maximum_concurrency: int):
        """Acquire per-tool and global capacity without starving unrelated tools.

        Args:
            tool_name: Fully-qualified MCP tool name.
            maximum_concurrency: Maximum concurrent calls for this tool.

        Yields:
            Control after both admission slots have been acquired.
        """
        configured, tool_capacity = self._tool_capacities.get(
            tool_name,
            (maximum_concurrency, asyncio.Semaphore(maximum_concurrency)),
        )
        if configured != maximum_concurrency:
            raise ValueError(f"Concurrency limit for {tool_name!r} changed after startup")
        self._tool_capacities[tool_name] = (configured, tool_capacity)
        async with tool_capacity, self.capacity:
            yield

    async def check_quota(self, key: str, rate: float, burst: int) -> None:
        """Reject an invocation when its quota partition is exhausted.

        Args:
            key: Tenant, subject, and tool quota partition.
            rate: Sustained units replenished per second.
            burst: Maximum available units.
        """
        if not await self.quota_backend.consume(key, rate, burst):
            raise PermissionPortalError("Request quota exceeded.", details={"quota_key": key})


class CircuitState(str, Enum):
    """Observable downstream circuit-breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Concurrency-safe consecutive-failure circuit breaker for one dependency."""

    def __init__(self, name: str, failure_threshold: int, recovery_seconds: float) -> None:
        """Initialize a closed circuit.

        Args:
            name: Stable downstream dependency name.
            failure_threshold: Consecutive failures required to open the circuit.
            recovery_seconds: Cooldown before one half-open probe is permitted.
        """
        if failure_threshold <= 0 or recovery_seconds <= 0:
            raise ValueError("Circuit-breaker thresholds must be positive")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Return the current circuit state.

        Returns:
            State including cooldown-based half-open eligibility.
        """
        if self._opened_at is None:
            return CircuitState.CLOSED
        if time.monotonic() - self._opened_at >= self.recovery_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    async def execute(
        self,
        operation: Callable[[], T | Awaitable[T]],
        *,
        timeout_seconds: float,
    ) -> T:
        """Execute one downstream operation under timeout and breaker protection.

        Args:
            operation: Zero-argument sync or async downstream operation.
            timeout_seconds: Maximum operation duration.

        Returns:
            The downstream result.
        """
        probe = await self._admit()
        try:
            with anyio.fail_after(timeout_seconds):
                if inspect.iscoroutinefunction(operation):
                    result = await operation()
                else:
                    result = await anyio.to_thread.run_sync(operation)
                if inspect.isawaitable(result):
                    result = await result
        except TimeoutError as error:
            await self._record_failure(probe)
            raise TimeoutPortalError(
                "Downstream operation exceeded its deadline.",
                details={"dependency": self.name},
                cause=error,
            ) from error
        except asyncio.CancelledError:
            await self._release_probe(probe)
            raise
        except Exception:
            await self._record_failure(probe)
            raise
        else:
            await self._record_success()
            return result

    async def _admit(self) -> bool:
        """Reject open circuits or reserve the single half-open probe.

        Returns:
            True when this invocation owns the half-open probe slot.
        """
        async with self._lock:
            state = self.state
            if state is CircuitState.OPEN or (
                state is CircuitState.HALF_OPEN and self._half_open_in_flight
            ):
                raise UpstreamPortalError(
                    "Downstream circuit is open.",
                    details={"dependency": self.name, "state": state.value},
                )
            probe = state is CircuitState.HALF_OPEN
            if probe:
                self._half_open_in_flight = True
            return probe

    async def _record_success(self) -> None:
        """Close the circuit after a successful normal call or probe."""
        async with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    async def _record_failure(self, probe: bool) -> None:
        """Count a failure and open or re-open the circuit when required.

        Args:
            probe: Whether the failed operation was a half-open probe.
        """
        async with self._lock:
            self._failures += 1
            self._half_open_in_flight = False
            if probe or self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    async def _release_probe(self, probe: bool) -> None:
        """Release half-open admission when a caller is cancelled.

        Args:
            probe: Whether the cancelled operation owned the probe slot.
        """
        if probe:
            async with self._lock:
                self._half_open_in_flight = False

    def snapshot(self) -> dict[str, Any]:
        """Return non-secret breaker state for readiness and diagnostics.

        Returns:
            Public breaker state and failure counters.
        """
        return {
            "state": self.state.value,
            "failures": self._failures,
            "failure_threshold": self.failure_threshold,
        }


class CircuitBreakerRegistry:
    """Lazily create and expose circuit breakers by downstream dependency name."""

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 30.0) -> None:
        """Initialize an empty registry.

        Args:
            failure_threshold: Consecutive failures required to open each circuit.
            recovery_seconds: Cooldown before a half-open probe.
        """
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        """Return the stable circuit for a dependency.

        Args:
            name: Registered dependency name.

        Returns:
            The process-local breaker for the dependency.
        """
        return self._breakers.setdefault(
            name,
            CircuitBreaker(name, self.failure_threshold, self.recovery_seconds),
        )

    async def execute(
        self,
        name: str,
        operation: Callable[[], T | Awaitable[T]],
        *,
        timeout_seconds: float,
    ) -> T:
        """Execute an operation through its named dependency circuit.

        Args:
            name: Registered dependency name.
            operation: Zero-argument sync or async downstream operation.
            timeout_seconds: Maximum operation duration.

        Returns:
            The downstream operation result.
        """
        return await self.get(name).execute(operation, timeout_seconds=timeout_seconds)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return deterministic snapshots of all circuits used by this process.

        Returns:
            Dependency names mapped to breaker state.
        """
        return {name: self._breakers[name].snapshot() for name in sorted(self._breakers)}
