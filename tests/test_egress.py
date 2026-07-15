"""Test data-aware destination, payload, credential, and audit policy."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_portal.audit import MemoryAuditSink
from mcp_portal.clients import ClientFactories
from mcp_portal.egress import (
    ApprovedEgress,
    EgressPolicy,
    EgressRequest,
    StructuredPayloadInspector,
)
from mcp_portal.errors import PermissionPortalError, ValidationPortalError
from mcp_portal.namespaces import NamespaceDependencies
from mcp_portal.redaction import Redactor
from mcp_portal.security import InvocationContext, InvocationIdentity, invocation_scope
from mcp_portal.testing import create_namespace_test_context


def invocation(
    *, subject: str | None = "alice", client_id: str | None = "desktop"
) -> InvocationContext:
    """Create a verified invocation for egress tests.

    Args:
        subject: Human or workload subject.
        client_id: Verified OAuth client identifier.

    Returns:
        Deterministic invocation context.
    """
    return InvocationContext(
        "request",
        "records_export",
        InvocationIdentity(
            subject=subject,
            client_id=client_id,
            tenant_id="tenant-a",
            scopes=frozenset({"records.read"}),
        ),
        30,
    )


def test_egress_classification_cannot_downgrade_namespace_policy() -> None:
    """Verify caller declarations cannot lower the trusted namespace classification."""
    policy = EgressPolicy(
        allowed_hosts=frozenset({"internal.example.com", "public.example.com"}),
        destination_classifications={
            "internal.example.com": "internal",
            "public.example.com": "public",
        },
    )
    request = EgressRequest(
        destination="https://internal.example.com/v1",
        purpose="records.lookup",
        method="post",
        payload={"record_id": 7},
        data_classification="public",
    )

    allowed = policy.evaluate(invocation(), request, minimum_classification="internal")
    denied = policy.evaluate(
        invocation(),
        EgressRequest(
            destination="https://public.example.com/hook",
            purpose="records.lookup",
            payload={"record_id": 7},
            data_classification="public",
        ),
        minimum_classification="internal",
    )

    assert allowed.allowed is True
    assert allowed.method == "POST"
    assert allowed.data_classification == "internal"
    assert denied.allowed is False
    assert denied.reason == "destination does not permit payload classification"


def test_egress_requires_explicit_host_approval_and_verified_identity() -> None:
    """Verify outbound data fails closed without an allowlist or verified actor/client."""
    request = EgressRequest(
        destination="https://api.example.com/v1",
        purpose="records.lookup",
        payload=None,
    )
    no_allowlist = EgressPolicy(destination_classifications={"api.example.com": "internal"})
    configured = EgressPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        destination_classifications={"api.example.com": "internal"},
    )

    assert (
        no_allowlist.evaluate(invocation(), request, minimum_classification="internal").allowed
        is False
    )
    anonymous = configured.evaluate(
        invocation(subject=None, client_id=None),
        request,
        minimum_classification="internal",
    )
    assert anonymous.allowed is False
    assert anonymous.reason == "verified identity is required for outbound data"


def test_sensitive_fields_block_or_are_removed_before_release() -> None:
    """Verify detected credentials and personal data never reach callbacks unchanged."""
    payload = {
        "api_key": "literal-secret",
        "contact": "alice@example.com",
        "note": "Bearer abcdefghijklmnop",
        "record_id": 7,
    }
    inspector = StructuredPayloadInspector(Redactor.from_secrets(("literal-secret",)))
    blocked_policy = EgressPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        destination_classifications={"api.example.com": "restricted"},
        inspector=inspector,
    )
    redacting_policy = EgressPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        destination_classifications={"api.example.com": "internal"},
        sensitive_field_action="redact",
        inspector=inspector,
    )
    request = EgressRequest(
        destination="https://api.example.com/v1",
        purpose="records.export",
        payload=payload,
    )

    blocked = blocked_policy.evaluate(invocation(), request, minimum_classification="internal")
    redacted = redacting_policy.evaluate(invocation(), request, minimum_classification="internal")

    assert blocked.allowed is False
    assert blocked.detected_classification == "restricted"
    assert redacted.allowed is True
    assert redacted.data_classification == "internal"
    assert set(redacted.findings) == {
        "bearer_token",
        "credential_field",
        "email_address",
    }
    assert redacted.payload == {
        "api_key": "[REDACTED]",
        "contact": "[REDACTED]",
        "note": "[REDACTED]",
        "record_id": 7,
    }
    assert payload["api_key"] == "literal-secret"
    assert "literal-secret" not in repr(redacted)


def test_payload_digest_is_stable_for_non_json_structures() -> None:
    """Verify audit correlation handles mixed keys, sets, and binary values."""
    inspector = StructuredPayloadInspector()
    first = inspector.inspect(
        {1: "numeric", "1": "text", "tags": {"beta", "alpha"}, "blob": b"value"},
        "internal",
    )
    second = inspector.inspect(
        {"blob": b"value", "tags": {"alpha", "beta"}, "1": "text", 1: "numeric"},
        "internal",
    )

    assert first.payload_digest == second.payload_digest
    assert first.detected_classification == "restricted"


def test_egress_rejects_unsafe_urls_methods_purposes_and_audiences() -> None:
    """Verify network and credential metadata is normalized before authorization."""
    policy = EgressPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        destination_classifications={"api.example.com": "internal"},
    )
    with pytest.raises(ValidationPortalError, match="credentials or fragments"):
        policy.validate_url("https://user:secret@api.example.com/v1")
    with pytest.raises(ValidationPortalError, match="credentials or fragments"):
        policy.validate_url("https://api.example.com/v1#secret")
    with pytest.raises(PermissionPortalError, match="Private"):
        policy.validate_url("https://127.0.0.1/v1")
    with pytest.raises(ValidationPortalError, match="method"):
        policy.evaluate(
            invocation(),
            EgressRequest("https://api.example.com", "records.lookup", method="TRACE"),
            minimum_classification="internal",
        )
    with pytest.raises(ValidationPortalError, match="purpose"):
        policy.evaluate(
            invocation(),
            EgressRequest("https://api.example.com", "User supplied purpose"),
            minimum_classification="internal",
        )
    with pytest.raises(PermissionPortalError, match="audience"):
        policy.evaluate(
            invocation(),
            EgressRequest(
                "https://api.example.com/v1",
                "records.lookup",
                credential_audience="https://tokens.example.com",
            ),
            minimum_classification="internal",
        )


class RecordingBroker:
    """Record credential exchange calls for ordering and denial assertions."""

    def __init__(self, audit: MemoryAuditSink) -> None:
        """Initialize the broker.

        Args:
            audit: Audit sink that must receive the allow event first.
        """
        self.audit = audit
        self.calls: list[tuple[InvocationIdentity, str]] = []

    async def credential_for(self, identity: InvocationIdentity, audience: str) -> str:
        """Issue a deterministic credential after observing an allow audit.

        Args:
            identity: Verified caller identity.
            audience: Same-origin downstream audience.

        Returns:
            Deterministic test credential.
        """
        assert self.audit.events[-1].allowed is True
        self.calls.append((identity, audience))
        return "downstream-token"


@pytest.mark.asyncio
async def test_namespace_downstream_audits_before_credentials_and_releases_sanitized_data() -> None:
    """Verify the governed namespace boundary enforces the complete operation order."""
    audit = MemoryAuditSink()
    broker = RecordingBroker(audit)
    policy = EgressPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        destination_classifications={"api.example.com": "internal"},
        sensitive_field_action="redact",
    )
    context = create_namespace_test_context(
        dependencies=NamespaceDependencies(
            clients=ClientFactories({"records_api": object}),
            audit_sink=audit,
            credential_broker=broker,
            egress_policy=policy,
        )
    )
    called: list[ApprovedEgress] = []

    def operation(approved: ApprovedEgress) -> dict[str, Any]:
        called.append(approved)
        return approved.payload

    request = EgressRequest(
        destination="https://api.example.com/v1/records",
        purpose="records.export",
        payload={"email": "alice@example.com", "record_id": 7},
        credential_audience="https://api.example.com",
    )
    with invocation_scope(invocation()):
        result = await context.downstream("records_api", request, operation)

    assert result == {"email": "[REDACTED]", "record_id": 7}
    assert called[0].credential == "downstream-token"
    assert broker.calls[0][1] == "https://api.example.com"
    event = audit.events[0]
    assert event.event == "egress_authorization"
    assert event.allowed is True
    assert event.destination_host == "api.example.com"
    assert event.data_classification == "internal"
    assert event.detected_classification == "confidential"
    assert event.findings == ("personal_identifier",)
    assert "alice@example.com" not in repr(event)


@pytest.mark.asyncio
async def test_namespace_downstream_denial_prevents_credentials_and_execution() -> None:
    """Verify a classification denial stops token exchange and the operation callback."""
    audit = MemoryAuditSink()
    broker = RecordingBroker(audit)
    context = create_namespace_test_context(
        dependencies=NamespaceDependencies(
            clients=ClientFactories({"records_api": object}),
            audit_sink=audit,
            credential_broker=broker,
            egress_policy=EgressPolicy(
                allowed_hosts=frozenset({"public.example.com"}),
                destination_classifications={"public.example.com": "public"},
            ),
        )
    )
    called = False

    def operation(approved: ApprovedEgress) -> None:
        nonlocal called
        _ = approved
        called = True

    request = EgressRequest(
        destination="https://public.example.com/hook",
        purpose="records.export",
        payload={"record_id": 7},
        credential_audience="https://public.example.com",
    )
    with (
        invocation_scope(invocation()),
        pytest.raises(PermissionPortalError, match="not authorized"),
    ):
        await context.downstream("records_api", request, operation)

    assert called is False
    assert broker.calls == []
    assert audit.events[0].allowed is False
    assert audit.events[0].reason == "destination does not permit payload classification"
