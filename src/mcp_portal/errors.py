from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp_portal.redaction import Redactor


class PortalError(Exception):
    """Base class for predictable MCP Portal failures.

    Attributes:
        code: Stable machine-readable error code.
        category: Broad error class used by tools and debug UIs.
    """

    code = "portal_error"
    category = "internal"

    def __init__(
        self,
        message: str,
        *,
        namespace: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize a structured portal error.

        Args:
            message: Human-readable error summary.
            namespace: Optional namespace where the error occurred.
            details: Optional diagnostic metadata.
            cause: Optional lower-level exception that triggered this error.
        """
        super().__init__(message)
        self.message = message
        self.namespace = namespace
        self.details = dict(details or {})
        self.__cause__ = cause

    def to_public_dict(self, redactor: Redactor | None = None) -> dict[str, Any]:
        """Return a redacted, client-safe error payload.

        Args:
            redactor: Optional redactor used for diagnostic details.

        Returns:
            Public error metadata safe for logs or debug UIs.
        """
        safe_details = self.details if redactor is None else redactor.redact(self.details)
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "namespace": self.namespace,
            "details": safe_details,
        }


class ConfigurationPortalError(PortalError):
    """Failure caused by missing or invalid runtime configuration."""

    code = "configuration_error"
    category = "configuration"


class ValidationPortalError(PortalError):
    """Failure caused by invalid user or tool input."""

    code = "validation_error"
    category = "validation"


class UpstreamPortalError(PortalError):
    """Failure caused by an upstream service or provider."""

    code = "upstream_error"
    category = "upstream"


class TimeoutPortalError(PortalError):
    """Failure caused by an operation exceeding its time budget."""

    code = "timeout_error"
    category = "timeout"


class PermissionPortalError(PortalError):
    """Failure caused by missing authorization or denied access."""

    code = "permission_error"
    category = "permission"


class InternalPortalError(PortalError):
    """Failure caused by an unexpected internal bug."""

    code = "internal_error"
    category = "internal"
