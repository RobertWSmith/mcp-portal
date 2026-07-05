from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCPApp
from prefab_ui.actions import CallTool, SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Button,
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle,
    Code,
    Column,
    Grid,
    Metric,
    Muted,
    Row,
    Separator,
    Text,
)
from prefab_ui.rx import RESULT, STATE

from mcp_portal.config import Settings


def create_debug_app(settings: Settings) -> FastMCPApp:
    """Create the interactive FastMCP Apps debug provider.

    Args:
        settings: Runtime settings shared by namespace servers.

    Returns:
        A FastMCP app provider with a dashboard UI and app-only backend tool.
    """
    app = FastMCPApp("MCP Portal Debug")

    @app.tool()
    def debug_snapshot() -> str:
        """Return a formatted runtime snapshot for the debug UI.

        Returns:
            JSON-formatted runtime configuration safe to display locally.
        """
        return _runtime_snapshot_text(settings)

    @app.ui(
        name="portal_debug",
        title="MCP Portal Debug",
        description=(
            "Open the MCP Portal debug dashboard. Use this in FastMCP Apps dev mode "
            "to inspect runtime configuration and verify tool wiring."
        ),
        tags={"debug", "ui"},
    )
    def portal_debug() -> PrefabApp:
        """Render the local development dashboard.

        Returns:
            A Prefab app containing runtime settings and a refresh action.
        """
        snapshot = _runtime_snapshot(settings)
        api_key_variant = "success" if snapshot["has_openai_api_key"] else "warning"
        api_key_label = (
            "API key configured" if snapshot["has_openai_api_key"] else "API key missing"
        )

        with Column(gap=4, css_class="max-w-3xl mx-auto") as view:
            with Card():
                with CardHeader():
                    with Row(gap=2, align="center", css_class="justify-between"):
                        CardTitle("MCP Portal Debug")
                        Badge(api_key_label, variant=api_key_variant)
                    CardDescription("Runtime status for the local FastMCP development UI.")

                with CardContent(), Column(gap=4):
                    with Grid(columns={"default": 1, "md": 3}, gap=3):
                        with Card(css_class="p-4"):
                            Metric(
                                label="Large model",
                                value=str(snapshot["openai_large_language_model"]),
                            )
                        with Card(css_class="p-4"):
                            Metric(
                                label="Small model",
                                value=str(snapshot["openai_small_language_model"]),
                            )
                        with Card(css_class="p-4"):
                            Metric(
                                label="Embedding model",
                                value=str(snapshot["openai_embedding_model"]),
                            )

                    Separator()
                    Text("Runtime snapshot", css_class="font-medium text-sm")
                    Muted("Secret values are omitted.")
                    Code(STATE.snapshot_text, language="json")

                with CardFooter():
                    Button(
                        "Refresh",
                        icon="refresh-cw",
                        variant="outline",
                        on_click=CallTool(
                            debug_snapshot,
                            on_success=SetState("snapshot_text", RESULT),
                        ),
                    )

        return PrefabApp(
            title="MCP Portal Debug",
            view=view,
            state={"snapshot_text": _runtime_snapshot_text(settings)},
        )

    return app


def _runtime_snapshot(settings: Settings) -> dict[str, Any]:
    """Build the non-secret runtime snapshot shown by the debug UI.

    Args:
        settings: Runtime settings to expose safely.

    Returns:
        A dictionary with public configuration and debug command metadata.
    """
    return {
        **settings.public_snapshot(),
        "dev_command": "fastmcp dev apps src/mcp_portal/server.py",
    }


def _runtime_snapshot_text(settings: Settings) -> str:
    """Serialize the runtime snapshot for display in the UI.

    Args:
        settings: Runtime settings to expose safely.

    Returns:
        Pretty-printed JSON with deterministic key ordering.
    """
    return json.dumps(_runtime_snapshot(settings), indent=2, sort_keys=True)
