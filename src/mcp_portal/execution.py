"""Create single-use execution cells for governed tool invocations."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Iterator, Literal

from mcp_portal.errors import ConfigurationPortalError, PermissionPortalError
from mcp_portal.security import InvocationContext, InvocationIdentity

ExecutionIsolation = Literal["in_process", "remote"]
_DATA_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})


@dataclass(frozen=True)
class ExecutionCell:
    """Immutable identity and resource envelope for one tool execution.

    Attributes:
        cell_id: Cryptographically random single-use cell identifier.
        request_id: Server-generated request bound to the cell.
        tool_name: Exact fully-qualified tool admitted into the cell.
        namespace: Exact namespace allowed to use invocation-bound capabilities.
        identity_partition: Non-reversible binding to the verified authorization context.
        isolation: In-process logical boundary or remote process/network boundary.
        data_classification: Namespace-owned maximum expected data classification.
        deadline_seconds: Maximum execution duration inherited from governance policy.
    """

    cell_id: str
    request_id: str
    tool_name: str
    namespace: str
    identity_partition: str = field(repr=False)
    isolation: ExecutionIsolation
    data_classification: str
    deadline_seconds: float


@dataclass
class _ExecutionCellLease:
    """Mutable lifetime marker shared with inherited asynchronous contexts.

    Attributes:
        cell: Immutable execution-cell identity and boundary metadata.
        identity: Verified identity retained only for exact lease validation.
        active: Whether invocation-bound capability access is still permitted.
    """

    cell: ExecutionCell
    identity: InvocationIdentity = field(repr=False)
    active: bool = True

    def close(self) -> None:
        """Permanently expire this single-use lease."""
        self.active = False


_current_execution_cell: ContextVar[_ExecutionCellLease | None] = ContextVar(
    "mcp_portal_execution_cell", default=None
)


def current_execution_cell() -> ExecutionCell | None:
    """Return the active execution cell, if one exists.

    Returns:
        Active cell, or `None` outside a governed tool execution.
    """
    lease = _current_execution_cell.get()
    return lease.cell if lease is not None and lease.active else None


def require_execution_cell(
    invocation: InvocationContext,
    *,
    namespace: str,
) -> ExecutionCell:
    """Require an active cell bound to an invocation and namespace.

    Args:
        invocation: Current trusted invocation context.
        namespace: Namespace requesting invocation-bound capabilities.

    Returns:
        Matching active execution cell.

    Raises:
        PermissionPortalError: If the cell is absent, expired, or belongs to another context.
    """
    lease = _current_execution_cell.get()
    if lease is None:
        raise PermissionPortalError("Namespace capability access requires an execution cell.")
    if not lease.active:
        raise PermissionPortalError("Execution cell lease has expired.")
    cell = lease.cell
    if (
        cell.request_id != invocation.request_id
        or cell.tool_name != invocation.tool_name
        or lease.identity != invocation.identity
    ):
        raise PermissionPortalError("Execution cell does not match the active invocation.")
    if cell.namespace != namespace:
        raise PermissionPortalError(
            "Execution cell does not permit cross-namespace capability access.",
            namespace=namespace,
            details={"cell_namespace": cell.namespace},
        )
    return cell


@dataclass(frozen=True)
class ExecutionCellManager:
    """Issue and enforce single-use execution-cell leases.

    Attributes:
        remote_required_classifications: Data classifications that cannot run in process.
        _partition_key: Process-local key protecting identity partition values.
    """

    remote_required_classifications: frozenset[str] = field(
        default_factory=lambda: frozenset({"restricted"})
    )
    _partition_key: bytes = field(
        default_factory=lambda: secrets.token_bytes(32), compare=False, repr=False
    )

    def __post_init__(self) -> None:
        """Normalize and validate remote-isolation classification policy."""
        selected = frozenset(
            classification.strip().lower()
            for classification in self.remote_required_classifications
        )
        unsupported = selected - _DATA_CLASSIFICATIONS
        if unsupported:
            raise ValueError(
                "Unsupported execution-cell classifications: " + ", ".join(sorted(unsupported))
            )
        object.__setattr__(self, "remote_required_classifications", selected)

    def validate_boundary(
        self,
        *,
        namespace: str,
        data_classification: str,
        isolation: ExecutionIsolation,
    ) -> None:
        """Validate namespace placement against the remote-isolation policy.

        Args:
            namespace: Namespace being mounted or executed.
            data_classification: Namespace-owned data classification.
            isolation: Actual provider isolation boundary.

        Raises:
            ConfigurationPortalError: If the namespace requires a remote boundary.
        """
        if isolation not in {"in_process", "remote"}:
            raise ConfigurationPortalError(
                "Execution cell has an unsupported isolation boundary.",
                namespace=namespace,
                details={"isolation": isolation},
            )
        classification = _classification(data_classification)
        if classification in self.remote_required_classifications and isolation != "remote":
            raise ConfigurationPortalError(
                "Namespace classification requires a remote execution-cell boundary.",
                namespace=namespace,
                details={
                    "data_classification": classification,
                    "required_isolation": "remote",
                },
            )

    @contextmanager
    def open(
        self,
        invocation: InvocationContext,
        *,
        namespace: str,
        data_classification: str,
        isolation: ExecutionIsolation,
    ) -> Iterator[ExecutionCell]:
        """Open one non-reentrant cell and expire it reliably on exit.

        Args:
            invocation: Trusted request, tool, identity, and deadline context.
            namespace: Exact namespace admitted into the cell.
            data_classification: Namespace-owned data classification.
            isolation: Actual provider isolation boundary.

        Yields:
            Active immutable execution cell.

        Returns:
            Context manager that permanently expires the lease on exit.

        Raises:
            PermissionPortalError: If a nested or escaped execution context opens a cell.
            ConfigurationPortalError: If placement violates remote-isolation policy.
        """
        inherited = _current_execution_cell.get()
        if inherited is not None:
            state = "active" if inherited.active else "expired"
            raise PermissionPortalError(
                "Execution cells cannot be nested or reused.", details={"inherited_state": state}
            )
        self.validate_boundary(
            namespace=namespace,
            data_classification=data_classification,
            isolation=isolation,
        )
        cell = ExecutionCell(
            cell_id=secrets.token_urlsafe(18),
            request_id=invocation.request_id,
            tool_name=invocation.tool_name,
            namespace=namespace,
            identity_partition=_identity_partition(invocation.identity, self._partition_key),
            isolation=isolation,
            data_classification=_classification(data_classification),
            deadline_seconds=invocation.deadline_seconds,
        )
        lease = _ExecutionCellLease(cell, invocation.identity)
        token: Token[_ExecutionCellLease | None] = _current_execution_cell.set(lease)
        try:
            yield cell
        finally:
            lease.close()
            _current_execution_cell.reset(token)


def _identity_partition(identity: InvocationIdentity, key: bytes) -> str:
    """Create a stable non-reversible authorization-context binding.

    Args:
        identity: Verified invocation identity.
        key: Process-local HMAC key.

    Returns:
        HMAC-SHA-256 partition for actor, tenant, client, scopes, groups, and auth method.
    """
    canonical = json.dumps(
        {
            "subject": identity.subject,
            "tenant_id": identity.tenant_id,
            "client_id": identity.client_id,
            "scopes": sorted(identity.scopes),
            "linux_groups": sorted(identity.linux_groups),
            "auth_method": identity.auth_method,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _classification(value: str) -> str:
    """Normalize one execution-cell data classification.

    Args:
        value: Candidate classification.

    Returns:
        Supported normalized classification.

    Raises:
        ConfigurationPortalError: If the classification is unsupported.
    """
    selected = value.strip().lower()
    if selected not in _DATA_CLASSIFICATIONS:
        raise ConfigurationPortalError(
            "Execution cell has an unsupported data classification.",
            details={"data_classification": selected},
        )
    return selected
