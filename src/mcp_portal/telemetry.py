from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from opentelemetry import metrics

from mcp_portal.security import InvocationContext


@dataclass(frozen=True)
class UsageRecord:
    """Detailed tenant-scoped usage and estimated-cost accounting record.

    Attributes:
        occurred_at: UTC event timestamp.
        request_id: Server-generated invocation identifier.
        tool_name: Fully-qualified MCP tool name.
        namespace: Namespace reporting consumption.
        subject: Authenticated human or workload subject.
        tenant_id: Trusted tenant partition.
        client_id: Calling OAuth client identifier.
        provider: External provider or internal cost center.
        service: Metered service or product family.
        operation: Low-cardinality operation name.
        sku: Optional model, deployment, or provider SKU.
        quantity: Metered quantity represented as an exact decimal string.
        unit: Unit such as input_token, request, document, or compute_second.
        estimated_cost: Optional estimated cost represented as an exact decimal string.
        currency: ISO-style currency code for estimated cost.
        pricing_version: Pricing table or contract version used for the estimate.
    """

    occurred_at: str
    request_id: str
    tool_name: str
    namespace: str
    subject: str | None
    tenant_id: str | None
    client_id: str | None
    provider: str
    service: str
    operation: str
    sku: str | None
    quantity: str
    unit: str
    estimated_cost: str | None
    currency: str
    pricing_version: str | None

    @classmethod
    def create(
        cls,
        invocation: InvocationContext,
        namespace: str,
        *,
        provider: str,
        service: str,
        operation: str,
        quantity: int | float | Decimal | str,
        unit: str,
        sku: str | None = None,
        estimated_cost: int | float | Decimal | str | None = None,
        currency: str = "USD",
        pricing_version: str | None = None,
    ) -> "UsageRecord":
        """Create a validated accounting record from trusted invocation context.

        Args:
            invocation: Current trusted tool invocation.
            namespace: Namespace reporting the usage.
            provider: External provider or internal cost center.
            service: Metered service or product family.
            operation: Low-cardinality operation name.
            quantity: Consumed quantity.
            unit: Unit of the consumed quantity.
            sku: Optional model, deployment, or provider SKU.
            estimated_cost: Optional estimated monetary cost.
            currency: Currency code for the estimate.
            pricing_version: Pricing table or contract version.

        Returns:
            Validated immutable usage record.

        Raises:
            ValueError: If required labels are blank or numeric values are negative.
        """
        quantity_value = Decimal(str(quantity))
        cost_value = Decimal(str(estimated_cost)) if estimated_cost is not None else None
        labels = (namespace, provider, service, operation, unit, currency)
        if any(not value.strip() for value in labels):
            raise ValueError("Usage accounting labels must not be blank")
        if quantity_value < 0 or (cost_value is not None and cost_value < 0):
            raise ValueError("Usage quantity and estimated cost must not be negative")
        identity = invocation.identity
        return cls(
            occurred_at=datetime.now(timezone.utc).isoformat(),
            request_id=invocation.request_id,
            tool_name=invocation.tool_name,
            namespace=namespace,
            subject=identity.subject,
            tenant_id=identity.tenant_id,
            client_id=identity.client_id,
            provider=provider,
            service=service,
            operation=operation,
            sku=sku,
            quantity=str(quantity_value),
            unit=unit,
            estimated_cost=str(cost_value) if cost_value is not None else None,
            currency=currency.upper(),
            pricing_version=pricing_version,
        )


class CostSink(Protocol):
    """Append-only destination for detailed usage and cost records."""

    async def append(self, record: UsageRecord) -> None:
        """Persist one usage record.

        Args:
            record: Validated usage and cost record.
        """
        ...


class LoggingCostSink:
    """Emit canonical JSON cost records for collection by an external pipeline."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the structured logging sink.

        Args:
            logger: Optional dedicated accounting logger.
        """
        self.logger = logger or logging.getLogger("mcp_portal.cost")

    async def append(self, record: UsageRecord) -> None:
        """Emit one canonical JSON usage record.

        Args:
            record: Validated usage and cost record.
        """
        self.logger.info("portal_cost %s", json.dumps(asdict(record), sort_keys=True))


class MemoryCostSink:
    """Deterministic cost sink for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize an empty record collection."""
        self.records: list[UsageRecord] = []

    async def append(self, record: UsageRecord) -> None:
        """Retain one usage record.

        Args:
            record: Validated usage and cost record.
        """
        self.records.append(record)


class TelemetryRecorder(Protocol):
    """Metrics and cost-accounting boundary used by portal runtime components."""

    def record_tool_call(
        self,
        invocation: InvocationContext,
        *,
        outcome: str,
        duration_seconds: float,
        admission_wait_seconds: float = 0,
    ) -> None:
        """Record one completed or rejected tool invocation.

        Args:
            invocation: Trusted invocation context.
            outcome: Low-cardinality result classification.
            duration_seconds: Total request duration.
            admission_wait_seconds: Time spent waiting for execution capacity.
        """
        ...

    def record_downstream_call(
        self,
        dependency: str,
        *,
        outcome: str,
        duration_seconds: float,
        circuit_state: str,
    ) -> None:
        """Record one downstream dependency operation.

        Args:
            dependency: Registered dependency name.
            outcome: Low-cardinality result classification.
            duration_seconds: Total operation duration.
            circuit_state: Breaker state after the operation.
        """
        ...

    async def record_usage(self, record: UsageRecord) -> None:
        """Record detailed usage and aggregate quantity/cost metrics.

        Args:
            record: Validated usage and cost record.
        """
        ...


class MemoryTelemetryRecorder:
    """Deterministic recorder for tests and embedded metric consumers."""

    def __init__(self) -> None:
        """Initialize empty tool, downstream, and usage event collections."""
        self.tool_calls: list[dict[str, Any]] = []
        self.downstream_calls: list[dict[str, Any]] = []
        self.usage_records: list[UsageRecord] = []

    def record_tool_call(
        self,
        invocation: InvocationContext,
        *,
        outcome: str,
        duration_seconds: float,
        admission_wait_seconds: float = 0,
    ) -> None:
        """Retain one tool metric event.

        Args:
            invocation: Trusted invocation context.
            outcome: Low-cardinality result classification.
            duration_seconds: Total request duration.
            admission_wait_seconds: Time spent waiting for execution capacity.
        """
        self.tool_calls.append(
            {
                "tool_name": invocation.tool_name,
                "tenant_id": invocation.identity.tenant_id,
                "outcome": outcome,
                "duration_seconds": duration_seconds,
                "admission_wait_seconds": admission_wait_seconds,
            }
        )

    def record_downstream_call(
        self,
        dependency: str,
        *,
        outcome: str,
        duration_seconds: float,
        circuit_state: str,
    ) -> None:
        """Retain one downstream metric event.

        Args:
            dependency: Registered dependency name.
            outcome: Low-cardinality result classification.
            duration_seconds: Total operation duration.
            circuit_state: Breaker state after the operation.
        """
        self.downstream_calls.append(
            {
                "dependency": dependency,
                "outcome": outcome,
                "duration_seconds": duration_seconds,
                "circuit_state": circuit_state,
            }
        )

    async def record_usage(self, record: UsageRecord) -> None:
        """Retain one detailed usage record.

        Args:
            record: Validated usage and cost record.
        """
        self.usage_records.append(record)


class OpenTelemetryRecorder:
    """Emit low-cardinality metrics and detailed cost-accounting events."""

    def __init__(
        self,
        *,
        cost_sink: CostSink | None = None,
        metrics_enabled: bool = True,
        cost_accounting_enabled: bool = True,
        include_tenant_metrics: bool = False,
        instrumentation_version: str = "0.1.0",
    ) -> None:
        """Create OpenTelemetry instruments using the process meter provider.

        Args:
            cost_sink: Detailed accounting destination.
            metrics_enabled: Whether metric instruments should receive observations.
            cost_accounting_enabled: Whether detailed cost events should be appended.
            include_tenant_metrics: Whether tenant ID is allowed as a metric dimension.
            instrumentation_version: Portal instrumentation version.
        """
        self.cost_sink = cost_sink or LoggingCostSink()
        self.metrics_enabled = metrics_enabled
        self.cost_accounting_enabled = cost_accounting_enabled
        self.include_tenant_metrics = include_tenant_metrics
        meter = metrics.get_meter("mcp_portal", instrumentation_version)
        self.tool_calls = meter.create_counter(
            "mcp.portal.tool.calls", unit="{call}", description="Completed MCP tool calls"
        )
        self.tool_duration = meter.create_histogram(
            "mcp.portal.tool.duration", unit="s", description="MCP tool call duration"
        )
        self.admission_wait = meter.create_histogram(
            "mcp.portal.tool.admission_wait",
            unit="s",
            description="Time waiting for tool execution capacity",
        )
        self.downstream_calls = meter.create_counter(
            "mcp.portal.downstream.calls",
            unit="{call}",
            description="Downstream dependency calls",
        )
        self.downstream_duration = meter.create_histogram(
            "mcp.portal.downstream.duration",
            unit="s",
            description="Downstream dependency call duration",
        )
        self.usage = meter.create_counter(
            "mcp.portal.usage", unit="{unit}", description="Metered provider usage"
        )
        self.estimated_cost = meter.create_counter(
            "mcp.portal.cost.estimated",
            unit="{currency}",
            description="Estimated provider cost",
        )

    def record_tool_call(
        self,
        invocation: InvocationContext,
        *,
        outcome: str,
        duration_seconds: float,
        admission_wait_seconds: float = 0,
    ) -> None:
        """Record one tool count, duration, and admission wait.

        Args:
            invocation: Trusted invocation context.
            outcome: Low-cardinality result such as succeeded, denied, failed, or timed_out.
            duration_seconds: Total request duration.
            admission_wait_seconds: Time spent waiting for execution slots.
        """
        if not self.metrics_enabled:
            return
        attributes = self._invocation_attributes(invocation, outcome)
        self.tool_calls.add(1, attributes)
        self.tool_duration.record(duration_seconds, attributes)
        self.admission_wait.record(admission_wait_seconds, attributes)

    def record_downstream_call(
        self,
        dependency: str,
        *,
        outcome: str,
        duration_seconds: float,
        circuit_state: str,
    ) -> None:
        """Record downstream count and duration without endpoint or credential data.

        Args:
            dependency: Registered low-cardinality dependency name.
            outcome: Operation result classification.
            duration_seconds: Total downstream duration.
            circuit_state: Breaker state after the operation.
        """
        if not self.metrics_enabled:
            return
        attributes = {
            "mcp.dependency.name": dependency,
            "mcp.outcome": outcome,
            "mcp.circuit.state": circuit_state,
        }
        self.downstream_calls.add(1, attributes)
        self.downstream_duration.record(duration_seconds, attributes)

    async def record_usage(self, record: UsageRecord) -> None:
        """Record aggregate usage metrics and append the detailed accounting event.

        Args:
            record: Validated usage and estimated-cost record.
        """
        if self.metrics_enabled:
            attributes: dict[str, Any] = {
                "mcp.tool.name": record.tool_name,
                "mcp.usage.provider": record.provider,
                "mcp.usage.service": record.service,
                "mcp.usage.operation": record.operation,
                "mcp.usage.unit": record.unit,
            }
            if record.sku:
                attributes["mcp.usage.sku"] = record.sku
            if self.include_tenant_metrics and record.tenant_id:
                attributes["mcp.tenant.id"] = record.tenant_id
            self.usage.add(float(record.quantity), attributes)
            if record.estimated_cost is not None:
                cost_attributes = dict(attributes)
                cost_attributes["mcp.cost.currency"] = record.currency
                if record.pricing_version:
                    cost_attributes["mcp.cost.pricing_version"] = record.pricing_version
                self.estimated_cost.add(float(record.estimated_cost), cost_attributes)
        if self.cost_accounting_enabled:
            await self.cost_sink.append(record)

    def _invocation_attributes(self, invocation: InvocationContext, outcome: str) -> dict[str, Any]:
        """Build bounded metric dimensions for one invocation.

        Args:
            invocation: Trusted invocation context.
            outcome: Low-cardinality result classification.

        Returns:
            Metric-safe attribute mapping without subject or request identifiers.
        """
        attributes: dict[str, Any] = {
            "mcp.tool.name": invocation.tool_name,
            "mcp.outcome": outcome,
            "mcp.auth.method": invocation.identity.auth_method,
        }
        if self.include_tenant_metrics and invocation.identity.tenant_id:
            attributes["mcp.tenant.id"] = invocation.identity.tenant_id
        return attributes
