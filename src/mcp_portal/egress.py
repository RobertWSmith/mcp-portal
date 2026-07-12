"""Enforce network egress restrictions for downstream destinations."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from mcp_portal.errors import PermissionPortalError, ValidationPortalError


@dataclass(frozen=True)
class EgressPolicy:
    """Validate outbound destinations before namespaces create network requests.

    Attributes:
        allowed_hosts: Optional exact DNS hostname allowlist.
        allow_private_networks: Whether literal private IP destinations are permitted.
    """

    allowed_hosts: frozenset[str] = frozenset()
    allow_private_networks: bool = False

    def validate_url(self, url: str) -> str:
        """Validate an outbound URL against scheme, host, and network boundaries.

        Args:
            url: Candidate outbound destination.

        Returns:
            Normalized approved URL.
        """
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValidationPortalError("Outbound destinations must be absolute HTTPS URLs.")
        host = parsed.hostname.lower().rstrip(".")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise PermissionPortalError(
                "Outbound destination is not approved.", details={"host": host}
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if (
            address
            and not self.allow_private_networks
            and (address.is_private or address.is_loopback or address.is_link_local)
        ):
            raise PermissionPortalError("Private or local outbound destinations are blocked.")
        return parsed.geturl()
