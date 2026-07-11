from __future__ import annotations

from typing import Protocol

from mcp_portal.errors import PermissionPortalError
from mcp_portal.security import InvocationIdentity


class CredentialBroker(Protocol):
    """Issue audience-bound downstream credentials without token passthrough."""

    async def credential_for(self, identity: InvocationIdentity, audience: str) -> str:
        """Issue a downstream credential.

        Args:
            identity: Verified caller identity.
            audience: Exact downstream resource identifier.

        Returns:
            Audience-bound access credential.
        """
        ...


class RejectingCredentialBroker:
    """Safe default requiring deployments to configure a real token-exchange broker."""

    async def credential_for(self, identity: InvocationIdentity, audience: str) -> str:
        """Reject credential requests until a real broker is configured.

        Args:
            identity: Verified caller identity.
            audience: Exact downstream resource identifier.

        Returns:
            This implementation never returns.
        """
        _ = identity
        raise PermissionPortalError(
            "No downstream credential broker is configured.", details={"audience": audience}
        )
