"""Create sanitized audit events and deliver them to configurable sinks."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Protocol

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
        destination_host: Optional normalized outbound hostname.
        egress_method: Optional normalized outbound HTTP method.
        data_classification: Optional classification of released outbound data.
        detected_classification: Optional classification detected before redaction.
        destination_max_classification: Optional destination classification ceiling.
        payload_digest: Optional digest of the original outbound payload.
        findings: Optional stable DLP finding labels without sensitive values.
        purpose: Optional low-cardinality outbound purpose.
        execution_cell_id: Optional single-use execution-cell identifier.
        execution_cell_namespace: Optional namespace bound to the execution cell.
        execution_isolation: Optional in-process or remote isolation boundary.
    """

    occurred_at: Annotated[str, "UTC event timestamp."] = field(
        metadata={"description": "UTC event timestamp."}
    )
    event: Annotated[str, "Lifecycle event type."] = field(
        metadata={"description": "Lifecycle event type."}
    )
    request_id: Annotated[str, "Server-generated correlation identifier."] = field(
        metadata={"description": "Server-generated correlation identifier."}
    )
    tool_name: Annotated[str, "Fully-qualified tool name."] = field(
        metadata={"description": "Fully-qualified tool name."}
    )
    subject: Annotated[str | None, "Authenticated human or workload subject."] = field(
        metadata={"description": "Authenticated human or workload subject."}
    )
    tenant_id: Annotated[str | None, "Trusted tenant partition."] = field(
        metadata={"description": "Trusted tenant partition."}
    )
    client_id: Annotated[str | None, "Calling OAuth client identifier."] = field(
        metadata={"description": "Calling OAuth client identifier."}
    )
    argument_digest: Annotated[str, "SHA-256 digest of canonicalized arguments."] = field(
        metadata={"description": "SHA-256 digest of canonicalized arguments."}
    )
    allowed: Annotated[bool | None, "Optional authorization result."] = field(
        default=None, metadata={"description": "Optional authorization result."}
    )
    reason: Annotated[str | None, "Optional policy reason."] = field(
        default=None, metadata={"description": "Optional policy reason."}
    )
    outcome: Annotated[str | None, "Optional completion outcome."] = field(
        default=None, metadata={"description": "Optional completion outcome."}
    )
    duration_ms: Annotated[float | None, "Optional execution duration."] = field(
        default=None, metadata={"description": "Optional execution duration."}
    )
    destination_host: Annotated[str | None, "Optional normalized outbound hostname."] = field(
        default=None, metadata={"description": "Optional normalized outbound hostname."}
    )
    egress_method: Annotated[str | None, "Optional normalized outbound HTTP method."] = field(
        default=None, metadata={"description": "Optional normalized outbound HTTP method."}
    )
    data_classification: Annotated[
        str | None, "Optional classification of released outbound data."
    ] = field(
        default=None, metadata={"description": "Optional classification of released outbound data."}
    )
    detected_classification: Annotated[
        str | None, "Optional classification detected before redaction."
    ] = field(
        default=None, metadata={"description": "Optional classification detected before redaction."}
    )
    destination_max_classification: Annotated[
        str | None, "Optional destination classification ceiling."
    ] = field(
        default=None, metadata={"description": "Optional destination classification ceiling."}
    )
    payload_digest: Annotated[str | None, "Optional digest of the original outbound payload."] = (
        field(
            default=None,
            metadata={"description": "Optional digest of the original outbound payload."},
        )
    )
    findings: Annotated[
        tuple[str, ...], "Optional stable DLP finding labels without sensitive values."
    ] = field(
        default=(),
        metadata={"description": "Optional stable DLP finding labels without sensitive values."},
    )
    purpose: Annotated[str | None, "Optional low-cardinality outbound purpose."] = field(
        default=None, metadata={"description": "Optional low-cardinality outbound purpose."}
    )
    execution_cell_id: Annotated[str | None, "Optional single-use execution-cell identifier."] = (
        field(
            default=None, metadata={"description": "Optional single-use execution-cell identifier."}
        )
    )
    execution_cell_namespace: Annotated[
        str | None, "Optional namespace bound to the execution cell."
    ] = field(
        default=None, metadata={"description": "Optional namespace bound to the execution cell."}
    )
    execution_isolation: Annotated[
        str | None, "Optional in-process or remote isolation boundary."
    ] = field(
        default=None, metadata={"description": "Optional in-process or remote isolation boundary."}
    )


@dataclass(frozen=True)
class AuditDetails:
    """Optional decision or completion details for an audit event.

    Attributes:
        decision: Optional authorization decision.
        allowed: Optional direct policy result when no `PolicyDecision` is used.
        reason: Optional direct policy decision reason.
        outcome: Optional completion outcome.
        duration_ms: Optional execution duration.
        destination_host: Optional normalized outbound hostname.
        egress_method: Optional outbound HTTP method.
        data_classification: Optional classification of released outbound data.
        detected_classification: Optional classification detected before redaction.
        destination_max_classification: Optional destination classification ceiling.
        payload_digest: Optional outbound payload digest.
        findings: Stable DLP finding labels without sensitive values.
        purpose: Optional low-cardinality outbound purpose.
        execution_cell_id: Optional single-use execution-cell identifier.
        execution_cell_namespace: Optional namespace bound to the execution cell.
        execution_isolation: Optional in-process or remote isolation boundary.
    """

    decision: Annotated[PolicyDecision | None, "Optional authorization decision."] = field(
        default=None, metadata={"description": "Optional authorization decision."}
    )
    allowed: Annotated[
        bool | None, "Optional direct policy result when no `PolicyDecision` is used."
    ] = field(
        default=None,
        metadata={"description": "Optional direct policy result when no `PolicyDecision` is used."},
    )
    reason: Annotated[str | None, "Optional direct policy decision reason."] = field(
        default=None, metadata={"description": "Optional direct policy decision reason."}
    )
    outcome: Annotated[str | None, "Optional completion outcome."] = field(
        default=None, metadata={"description": "Optional completion outcome."}
    )
    duration_ms: Annotated[float | None, "Optional execution duration."] = field(
        default=None, metadata={"description": "Optional execution duration."}
    )
    destination_host: Annotated[str | None, "Optional normalized outbound hostname."] = None
    egress_method: Annotated[str | None, "Optional outbound HTTP method."] = None
    data_classification: Annotated[
        str | None, "Optional classification of released outbound data."
    ] = None
    detected_classification: Annotated[
        str | None, "Optional classification detected before redaction."
    ] = None
    destination_max_classification: Annotated[
        str | None, "Optional destination classification ceiling."
    ] = None
    payload_digest: Annotated[str | None, "Optional outbound payload digest."] = None
    findings: Annotated[tuple[str, ...], "Stable DLP finding labels without sensitive values."] = ()
    purpose: Annotated[str | None, "Optional low-cardinality outbound purpose."] = None
    execution_cell_id: Annotated[str | None, "Optional single-use execution-cell identifier."] = (
        None
    )
    execution_cell_namespace: Annotated[
        str | None, "Optional namespace bound to the execution cell."
    ] = None
    execution_isolation: Annotated[
        str | None, "Optional in-process or remote isolation boundary."
    ] = None


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
        allowed=details.decision.allowed if details.decision else details.allowed,
        reason=details.decision.reason if details.decision else details.reason,
        outcome=details.outcome,
        duration_ms=details.duration_ms,
        destination_host=details.destination_host,
        egress_method=details.egress_method,
        data_classification=details.data_classification,
        detected_classification=details.detected_classification,
        destination_max_classification=details.destination_max_classification,
        payload_digest=details.payload_digest,
        findings=details.findings,
        purpose=details.purpose,
        execution_cell_id=details.execution_cell_id,
        execution_cell_namespace=details.execution_cell_namespace,
        execution_isolation=details.execution_isolation,
    )
