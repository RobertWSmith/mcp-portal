"""Implement portal governance as composable FastMCP middleware."""

from __future__ import annotations

import json
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import anyio
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult

from mcp_portal.audit import AuditDetails, audit_event
from mcp_portal.errors import PermissionPortalError, TimeoutPortalError, UpstreamPortalError
from mcp_portal.namespaces import Namespace
from mcp_portal.policy import PolicyDecision
from mcp_portal.security import InvocationContext, invocation_scope, new_invocation


@dataclass(frozen=True)
class ToolCall:
    """Trusted state shared by tool-call middleware stages.

    Attributes:
        name: Fully-qualified tool name.
        arguments: Validated invocation arguments.
        tool: Registered FastMCP tool.
        invocation: Trusted invocation identity and deadline.
        started: Monotonic start time.
    """

    name: str
    arguments: dict[str, Any]
    tool: Tool
    invocation: InvocationContext
    started: float


_current_tool_call: ContextVar[ToolCall | None] = ContextVar("mcp_portal_tool_call", default=None)


def current_tool_call() -> ToolCall:
    """Return middleware state for the active tool invocation.

    Returns:
        Active trusted tool-call state.

    Raises:
        RuntimeError: If called outside the invocation middleware scope.
    """
    call = _current_tool_call.get()
    if call is None:
        raise RuntimeError("No governed tool call is active")
    return call


class InvocationContextMiddleware(Middleware):
    """Derive trusted invocation state before any governance decision."""

    def __init__(self, server: Any) -> None:
        """Initialize middleware for one portal server.

        Args:
            server: Portal server exposing settings and public component lookups.
        """
        self.server = server

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Create the invocation context and keep it active downstream.

        Args:
            context: FastMCP tool-call context.
            call_next: Remaining middleware chain.

        Returns:
            Governed tool result.
        """
        name = context.message.name
        arguments = dict(context.message.arguments or {})
        tool = await self.server.get_tool(name)
        if tool is None:
            return await call_next(context)
        namespace = self.server.component_namespace("tool", name)
        if namespace is not None and (tool.meta or {}).get("namespace") is None:
            meta = dict(tool.meta or {})
            meta.update(
                {
                    "namespace": namespace.name,
                    "namespace_version": namespace.version,
                    "owner": namespace.owner,
                    "maturity": namespace.maturity,
                    "data_classification": namespace.data_classification,
                    "required_scopes": sorted(
                        namespace.required_scopes
                        | frozenset(
                            self.server.portal_settings.authorization.namespace_scopes.get(
                                namespace.name, ()
                            )
                        )
                    ),
                }
            )
            tool = tool.model_copy(update={"meta": meta})
        invocation = new_invocation(
            name,
            self.server.portal_settings.enterprise.tenant_claim,
            self.server.portal_settings.enterprise.tool_timeout(name, tool.meta),
        )
        call = ToolCall(name, arguments, tool, invocation, time.perf_counter())
        state_token: Token[ToolCall | None] = _current_tool_call.set(call)
        try:
            with invocation_scope(invocation):
                return await call_next(context)
        finally:
            _current_tool_call.reset(state_token)


class AuthorizationMiddleware(Middleware):
    """Authorize tool calls and emit sanitized decision audit records."""

    def __init__(self, server: Any) -> None:
        """Initialize authorization middleware.

        Args:
            server: Portal server containing policy and audit adapters.
        """
        self.server = server

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Reject denied calls before tool execution.

        Args:
            context: FastMCP tool-call context.
            call_next: Remaining middleware chain.

        Returns:
            Authorized tool result.
        """
        call = _current_tool_call.get()
        if call is None:
            return await call_next(context)
        decision = await self.server.policy_engine.authorize(
            call.invocation, call.tool, call.arguments
        )
        decision_token = self.server.current_decision.set(decision)
        if self.server.portal_settings.enterprise.audit_enabled:
            await self.server.audit_sink.append(
                audit_event(
                    "authorization",
                    call.invocation,
                    call.arguments,
                    AuditDetails(decision=decision),
                )
            )
        if decision.allowed:
            try:
                return await call_next(context)
            finally:
                self.server.current_decision.reset(decision_token)
        self.server.current_decision.reset(decision_token)
        self.server.telemetry.record_tool_call(
            call.invocation,
            outcome="denied",
            duration_seconds=time.perf_counter() - call.started,
        )
        raise PermissionPortalError(
            "Tool invocation is not authorized.",
            details={
                "required_scopes": sorted(decision.required_scopes),
                "required_linux_groups": sorted(decision.required_linux_groups),
            },
        )


class ApprovalMiddleware(Middleware):
    """Enforce approval obligations produced by authorization policy."""

    def __init__(self, server: Any) -> None:
        """Initialize approval middleware.

        Args:
            server: Portal server containing the approval verifier.
        """
        self.server = server

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Validate a single-use approval when policy requires one.

        Args:
            context: FastMCP tool-call context.
            call_next: Remaining middleware chain.

        Returns:
            Approved tool result.
        """
        call = _current_tool_call.get()
        if call is None:
            return await call_next(context)
        decision: PolicyDecision = self.server.current_decision.get()
        if (
            "approval_required" not in decision.obligations
            or await self.server.approval_verifier.verify(
                call.invocation, call.tool, call.arguments
            )
        ):
            return await call_next(context)
        if self.server.portal_settings.enterprise.audit_enabled:
            await self.server.audit_sink.append(
                audit_event(
                    "approval",
                    call.invocation,
                    call.arguments,
                    AuditDetails(
                        decision=PolicyDecision(False, "approval receipt missing or invalid")
                    ),
                )
            )
        self.server.telemetry.record_tool_call(
            call.invocation,
            outcome="denied",
            duration_seconds=time.perf_counter() - call.started,
        )
        raise PermissionPortalError(
            "Destructive tool invocation requires an approved out-of-band receipt."
        )


class ExecutionControlMiddleware(Middleware):
    """Apply quota, concurrency, deadline, response, telemetry, and audit controls."""

    def __init__(self, server: Any) -> None:
        """Initialize execution-control middleware.

        Args:
            server: Portal server containing admission and telemetry services.
        """
        self.server = server

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Execute one tool within configured operational limits.

        Args:
            context: FastMCP tool-call context.
            call_next: Remaining middleware chain and tool handler.

        Returns:
            Bounded tool result.
        """
        call = _current_tool_call.get()
        if call is None:
            return await call_next(context)
        settings = self.server.portal_settings
        if self.server.enforce_request_controls:
            identity = call.invocation.identity
            actor = identity.subject or identity.client_id or "anonymous"
            quota_key = f"{identity.tenant_id or '-'}:{actor}:{call.name}"
            try:
                await self.server.admission.check_quota(
                    quota_key,
                    settings.middleware.rate_limit_per_second,
                    settings.middleware.rate_limit_burst,
                )
            except PermissionPortalError:
                self.server.telemetry.record_tool_call(
                    call.invocation,
                    outcome="quota_rejected",
                    duration_seconds=time.perf_counter() - call.started,
                )
                raise

        outcome = "succeeded"
        admission_started = time.perf_counter()
        admission_wait = 0.0
        try:
            with anyio.fail_after(call.invocation.deadline_seconds):
                async with self.server.admission.capacity_for(
                    call.name,
                    settings.enterprise.tool_concurrency(call.name, call.tool.meta),
                ):
                    admission_wait = time.perf_counter() - admission_started
                    result = await call_next(context)
            self._validate_response_size(result)
            return result
        except TimeoutError as error:
            outcome = "timed_out"
            raise TimeoutPortalError(
                "Tool execution exceeded its deadline.", cause=error
            ) from error
        except Exception:
            outcome = "failed"
            raise
        finally:
            duration = time.perf_counter() - call.started
            if admission_wait == 0:
                admission_wait = time.perf_counter() - admission_started
            self.server.telemetry.record_tool_call(
                call.invocation,
                outcome=outcome,
                duration_seconds=duration,
                admission_wait_seconds=admission_wait,
            )
            if settings.enterprise.audit_enabled:
                await self.server.audit_sink.append(
                    audit_event(
                        "completion",
                        call.invocation,
                        call.arguments,
                        AuditDetails(outcome=outcome, duration_ms=duration * 1000),
                    )
                )

    def _validate_response_size(self, result: ToolResult) -> None:
        """Reject a serialized response larger than the configured limit.

        Args:
            result: FastMCP tool result to measure.

        Raises:
            UpstreamPortalError: If the response exceeds its byte limit.
        """
        maximum = self.server.portal_settings.middleware.response_max_bytes
        if not self.server.enforce_request_controls or maximum <= 0:
            return
        size = len(json.dumps(result, default=str).encode("utf-8"))
        if size > maximum:
            raise UpstreamPortalError(
                "Tool response exceeded the configured size limit.", details={"size": size}
            )


class CatalogAuthorizationMiddleware(Middleware):
    """Filter every MCP catalog surface using namespace and tool policy."""

    def __init__(self, server: Any) -> None:
        """Initialize catalog middleware.

        Args:
            server: Portal server containing component ownership metadata.
        """
        self.server = server

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        """Return tools authorized for the verified caller.

        Args:
            context: FastMCP catalog context.
            call_next: Catalog provider chain.

        Returns:
            Authorized tools.
        """
        visible = []
        for tool in await call_next(context):
            namespace = self.server.component_namespace("tool", tool.name)
            if not self.server.namespace_visible(namespace):
                continue
            invocation = new_invocation(
                tool.name,
                self.server.portal_settings.enterprise.tenant_claim,
                self.server.portal_settings.enterprise.tool_timeout(tool.name, tool.meta),
            )
            authorizer = getattr(self.server.policy_engine, "authorize_catalog", None)
            decision = (
                await authorizer(invocation, tool)
                if authorizer is not None
                else await self.server.policy_engine.authorize(invocation, tool, {})
            )
            if decision.allowed:
                visible.append(tool)
        return visible

    async def on_list_resources(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        """Filter static resources by namespace visibility.

        Args:
            context: FastMCP catalog context.
            call_next: Catalog provider chain.

        Returns:
            Authorized static resources.
        """
        return [
            item
            for item in await call_next(context)
            if self.server.namespace_visible(
                self.server.component_namespace("resource", str(item.uri))
            )
        ]

    async def on_list_resource_templates(
        self, context: MiddlewareContext[Any], call_next: CallNext
    ) -> Any:
        """Filter resource templates by namespace visibility.

        Args:
            context: FastMCP catalog context.
            call_next: Catalog provider chain.

        Returns:
            Authorized resource templates.
        """
        return [
            item
            for item in await call_next(context)
            if self.server.namespace_visible(
                self.server.component_namespace("template", str(item.uri_template))
            )
        ]

    async def on_list_prompts(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        """Filter prompts by namespace visibility.

        Args:
            context: FastMCP catalog context.
            call_next: Catalog provider chain.

        Returns:
            Authorized prompts.
        """
        return [
            item
            for item in await call_next(context)
            if self.server.namespace_visible(self.server.component_namespace("prompt", item.name))
        ]

    async def on_read_resource(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        """Hide direct access to an unauthorized resource.

        Args:
            context: FastMCP resource request context.
            call_next: Resource provider chain.

        Returns:
            Authorized resource result.
        """
        uri = str(context.message.uri)
        namespace = await self.server.resource_namespace(uri)
        if namespace is not None and not self.server.namespace_visible(namespace):
            raise ValueError(f"Unknown resource: {uri}")
        return await call_next(context)

    async def on_get_prompt(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        """Hide direct access to an unauthorized prompt.

        Args:
            context: FastMCP prompt request context.
            call_next: Prompt provider chain.

        Returns:
            Authorized prompt result.
        """
        name = context.message.name
        namespace: Namespace | None = self.server.component_namespace("prompt", name)
        if namespace is not None and not self.server.namespace_visible(namespace):
            raise ValueError(f"Unknown prompt: {name}")
        return await call_next(context)


def create_governance_middleware(server: Any) -> tuple[Middleware, ...]:
    """Create the ordered portal governance middleware pipeline.

    Args:
        server: Fully initialized portal server.

    Returns:
        Middleware ordered from outermost context to tool execution.
    """
    return (
        CatalogAuthorizationMiddleware(server),
        InvocationContextMiddleware(server),
        AuthorizationMiddleware(server),
        ApprovalMiddleware(server),
        ExecutionControlMiddleware(server),
    )
