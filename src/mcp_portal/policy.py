"""Evaluate invocation policies and verified scope requirements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from fastmcp.tools import Tool

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
        required_linux_groups: Linux groups evaluated by the decision.
    """

    allowed: bool
    reason: str
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    obligations: tuple[str, ...] = ()
    required_linux_groups: frozenset[str] = field(default_factory=frozenset)


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

    async def authorize_catalog(
        self,
        invocation: InvocationContext,
        tool: Tool,
    ) -> PolicyDecision:
        """Evaluate whether a tool may be disclosed in discovery.

        Args:
            invocation: Trusted invocation context.
            tool: Registered SDK tool considered for disclosure.

        Returns:
            Auditable catalog visibility decision.
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
        supplied_tenant_fields = {
            name
            for name in {"tenant", "tenant_id", "organization_id", "org_id"}
            if name in arguments
        }
        if supplied_tenant_fields and "tenant_override" not in tags:
            return PolicyDecision(
                False,
                "tenant identifiers must come from verified invocation context",
                obligations=("remove_untrusted_tenant_arguments",),
            )
        required = self.required_scopes(tool, tags=tags)
        if "tenant_override" in tags:
            required = required | {"tenant.admin"}
        obligations: list[str] = []
        if "destructive" in tags:
            obligations.append("approval_required")
        if "external" in tags:
            obligations.append("egress_policy")

        if not self.settings.auth.enabled:
            return PolicyDecision(True, "authentication disabled", required, tuple(obligations))
        if invocation.identity.subject is None and invocation.identity.client_id is None:
            return PolicyDecision(False, "verified identity is required", required)
        if self.settings.enterprise.require_tenant and invocation.identity.tenant_id is None:
            return PolicyDecision(False, "verified tenant claim is required", required)

        required_groups = self.required_linux_groups(tool)
        missing_groups = required_groups - invocation.identity.linux_groups
        if missing_groups:
            return PolicyDecision(
                False,
                "required Linux groups are missing",
                required,
                required_linux_groups=missing_groups,
            )

        missing = required - invocation.identity.scopes
        if missing:
            return PolicyDecision(False, "required scopes are missing", missing)
        return PolicyDecision(True, "scope policy satisfied", required, tuple(obligations))

    def required_linux_groups(self, tool: Tool) -> frozenset[str]:
        """Resolve portal-wide and namespace-specific Linux group requirements.

        Args:
            tool: Registered governed tool.

        Returns:
            Complete Linux group set required for execution and discovery.
        """
        required = frozenset(self.settings.auth.required_linux_groups)
        namespace = (tool.meta or {}).get("namespace")
        if namespace is None:
            return required
        return required | frozenset(
            self.settings.authorization.namespace_linux_groups.get(str(namespace), ())
        )

    async def authorize_catalog(
        self,
        invocation: InvocationContext,
        tool: Tool,
    ) -> PolicyDecision:
        """Use execution policy to hide tools the caller cannot invoke.

        Args:
            invocation: Trusted caller identity for the discovery request.
            tool: Registered tool considered for disclosure.

        Returns:
            Allow when the caller satisfies namespace and tool scope policy.
        """
        return await self.authorize(invocation, tool, {})

    def required_scopes(
        self,
        tool: Tool,
        *,
        tags: frozenset[str] | None = None,
    ) -> frozenset[str]:
        """Resolve combined tag, manifest, and deployment namespace scopes.

        Args:
            tool: Registered governed tool.
            tags: Optional normalized tool tags.

        Returns:
            Complete scope set required for discovery and execution.
        """
        meta = tool.meta or {}
        selected_tags = tags if tags is not None else frozenset(meta.get("tags", ()))
        namespace = meta.get("namespace")
        namespace_scopes = (
            self.settings.authorization.namespace_scopes.get(str(namespace), ())
            if namespace is not None
            else ()
        )
        return (
            frozenset(
                scope
                for tag in selected_tags
                for scope in self.settings.authorization.tag_scopes.get(tag, ())
            )
            | frozenset(meta.get("required_scopes", ()))
            | frozenset(namespace_scopes)
        )
