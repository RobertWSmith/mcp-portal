from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
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
from mcp_portal.errors import InternalPortalError, PortalError
from mcp_portal.namespaces import (
    NamespaceDebugPanel,
    NamespaceRuntime,
    NamespaceStatus,
    iter_namespace_discovery_errors,
)


def create_debug_app(
    settings: Settings,
    namespace_runtimes: Sequence[NamespaceRuntime] = (),
) -> FastMCP:
    """Create the debug tool server.

    Args:
        settings: Runtime settings shared by namespace servers.
        namespace_runtimes: Namespace manifests paired with their runtime contexts.

    Returns:
        A FastMCP server with dashboard and snapshot tools.
    """
    app = FastMCP("MCP Portal Debug")

    @app.tool(
        title="Debug Runtime Snapshot",
        annotations=ToolAnnotations(
            title="Debug Runtime Snapshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"tags": ["debug", "readonly", "closed-world"]},
        structured_output=True,
    )
    def debug_snapshot() -> str:
        """Return a formatted runtime snapshot for the debug UI.

        Returns:
            JSON-formatted runtime configuration safe to display locally.
        """
        return _runtime_snapshot_text(settings, namespace_runtimes)

    @app.tool(
        title="MCP Portal Debug Dashboard",
        annotations=ToolAnnotations(
            title="MCP Portal Debug Dashboard",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"tags": ["debug", "readonly", "closed-world"]},
        structured_output=True,
    )
    def portal_debug() -> dict[str, Any]:
        """Return the local development dashboard payload.

        Returns:
            A Prefab app payload containing runtime settings and namespace diagnostics.
        """
        snapshot = _runtime_snapshot(settings, namespace_runtimes)
        model_provider_snapshot = snapshot["settings"]["model_provider"]
        provider_configured = bool(model_provider_snapshot["configured"])
        provider_name = str(model_provider_snapshot["provider"])
        provider_label = _model_provider_label(provider_name)
        provider_status_variant = "success" if provider_configured else "warning"
        provider_status_label = (
            f"{provider_label} configured"
            if provider_configured
            else f"{provider_label} settings missing"
        )

        with Column(gap=4, css_class="max-w-5xl mx-auto") as view:
            with Card():
                with CardHeader():
                    with Row(gap=2, align="center", css_class="justify-between"):
                        CardTitle("MCP Portal Debug")
                        Badge(provider_status_label, variant=provider_status_variant)
                    CardDescription("Runtime status for the local FastMCP development UI.")

            with Grid(columns={"default": 1, "md": 4}, gap=3):
                with Card():
                    with CardContent(css_class="p-4"):
                        Metric(label="Model provider", value=provider_label)
                with Card():
                    with CardContent(css_class="p-4"):
                        Metric(
                            label="Large model",
                            value=str(model_provider_snapshot["large_language_model"]),
                        )
                with Card():
                    with CardContent(css_class="p-4"):
                        Metric(
                            label="Small model",
                            value=str(model_provider_snapshot["small_language_model"]),
                        )
                with Card():
                    with CardContent(css_class="p-4"):
                        Metric(
                            label="Embedding model",
                            value=str(model_provider_snapshot["embedding_model"]),
                        )

            Text("Namespaces", css_class="font-medium text-sm")
            with Grid(columns={"default": 1, "lg": 2}, gap=3):
                for namespace_snapshot in snapshot["namespaces"]:
                    status = namespace_snapshot["status"]
                    debug_panel = namespace_snapshot.get("debug")
                    mounted_label = "Mounted" if namespace_snapshot["mounted"] else "Not mounted"

                    with Card():
                        with CardHeader():
                            with Row(gap=2, align="center", css_class="justify-between"):
                                CardTitle(str(namespace_snapshot["name"]))
                                Badge(
                                    str(status["state"]),
                                    variant=_status_variant(str(status["state"])),
                                )
                            CardDescription(
                                namespace_snapshot["description"] or "Namespace diagnostics."
                            )
                        with CardContent(), Column(gap=3):
                            with Row(gap=2, align="center"):
                                Badge(mounted_label, variant="outline")
                                for tag in namespace_snapshot["tags"]:
                                    Badge(str(tag), variant="secondary")

                            Text(str(status["message"]), css_class="text-sm")
                            if debug_panel is not None:
                                Separator()
                                Text(str(debug_panel["title"]), css_class="font-medium text-sm")
                                Muted(str(debug_panel["summary"]))
                            Code(_json_text(namespace_snapshot), language="json")

            with Card():
                with CardHeader():
                    CardTitle("Runtime Snapshot")
                    CardDescription("Secret values are omitted.")
                with CardContent():
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

        dashboard = PrefabApp(
            title="MCP Portal Debug",
            view=view,
            state={"snapshot_text": _runtime_snapshot_text(settings, namespace_runtimes)},
        )
        return dashboard.model_dump(mode="json", by_alias=True, exclude_none=True)

    return app


def _runtime_snapshot(
    settings: Settings,
    namespace_runtimes: Sequence[NamespaceRuntime] = (),
) -> dict[str, Any]:
    """Build the non-secret runtime snapshot shown by the debug UI.

    Args:
        settings: Runtime settings to expose safely.
        namespace_runtimes: Namespace manifests paired with runtime contexts.

    Returns:
        A dictionary with public configuration and debug command metadata.
    """
    return {
        "settings": settings.public_snapshot(),
        "namespaces": [_namespace_snapshot(runtime) for runtime in namespace_runtimes],
        "namespace_discovery_errors": iter_namespace_discovery_errors(),
        "dev_command": "mcp-portal --transport streamable-http --port 8000",
    }


def _namespace_snapshot(runtime: NamespaceRuntime) -> dict[str, Any]:
    """Build a public debug snapshot for one namespace.

    Args:
        runtime: Namespace manifest paired with runtime context.

    Returns:
        Public namespace diagnostics.
    """
    status = _namespace_status(runtime)
    debug_panel = _namespace_debug_panel(runtime)

    return {
        "name": runtime.namespace.name,
        "description": runtime.namespace.description,
        "tags": sorted(runtime.namespace.tags),
        "mounted": runtime.context.settings.namespace_enabled(runtime.namespace.name),
        "status": status.to_public_dict(runtime.context.redactor),
        "debug": (
            debug_panel.to_public_dict(runtime.context.redactor)
            if debug_panel is not None
            else None
        ),
    }


def _namespace_status(runtime: NamespaceRuntime) -> NamespaceStatus:
    """Return namespace status, converting hook failures to public errors.

    Args:
        runtime: Namespace manifest paired with runtime context.

    Returns:
        Namespace status metadata.
    """
    if not runtime.context.settings.namespace_enabled(runtime.namespace.name):
        return NamespaceStatus(
            state="disabled",
            message="Namespace is configured off.",
            details={"namespace": runtime.namespace.name},
        )

    if runtime.namespace.health_check is None:
        return NamespaceStatus(
            state="ok",
            message="No namespace health check registered.",
            details={"namespace": runtime.namespace.name},
        )

    try:
        return runtime.namespace.health_check(runtime.context)
    except PortalError as error:
        runtime.context.logger.warning("Namespace health check failed: %s", error.message)
        return NamespaceStatus(
            state="error",
            message=error.message,
            details={"error": error.to_public_dict(runtime.context.redactor)},
        )
    except Exception as error:
        runtime.context.logger.exception("Namespace health check crashed")
        portal_error = InternalPortalError(
            "Namespace health check crashed.",
            namespace=runtime.namespace.name,
            details={"hook": "health_check", "error_type": type(error).__name__},
            cause=error,
        )
        return NamespaceStatus(
            state="error",
            message=portal_error.message,
            details={"error": portal_error.to_public_dict(runtime.context.redactor)},
        )


def _namespace_debug_panel(runtime: NamespaceRuntime) -> NamespaceDebugPanel | None:
    """Return a namespace debug panel, converting hook failures to public errors.

    Args:
        runtime: Namespace manifest paired with runtime context.

    Returns:
        Public namespace debug panel, or None when none is registered.
    """
    if runtime.namespace.debug is None:
        return None

    try:
        return runtime.namespace.debug(runtime.context)
    except PortalError as error:
        runtime.context.logger.warning("Namespace debug hook failed: %s", error.message)
        return NamespaceDebugPanel(
            title="Debug hook failed",
            summary=error.message,
            snapshot={"error": error.to_public_dict(runtime.context.redactor)},
        )
    except Exception as error:
        runtime.context.logger.exception("Namespace debug hook crashed")
        portal_error = InternalPortalError(
            "Namespace debug hook crashed.",
            namespace=runtime.namespace.name,
            details={"hook": "debug", "error_type": type(error).__name__},
            cause=error,
        )
        return NamespaceDebugPanel(
            title="Debug hook failed",
            summary=portal_error.message,
            snapshot={"error": portal_error.to_public_dict(runtime.context.redactor)},
        )


def _runtime_snapshot_text(
    settings: Settings,
    namespace_runtimes: Sequence[NamespaceRuntime] = (),
) -> str:
    """Serialize the runtime snapshot for display in the UI.

    Args:
        settings: Runtime settings to expose safely.
        namespace_runtimes: Namespace manifests paired with runtime contexts.

    Returns:
        Pretty-printed JSON with deterministic key ordering.
    """
    return _json_text(_runtime_snapshot(settings, namespace_runtimes))


def _json_text(value: Any) -> str:
    """Serialize a value as deterministic pretty JSON.

    Args:
        value: JSON-serializable value.

    Returns:
        Pretty-printed JSON with deterministic key ordering.
    """
    return json.dumps(value, indent=2, sort_keys=True)


def _status_variant(state: str) -> str:
    """Map namespace state to a Prefab badge variant.

    Args:
        state: Namespace status state.

    Returns:
        Badge variant name.
    """
    if state == "ok":
        return "success"
    if state == "warning":
        return "warning"
    if state == "disabled":
        return "secondary"
    return "destructive"


def _model_provider_label(provider: str) -> str:
    """Return a readable label for a configured model provider.

    Args:
        provider: Provider identifier from runtime settings.

    Returns:
        Human-readable provider label.
    """
    if provider == "azure_openai":
        return "Azure OpenAI"
    return "OpenAI"
