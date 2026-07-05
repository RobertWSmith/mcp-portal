from __future__ import annotations

import os

from mcp_portal.config import Settings


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
