"""Model authenticated identities and invocation-scoped security context."""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken


@dataclass(frozen=True)
class InvocationIdentity:
    """Trusted identity and tenancy attached to one MCP invocation.

    Attributes:
        subject: Human or workload subject identifier.
        tenant_id: Trusted tenant partition identifier.
        client_id: OAuth client application identifier.
        scopes: Verified authorization scopes.
        linux_groups: Linux/NSS groups resolved for the verified subject.
        auth_method: Authentication method metadata.
    """

    subject: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    linux_groups: frozenset[str] = field(default_factory=frozenset)
    auth_method: str = "anonymous"


@dataclass(frozen=True)
class InvocationContext:
    """Invocation-scoped security, tracing, and budget context.

    Attributes:
        request_id: Server-generated correlation identifier.
        tool_name: Fully-qualified mounted tool name.
        identity: Verified caller identity.
        deadline_seconds: Maximum execution duration.
    """

    request_id: str
    tool_name: str
    identity: InvocationIdentity
    deadline_seconds: float


_invocation_context: contextvars.ContextVar[InvocationContext | None] = contextvars.ContextVar(
    "mcp_portal_invocation_context", default=None
)


def current_invocation() -> InvocationContext | None:
    """Return the current invocation context, if called from a tool request.

    Returns:
        Current invocation, or None outside a tool request.
    """
    return _invocation_context.get()


def set_invocation(context: InvocationContext) -> contextvars.Token[InvocationContext | None]:
    """Install an invocation context and return its reset token.

    Args:
        context: Invocation to install.

    Returns:
        Context variable token used to restore the previous value.
    """
    return _invocation_context.set(context)


def reset_invocation(token: contextvars.Token[InvocationContext | None]) -> None:
    """Restore the invocation context that preceded ``set_invocation``.

    Args:
        token: Reset token returned by ``set_invocation``.
    """
    _invocation_context.reset(token)


@contextmanager
def invocation_scope(context: InvocationContext) -> Iterator[InvocationContext]:
    """Install and reliably restore invocation context around request processing.

    Args:
        context: Invocation to install.

    Yields:
        Installed invocation context.

    Returns:
        Context manager that restores the previous invocation on exit.
    """
    token = set_invocation(context)
    try:
        yield context
    finally:
        reset_invocation(token)


def identity_from_access_token(tenant_claim: str) -> InvocationIdentity:
    """Build trusted identity exclusively from the verified bearer-token context.

    Args:
        tenant_claim: Verified claim containing the tenant partition.

    Returns:
        Normalized invocation identity.
    """
    token = get_access_token()
    if token is None:
        return InvocationIdentity()
    return identity_from_token(token, tenant_claim)


def identity_from_token(token: AccessToken, tenant_claim: str) -> InvocationIdentity:
    """Normalize one verified SDK access token into portal identity fields.

    Args:
        token: Verified SDK access token.
        tenant_claim: Claim containing the tenant partition.

    Returns:
        Normalized invocation identity.
    """
    claims: dict[str, Any] = token.claims or {}
    tenant = claims.get(tenant_claim)
    subject = token.subject or claims.get("sub")
    auth_method = str(claims.get("auth_method", claims.get("amr", "bearer")))
    linux_groups = (
        linux_groups_for_subject(str(subject), strip_realm=auth_method == "kerberos")
        if subject is not None
        else frozenset()
    )
    return InvocationIdentity(
        subject=str(subject) if subject is not None else None,
        tenant_id=str(tenant) if tenant is not None else None,
        client_id=token.client_id,
        scopes=frozenset(token.scopes),
        linux_groups=linux_groups,
        auth_method=auth_method,
    )


def linux_groups_for_subject(subject: str, *, strip_realm: bool = False) -> frozenset[str]:
    """Resolve a verified subject's primary and supplementary Linux groups via NSS.

    Unknown users, unsupported platforms, and NSS failures deliberately produce no
    memberships.

    Args:
        subject: Authenticated local username or Kerberos principal.
        strip_realm: Resolve the local-name portion before ``@`` for Kerberos principals.

    Returns:
        Resolved Linux group names, or an empty set when the subject cannot be resolved.
    """
    username = subject.partition("@")[0] if strip_realm else subject
    if not username:
        return frozenset()
    try:
        import grp
        import os
        import pwd

        account = pwd.getpwnam(username)
        group_ids = os.getgrouplist(username, account.pw_gid)
        return frozenset(grp.getgrgid(group_id).gr_name for group_id in group_ids)
    except (ImportError, KeyError, OSError):
        return frozenset()


def new_invocation(tool_name: str, tenant_claim: str, deadline_seconds: float) -> InvocationContext:
    """Create an invocation context from verified request state.

    Args:
        tool_name: Fully-qualified mounted tool name.
        tenant_claim: Claim containing the tenant partition.
        deadline_seconds: Maximum execution duration.

    Returns:
        New server-correlated invocation context.
    """
    return InvocationContext(
        request_id=str(uuid.uuid4()),
        tool_name=tool_name,
        identity=identity_from_access_token(tenant_claim),
        deadline_seconds=deadline_seconds,
    )
