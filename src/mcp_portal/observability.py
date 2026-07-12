"""Configure telemetry environment variables and metric recorders."""

from __future__ import annotations

import os

from mcp_portal.config import Settings
from mcp_portal.telemetry import CostSink, OpenTelemetryRecorder, TelemetryRecorder


def configure_observability_environment(settings: Settings) -> None:
    """Populate OpenTelemetry environment defaults from portal settings.

    Args:
        settings: Runtime settings containing observability metadata.
    """
    os.environ.setdefault("OTEL_SERVICE_NAME", settings.observability.service_name)
    if settings.observability.otlp_endpoint is not None:
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            settings.observability.otlp_endpoint,
        )


def create_telemetry_recorder(
    settings: Settings,
    *,
    cost_sink: CostSink | None = None,
) -> TelemetryRecorder:
    """Create portal metrics and accounting instrumentation from settings.

    Args:
        settings: Runtime settings containing observability policy.
        cost_sink: Optional durable or test accounting destination.

    Returns:
        Recorder backed by the process OpenTelemetry meter provider.
    """
    return OpenTelemetryRecorder(
        cost_sink=cost_sink,
        metrics_enabled=settings.observability.metrics_enabled,
        cost_accounting_enabled=settings.observability.cost_accounting_enabled,
        include_tenant_metrics=settings.observability.include_tenant_metrics,
    )
