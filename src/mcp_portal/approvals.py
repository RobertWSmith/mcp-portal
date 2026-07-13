"""Define approval verification interfaces and fail-closed defaults."""

from __future__ import annotations

from typing import Any, Protocol

from fastmcp.tools import Tool

from mcp_portal.security import InvocationContext


class ApprovalVerifier(Protocol):
    """Verify out-of-band approval receipts for destructive operations."""

    async def verify(
        self,
        invocation: InvocationContext,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> bool:
        """Verify approval is bound to the actor, tool, and intended arguments.

        Args:
            invocation: Trusted invocation context.
            tool: Destructive registered tool.
            arguments: Validated invocation arguments.

        Returns:
            True only for a valid, unexpired, single-use approval receipt.
        """
        ...


class RejectingApprovalVerifier:
    """Safe default that disables destructive operations until approval is configured."""

    async def verify(
        self,
        invocation: InvocationContext,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> bool:
        """Reject approval because no external verifier is configured.

        Args:
            invocation: Trusted invocation context.
            tool: Destructive registered tool.
            arguments: Validated invocation arguments.

        Returns:
            Always False.
        """
        _ = invocation, tool, arguments
        return False
