from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from mcp_portal.errors import PermissionPortalError


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
        self.quota_backend = quota_backend or MemoryQuotaBackend()

    async def check_quota(self, key: str, rate: float, burst: int) -> None:
        """Reject an invocation when its quota partition is exhausted.

        Args:
            key: Tenant, subject, and tool quota partition.
            rate: Sustained units replenished per second.
            burst: Maximum available units.
        """
        if not await self.quota_backend.consume(key, rate, burst):
            raise PermissionPortalError("Request quota exceeded.", details={"quota_key": key})
