from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp.server.fastmcp.tools import Tool

from mcp_portal.config import Settings
from mcp_portal.security import InvocationContext


@dataclass(frozen=True)
class PolicyDecision:
    """Auditable result returned by an authorization policy engine.

    Attributes:
        allowed: Whether execution may proceed.
        reason: Stable human-readable decision reason.
        required_scopes: Scopes evaluated by the decision.
        obligations: Additional controls required during execution.
    """

    allowed: bool
    reason: str
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    obligations: tuple[str, ...] = ()


class PolicyEngine(Protocol):
    """Extension point for an external ABAC engine such as Cedar or OPA."""

    async def authorize(
        self,
        invocation: InvocationContext,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        """Evaluate access to a tool invocation.

        Args:
            invocation: Trusted invocation context.
            tool: Registered SDK tool.
            arguments: Validated invocation arguments.

        Returns:
            Auditable authorization decision.
        """
        ...


class ScopePolicyEngine:
    """Default-deny tag/scope policy for the built-in deployment profile."""

    def __init__(self, settings: Settings) -> None:
        """Initialize scope policy from portal settings.

        Args:
            settings: Portal authentication and tag policy configuration.
        """
        self.settings = settings

    async def authorize(
        self,
        invocation: InvocationContext,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        """Authorize one invocation using tool tags and verified scopes.

        Args:
            invocation: Trusted invocation context.
            tool: Registered SDK tool.
            arguments: Validated invocation arguments.

        Returns:
            Allow or deny decision with required scopes.
        """
        _ = arguments
        tags = frozenset((tool.meta or {}).get("tags", ()))
        required = frozenset(
            scope for tag in tags for scope in self.settings.authorization.tag_scopes.get(tag, ())
        ) | frozenset((tool.meta or {}).get("required_scopes", ()))
        obligations: list[str] = []
        if "destructive" in tags:
            obligations.append("approval_required")
        if "external" in tags:
            obligations.append("egress_policy")

        if not self.settings.auth.enabled:
            return PolicyDecision(True, "authentication disabled", required, tuple(obligations))
        if invocation.identity.subject is None and invocation.identity.client_id is None:
            return PolicyDecision(False, "verified identity is required", required)

        missing = required - invocation.identity.scopes
        if missing:
            return PolicyDecision(False, "required scopes are missing", missing)
        return PolicyDecision(True, "scope policy satisfied", required, tuple(obligations))
