from __future__ import annotations

from dataclasses import replace

import pytest

import mcp_portal.telemetry as telemetry_module
from mcp_portal.clients import ClientFactories
from mcp_portal.config import ObservabilitySettings
from mcp_portal.errors import PermissionPortalError
from mcp_portal.namespaces import NamespaceContext
from mcp_portal.policy import PolicyDecision
from mcp_portal.security import (
    InvocationContext,
    InvocationIdentity,
    invocation_scope,
)
from mcp_portal.server import create_mcp
from mcp_portal.telemetry import (
    MemoryCostSink,
    MemoryTelemetryRecorder,
    OpenTelemetryRecorder,
    UsageRecord,
)
from mcp_portal.testing import create_namespace_test_context, create_test_settings


class DenyTelemetryPolicy:
    """Deny every call for telemetry classification tests."""

    async def authorize(self, invocation, tool, arguments):
        """Return a deterministic policy denial.

        Args:
            invocation: Trusted invocation context.
            tool: Registered tool.
            arguments: Validated tool arguments.

        Returns:
            Denied policy decision.
        """
        _ = invocation, tool, arguments
        return PolicyDecision(False, "telemetry denial")


class FakeInstrument:
    """Capture OpenTelemetry instrument observations."""

    def __init__(self) -> None:
        """Initialize an empty observation sequence."""
        self.observations: list[tuple[float, dict[str, object]]] = []

    def add(self, value: float, attributes: dict[str, object]) -> None:
        """Capture a counter observation.

        Args:
            value: Counter increment.
            attributes: Metric dimensions.
        """
        self.observations.append((value, attributes))

    def record(self, value: float, attributes: dict[str, object]) -> None:
        """Capture a histogram observation.

        Args:
            value: Histogram value.
            attributes: Metric dimensions.
        """
        self.observations.append((value, attributes))


class FakeMeter:
    """Create named fake counters and histograms."""

    def __init__(self) -> None:
        """Initialize an empty instrument mapping."""
        self.instruments: dict[str, FakeInstrument] = {}

    def create_counter(self, name: str, **kwargs) -> FakeInstrument:
        """Create a fake counter.

        Args:
            name: OpenTelemetry instrument name.
            kwargs: Unused instrument metadata.

        Returns:
            Capturing fake instrument.
        """
        _ = kwargs
        return self.instruments.setdefault(name, FakeInstrument())

    def create_histogram(self, name: str, **kwargs) -> FakeInstrument:
        """Create a fake histogram.

        Args:
            name: OpenTelemetry instrument name.
            kwargs: Unused instrument metadata.

        Returns:
            Capturing fake instrument.
        """
        _ = kwargs
        return self.instruments.setdefault(name, FakeInstrument())


def invocation() -> InvocationContext:
    """Create a deterministic tenant-bound invocation.

    Returns:
        Test invocation context.
    """
    return InvocationContext(
        "request-1",
        "billing_generate",
        InvocationIdentity(
            subject="alice",
            tenant_id="tenant-a",
            client_id="dashboard",
            auth_method="bearer",
        ),
        45,
    )


def test_usage_record_is_exact_tenant_bound_and_validated() -> None:
    """Verify accounting records retain exact decimals and trusted ownership."""
    record = UsageRecord.create(
        invocation(),
        "billing",
        provider="azure.ai.openai",
        service="language-model",
        operation="chat",
        sku="gpt-enterprise",
        quantity="1250",
        unit="input_token",
        estimated_cost="0.012500",
        currency="usd",
        pricing_version="contract-2026-07",
    )

    assert record.tenant_id == "tenant-a"
    assert record.quantity == "1250"
    assert record.estimated_cost == "0.012500"
    assert record.currency == "USD"
    assert record.request_id == "request-1"
    with pytest.raises(ValueError, match="negative"):
        UsageRecord.create(
            invocation(),
            "billing",
            provider="provider",
            service="service",
            operation="chat",
            quantity=-1,
            unit="request",
        )


@pytest.mark.asyncio
async def test_open_telemetry_metrics_and_cost_sink_are_both_recorded(monkeypatch) -> None:
    """Verify aggregate metrics and detailed accounting events share one record."""
    meter = FakeMeter()
    sink = MemoryCostSink()
    monkeypatch.setattr(telemetry_module.metrics, "get_meter", lambda *args: meter)
    recorder = OpenTelemetryRecorder(cost_sink=sink, include_tenant_metrics=False)
    record = UsageRecord.create(
        invocation(),
        "billing",
        provider="openai",
        service="language-model",
        operation="chat",
        quantity=50,
        unit="output_token",
        estimated_cost="0.002",
    )

    recorder.record_tool_call(
        invocation(), outcome="succeeded", duration_seconds=2.5, admission_wait_seconds=0.1
    )
    recorder.record_downstream_call(
        "openai", outcome="succeeded", duration_seconds=2, circuit_state="closed"
    )
    await recorder.record_usage(record)

    assert sink.records == [record]
    tool_attributes = meter.instruments["mcp.portal.tool.calls"].observations[0][1]
    assert tool_attributes["mcp.tool.name"] == "billing_generate"
    assert "mcp.tenant.id" not in tool_attributes
    assert meter.instruments["mcp.portal.usage"].observations[0][0] == 50
    assert meter.instruments["mcp.portal.cost.estimated"].observations[0][0] == 0.002


@pytest.mark.asyncio
async def test_namespace_context_records_usage_with_configured_defaults() -> None:
    """Verify namespaces emit accounting records through trusted invocation context."""
    recorder = MemoryTelemetryRecorder()
    settings = replace(
        create_test_settings(),
        observability=ObservabilitySettings(
            cost_currency="EUR", pricing_version="enterprise-contract-v3"
        ),
    )
    context: NamespaceContext = create_namespace_test_context(settings=settings, telemetry=recorder)

    with invocation_scope(invocation()):
        record = await context.record_usage(
            provider="internal",
            service="document-processing",
            operation="extract",
            quantity=4,
            unit="document",
            estimated_cost="0.08",
        )

    assert recorder.usage_records == [record]
    assert record.tenant_id == "tenant-a"
    assert record.currency == "EUR"
    assert record.pricing_version == "enterprise-contract-v3"


@pytest.mark.asyncio
async def test_tool_and_downstream_outcomes_reach_injected_recorder() -> None:
    """Verify active server and client execution paths emit outcome metrics."""
    recorder = MemoryTelemetryRecorder()
    server = create_mcp(create_test_settings(), include_debug_ui=False, telemetry=recorder)

    assert await server.call_tool("health_ping", {})
    assert recorder.tool_calls[-1]["outcome"] == "succeeded"

    denied = create_mcp(
        create_test_settings(),
        include_debug_ui=False,
        telemetry=recorder,
        policy_engine=DenyTelemetryPolicy(),
    )
    with pytest.raises(PermissionPortalError):
        await denied.call_tool("health_ping", {})
    assert recorder.tool_calls[-1]["outcome"] == "denied"

    clients = ClientFactories(telemetry=recorder).with_factory("records", lambda: object())
    assert await clients.execute("records", lambda: "ok") == "ok"
    assert recorder.downstream_calls[-1]["outcome"] == "succeeded"
