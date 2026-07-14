"""Define the unified deployment-service composition boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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

    clients: ClientFactories | None = None
    policy_engine: PolicyEngine | None = None
    audit_sink: AuditSink | None = None
    quota_backend: QuotaBackend | None = None
    approval_verifier: ApprovalVerifier | None = None
    task_store: TaskStore | None = None
    telemetry: TelemetryRecorder | None = None
    cost_sink: CostSink | None = None
    credential_broker: CredentialBroker | None = None
    egress_policy: EgressPolicy | None = None
    redactor: Redactor | None = None
    clock: Clock | None = None


# Compatibility aliases for the pre-0.2 composition API. Both names now refer to the
# same container, so no parallel dependency graph can develop.
PortalDependencies = PortalServices
NamespaceDependencies = PortalServices
