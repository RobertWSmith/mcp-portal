"""Define the unified deployment-service composition boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from datetime import datetime

from mcp_portal.approvals import ApprovalVerifier
from mcp_portal.audit import AuditSink
from mcp_portal.clients import ClientFactories
from mcp_portal.credentials import CredentialBroker
from mcp_portal.egress import EgressPolicy
from mcp_portal.policy import PolicyEngine
from mcp_portal.redaction import Redactor
from mcp_portal.resilience import QuotaBackend
from mcp_portal.tasks import TaskStore
from mcp_portal.telemetry import CostSink, TelemetryRecorder

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PortalServices:
    """Deployment-specific adapters shared by the portal and every namespace.

    Attributes:
        clients: Shared lifecycle-managed external client registry.
        policy_engine: Central authorization policy decision point.
        audit_sink: Append-only security audit destination.
        quota_backend: Shared request-quota backend.
        approval_verifier: Single-use out-of-band approval verifier.
        task_store: Authorization-bound durable task store.
        telemetry: Metrics and cost-accounting recorder.
        cost_sink: Detailed usage and cost destination.
        credential_broker: Audience-bound downstream credential broker.
        egress_policy: Outbound destination policy.
        redactor: Diagnostic redaction service.
        clock: Injectable UTC clock for namespace code.
    """

    clients: Annotated[
        ClientFactories | None, "Shared lifecycle-managed external client registry."
    ] = None
    policy_engine: Annotated[
        PolicyEngine | None, "Central authorization policy decision point."
    ] = None
    audit_sink: Annotated[AuditSink | None, "Append-only security audit destination."] = None
    quota_backend: Annotated[QuotaBackend | None, "Shared request-quota backend."] = None
    approval_verifier: Annotated[
        ApprovalVerifier | None, "Single-use out-of-band approval verifier."
    ] = None
    task_store: Annotated[TaskStore | None, "Authorization-bound durable task store."] = None
    telemetry: Annotated[TelemetryRecorder | None, "Metrics and cost-accounting recorder."] = None
    cost_sink: Annotated[CostSink | None, "Detailed usage and cost destination."] = None
    credential_broker: Annotated[
        CredentialBroker | None, "Audience-bound downstream credential broker."
    ] = None
    egress_policy: Annotated[EgressPolicy | None, "Outbound destination policy."] = None
    redactor: Annotated[Redactor | None, "Diagnostic redaction service."] = None
    clock: Annotated[Clock | None, "Injectable UTC clock for namespace code."] = None


# Compatibility aliases for the pre-0.2 composition API. Both names now refer to the
# same container, so no parallel dependency graph can develop.
PortalDependencies = PortalServices
NamespaceDependencies = PortalServices
