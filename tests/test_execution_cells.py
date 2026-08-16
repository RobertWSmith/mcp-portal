"""Test single-use execution-cell lifecycle and isolation boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from mcp_portal.audit import MemoryAuditSink
from mcp_portal.errors import ConfigurationPortalError, PermissionPortalError
from mcp_portal.execution import (
    ExecutionCellManager,
    current_execution_cell,
    require_execution_cell,
)
from mcp_portal.namespaces import Namespace, NamespaceProvider
from mcp_portal.security import InvocationContext, InvocationIdentity, invocation_scope
from mcp_portal.server import PortalServices, create_mcp
from mcp_portal.testing import (
    create_namespace_test_context,
    create_test_settings,
    namespace_execution_scope,
)


def invocation(
    *, request_id: str = "request", tool_name: str = "records_lookup"
) -> InvocationContext:
    """Create a deterministic verified invocation.

    Args:
        request_id: Server request identifier.
        tool_name: Fully-qualified tool name.

    Returns:
        Trusted test invocation.
    """
    return InvocationContext(
        request_id,
        tool_name,
        InvocationIdentity(
            subject="alice",
            tenant_id="tenant-a",
            client_id="desktop",
            scopes=frozenset({"records.read"}),
            auth_method="bearer",
        ),
        30,
    )


def test_execution_cell_binds_request_tool_identity_and_namespace() -> None:
    """Verify a cell admits only its exact immutable authorization context."""
    selected = invocation()
    manager = ExecutionCellManager()

    with manager.open(
        selected,
        namespace="records",
        data_classification="internal",
        isolation="in_process",
    ) as cell:
        assert current_execution_cell() is cell
        assert require_execution_cell(selected, namespace="records") is cell
        with pytest.raises(PermissionPortalError, match="active invocation"):
            require_execution_cell(replace(selected, request_id="other"), namespace="records")
        with pytest.raises(PermissionPortalError, match="cross-namespace"):
            require_execution_cell(selected, namespace="finance")

    assert current_execution_cell() is None


def test_execution_cells_are_non_reentrant_and_restricted_cells_require_remote() -> None:
    """Verify nesting is denied and restricted namespaces cannot run in process."""
    selected = invocation()
    manager = ExecutionCellManager()

    with pytest.raises(ConfigurationPortalError, match="remote"):
        manager.validate_boundary(
            namespace="records",
            data_classification="restricted",
            isolation="in_process",
        )

    with (
        manager.open(
            selected,
            namespace="records",
            data_classification="restricted",
            isolation="remote",
        ),
        pytest.raises(PermissionPortalError, match="nested or reused"),
        manager.open(
            selected,
            namespace="records",
            data_classification="restricted",
            isolation="remote",
        ),
    ):
        pass


@pytest.mark.asyncio
async def test_inherited_background_context_cannot_reuse_expired_cell() -> None:
    """Verify a task inheriting context loses capability access after cell closure."""
    selected = invocation()
    manager = ExecutionCellManager()
    release = asyncio.Event()

    async def escaped() -> str:
        """Wait until the parent closes the cell, then report the denial."""
        await release.wait()
        try:
            require_execution_cell(selected, namespace="records")
        except PermissionPortalError as error:
            return error.message
        return "unexpectedly allowed"

    with (
        invocation_scope(selected),
        manager.open(
            selected,
            namespace="records",
            data_classification="internal",
            isolation="in_process",
        ),
    ):
        task = asyncio.create_task(escaped())

    release.set()
    assert await task == "Execution cell lease has expired."


def test_namespace_capabilities_require_the_matching_cell() -> None:
    """Verify namespace invocation services fail closed without their exact cell."""
    context = create_namespace_test_context(namespace_name="records")
    selected = invocation()

    with invocation_scope(selected), pytest.raises(PermissionPortalError, match="requires"):
        context.invocation()

    with namespace_execution_scope(context, selected) as cell:
        assert context.execution_cell() is cell
        assert context.invocation() is selected


def test_local_restricted_namespace_is_rejected_during_mount() -> None:
    """Verify unsafe namespace placement fails before any tool can execute."""

    def provider(context) -> NamespaceProvider:
        """Create an intentionally local restricted provider."""
        _ = context
        return NamespaceProvider("restricted")

    namespace = Namespace(
        "restricted_records",
        provider,
        data_classification="restricted",
        timeout_seconds=5,
    )

    with pytest.raises(ConfigurationPortalError, match="remote execution-cell"):
        create_mcp(create_test_settings(), namespaces=[namespace])


@pytest.mark.asyncio
async def test_runtime_audits_unique_execution_cell_lifecycles() -> None:
    """Verify each admitted call gets a unique cell correlated with completion."""
    audit = MemoryAuditSink()
    server = create_mcp(
        create_test_settings(),
        services=PortalServices(audit_sink=audit),
    )

    await server.call_tool("health_ping", {})
    await server.call_tool("health_ping", {})

    started = [event for event in audit.events if event.event == "execution_cell_started"]
    completed = [event for event in audit.events if event.event == "completion"]
    assert len(started) == len(completed) == 2
    assert len({event.execution_cell_id for event in started}) == 2
    assert [event.execution_cell_id for event in completed] == [
        event.execution_cell_id for event in started
    ]
    assert all(event.execution_cell_namespace == "health" for event in started)
    assert all(event.execution_isolation == "in_process" for event in started)
