"""Build the production ASGI application around an MCP Portal server."""

from __future__ import annotations

from typing import Any

from mcp_portal.config import Settings
from mcp_portal.server import create_production_mcp


def create_app(settings: Settings | None = None) -> Any:
    """Create the production ASGI application.

    Args:
        settings: Optional runtime settings. When omitted, settings are loaded from
            the environment.

    Returns:
        A Starlette-compatible ASGI application.
    """
    selected_settings = settings or Settings.from_env()
    return create_production_mcp(selected_settings).http_app(
        path=selected_settings.http.path,
        json_response=selected_settings.http.json_response,
        stateless_http=selected_settings.http.stateless,
    )


app = create_app()
