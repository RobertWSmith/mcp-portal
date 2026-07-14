"""Store and manage tenant-owned asynchronous portal tasks in memory."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from mcp_portal.errors import PermissionPortalError, ValidationPortalError

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class PortalTask:
    """Authorization-bound durable-task model, independent of one MCP revision.

    Attributes:
        task_id: Cryptographically random opaque identifier.
        owner: Authenticated task owner.
        tenant_id: Trusted tenant partition.
        status: Current task lifecycle state.
        created_at: UTC creation timestamp.
        expires_at: UTC retention deadline.
        result: Optional terminal or intermediate result.
    """

    task_id: str = field(metadata={"description": "Cryptographically random opaque identifier."})
    owner: str = field(metadata={"description": "Authenticated task owner."})
    tenant_id: str | None = field(metadata={"description": "Trusted tenant partition."})
    status: TaskStatus = field(metadata={"description": "Current task lifecycle state."})
    created_at: datetime = field(metadata={"description": "UTC creation timestamp."})
    expires_at: datetime = field(metadata={"description": "UTC retention deadline."})
    result: Any = field(
        default=None, metadata={"description": "Optional terminal or intermediate result."}
    )


class TaskStore(Protocol):
    """Protocol implemented by authorization-bound task persistence adapters."""

    def create(self, owner: str, tenant_id: str | None, ttl_seconds: int) -> PortalTask:
        """Create a task owned by an authenticated actor and tenant.

        Args:
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.
            ttl_seconds: Requested retention duration.

        Returns:
            Newly created task.
        """
        ...

    def get(self, task_id: str, owner: str, tenant_id: str | None) -> PortalTask:
        """Retrieve a task for its exact authorization context.

        Args:
            task_id: Opaque task identifier.
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.

        Returns:
            Matching task.
        """
        ...

    def update(
        self,
        task_id: str,
        owner: str,
        tenant_id: str | None,
        *,
        status: TaskStatus,
        result: Any = None,
    ) -> PortalTask:
        """Update a task for its exact authorization context.

        Args:
            task_id: Opaque task identifier.
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.
            status: New task lifecycle state.
            result: Optional task result.

        Returns:
            Updated task.
        """
        ...

    def list(self, owner: str, tenant_id: str | None) -> tuple[PortalTask, ...]:
        """List tasks visible to an authorization context.

        Args:
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.

        Returns:
            Visible tasks.
        """
        ...


class MemoryTaskStore:
    """Reference task store with ownership, TTL, and concurrency enforcement."""

    def __init__(self, *, max_ttl_seconds: int = 3600, max_per_owner: int = 10) -> None:
        """Initialize task storage limits.

        Args:
            max_ttl_seconds: Maximum accepted task TTL.
            max_per_owner: Maximum concurrent working tasks per owner.
        """
        self.max_ttl_seconds = max_ttl_seconds
        self.max_per_owner = max_per_owner
        self._tasks: dict[str, PortalTask] = {}

    def create(self, owner: str, tenant_id: str | None, ttl_seconds: int) -> PortalTask:
        """Create an authorization-bound task.

        Args:
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.
            ttl_seconds: Requested retention duration.

        Returns:
            Newly created task.
        """
        self.cleanup()
        if not owner:
            raise PermissionPortalError("Task creation requires an authenticated owner.")
        if ttl_seconds <= 0 or ttl_seconds > self.max_ttl_seconds:
            raise ValidationPortalError("Requested task TTL is outside the supported range.")
        if (
            sum(task.owner == owner and task.status == "working" for task in self._tasks.values())
            >= self.max_per_owner
        ):
            raise PermissionPortalError("Concurrent task limit exceeded.")
        now = datetime.now(timezone.utc)
        task = PortalTask(
            task_id=secrets.token_urlsafe(24),
            owner=owner,
            tenant_id=tenant_id,
            status="working",
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str, owner: str, tenant_id: str | None) -> PortalTask:
        """Retrieve a task only for its exact authorization context.

        Args:
            task_id: Opaque task identifier.
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.

        Returns:
            Matching task.
        """
        self.cleanup()
        task = self._tasks.get(task_id)
        if task is None or task.owner != owner or task.tenant_id != tenant_id:
            raise PermissionPortalError("Task is unavailable to this authorization context.")
        return task

    def update(
        self,
        task_id: str,
        owner: str,
        tenant_id: str | None,
        *,
        status: TaskStatus,
        result: Any = None,
    ) -> PortalTask:
        """Update a task only for its exact authorization context.

        Args:
            task_id: Opaque task identifier.
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.
            status: New task lifecycle state.
            result: Optional task result.

        Returns:
            Updated immutable task value.
        """
        updated = replace(self.get(task_id, owner, tenant_id), status=status, result=result)
        self._tasks[task_id] = updated
        return updated

    def list(self, owner: str, tenant_id: str | None) -> tuple[PortalTask, ...]:
        """List tasks visible to one authorization context.

        Args:
            owner: Authenticated task owner.
            tenant_id: Trusted tenant partition.

        Returns:
            Visible tasks only.
        """
        self.cleanup()
        return tuple(
            task
            for task in self._tasks.values()
            if task.owner == owner and task.tenant_id == tenant_id
        )

    def cleanup(self) -> None:
        """Delete tasks whose configured retention period has elapsed."""
        now = datetime.now(timezone.utc)
        for task_id in [task_id for task_id, task in self._tasks.items() if task.expires_at <= now]:
            del self._tasks[task_id]
