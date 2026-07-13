"""Create sanitized audit events and deliver them to configurable sinks."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from mcp_portal.policy import PolicyDecision
from mcp_portal.security import InvocationContext


@dataclass(frozen=True)
class AuditEvent:
    """Sanitized append-only record of a tool lifecycle event.

    Attributes:
        occurred_at: UTC event timestamp.
        event: Lifecycle event type.
        request_id: Server-generated correlation identifier.
        tool_name: Fully-qualified tool name.
        subject: Authenticated human or workload subject.
        tenant_id: Trusted tenant partition.
        client_id: Calling OAuth client identifier.
        argument_digest: SHA-256 digest of canonicalized arguments.
        allowed: Optional authorization result.
        reason: Optional policy reason.
        outcome: Optional completion outcome.
        duration_ms: Optional execution duration.
    """

    occurred_at: str = field(metadata={"description": "UTC event timestamp."})
    event: str = field(metadata={"description": "Lifecycle event type."})
    request_id: str = field(metadata={"description": "Server-generated correlation identifier."})
    tool_name: str = field(metadata={"description": "Fully-qualified tool name."})
    subject: str | None = field(
        metadata={"description": "Authenticated human or workload subject."}
    )
    tenant_id: str | None = field(metadata={"description": "Trusted tenant partition."})
    client_id: str | None = field(metadata={"description": "Calling OAuth client identifier."})
    argument_digest: str = field(
        metadata={"description": "SHA-256 digest of canonicalized arguments."}
    )
    allowed: bool | None = field(
        default=None, metadata={"description": "Optional authorization result."}
    )
    reason: str | None = field(default=None, metadata={"description": "Optional policy reason."})
    outcome: str | None = field(
        default=None, metadata={"description": "Optional completion outcome."}
    )
    duration_ms: float | None = field(
        default=None, metadata={"description": "Optional execution duration."}
    )


@dataclass(frozen=True)
class AuditDetails:
    """Optional decision or completion details for an audit event.

    Attributes:
        decision: Optional authorization decision.
        outcome: Optional completion outcome.
        duration_ms: Optional execution duration.
    """

    decision: PolicyDecision | None = field(
        default=None, metadata={"description": "Optional authorization decision."}
    )
    outcome: str | None = field(
        default=None, metadata={"description": "Optional completion outcome."}
    )
    duration_ms: float | None = field(
        default=None, metadata={"description": "Optional execution duration."}
    )


class AuditSink(Protocol):
    """Destination for immutable audit events."""

    async def append(self, event: AuditEvent) -> None:
        """Append one immutable event.

        Args:
            event: Sanitized event to persist.
        """
        ...


class LoggingAuditSink:
    """JSON audit sink suitable for forwarding into a SIEM collector."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the sink.

        Args:
            logger: Optional dedicated audit logger.
        """
        self.logger = logger or logging.getLogger("mcp_portal.audit")

    async def append(self, event: AuditEvent) -> None:
        """Emit one event as canonical JSON.

        Args:
            event: Sanitized event to emit.
        """
        self.logger.info("portal_audit %s", json.dumps(asdict(event), sort_keys=True))


class MemoryAuditSink:
    """Deterministic audit sink for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize an empty in-memory event collection."""
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        """Append one event to memory.

        Args:
            event: Sanitized event to retain.
        """
        self.events.append(event)


def digest_arguments(arguments: dict[str, Any]) -> str:
    """Hash arguments without retaining their potentially sensitive values.

    Args:
        arguments: Validated invocation arguments.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def audit_event(
    event: str,
    invocation: InvocationContext,
    arguments: dict[str, Any],
    details: AuditDetails | None = None,
) -> AuditEvent:
    """Compose a normalized audit event without raw arguments or credentials.

    Args:
        event: Lifecycle event name.
        invocation: Trusted invocation context.
        arguments: Validated invocation arguments.
        details: Optional authorization or completion details.

    Returns:
        Sanitized audit event.
    """
    identity = invocation.identity
    details = details or AuditDetails()
    return AuditEvent(
        occurred_at=datetime.now(timezone.utc).isoformat(),
        event=event,
        request_id=invocation.request_id,
        tool_name=invocation.tool_name,
        subject=identity.subject,
        tenant_id=identity.tenant_id,
        client_id=identity.client_id,
        argument_digest=digest_arguments(arguments),
        allowed=details.decision.allowed if details.decision else None,
        reason=details.decision.reason if details.decision else None,
        outcome=details.outcome,
        duration_ms=details.duration_ms,
    )
