"""Test strict namespace admission and committed contract governance."""

from __future__ import annotations

import json

import pytest

from mcp_portal import governance
from mcp_portal.contracts import tool_contract_payload
from mcp_portal.namespaces import (
    Namespace,
    NamespaceContext,
    NamespaceProvider,
    validate_namespace_metadata,
    validate_namespaces,
)


def provider(context: NamespaceContext) -> NamespaceProvider:
    """Create a minimal provider for manifest validation tests."""
    return NamespaceProvider(context.name)


def valid_namespace(**overrides) -> Namespace:
    """Create a strictly valid manifest with optional field overrides."""
    values = {
        "name": "example",
        "create": provider,
        "description": "Example governed namespace.",
        "tags": frozenset({"example"}),
        "owner": "example-team",
        "version": "1.2.3",
        "maturity": "stable",
        "data_classification": "internal",
        "timeout_seconds": 15.0,
        "dependencies": ("database",),
    }
    values.update(overrides)
    return Namespace(**values)


def test_strict_namespace_metadata_accepts_complete_manifest() -> None:
    """Verify complete governed metadata passes admission."""
    assert validate_namespace_metadata(valid_namespace()) == ()
    assert validate_namespaces((valid_namespace(),)) == ()


def test_strict_namespace_metadata_reports_incomplete_lifecycle() -> None:
    """Verify strict admission reports incomplete and inconsistent metadata."""
    namespace = valid_namespace(
        description="",
        tags=frozenset(),
        owner="platform",
        version="latest",
        data_classification="secret",
        timeout_seconds=None,
        dependencies=("", "database", "database"),
        maturity="deprecated",
    )

    errors = validate_namespace_metadata(namespace)

    assert len(errors) == 10
    assert any("specific owner" in error for error in errors)
    assert any("deprecation date" in error for error in errors)
    assert any("replacement" in error for error in errors)


def test_strict_namespace_metadata_rejects_deprecation_fields_on_active_namespace() -> None:
    """Verify active namespaces cannot carry ambiguous deprecation metadata."""
    errors = validate_namespace_metadata(
        valid_namespace(deprecation_date="2027-01-01", replacement="next")
    )
    assert errors == ("namespace 'example' may use deprecation metadata only when deprecated",)


@pytest.mark.asyncio
async def test_governance_writes_and_checks_contract_baseline(tmp_path) -> None:
    """Verify the committed baseline round trip and drift classification."""
    baseline = tmp_path / "tool-contracts.json"

    await governance.write_baseline(baseline)

    assert await governance.check_repository(baseline) == ()
    manifest = json.loads(baseline.read_text(encoding="utf-8"))
    manifest["removed_tool"] = "0" * 64
    baseline.write_text(json.dumps(manifest), encoding="utf-8")
    assert await governance.check_repository(baseline) == ("tool contracts removed: removed_tool",)


@pytest.mark.asyncio
async def test_governed_tool_descriptions_are_normalized() -> None:
    """Verify nested function indentation cannot change public contract fingerprints."""
    server = governance.create_mcp(
        governance.governance_settings(), namespaces=governance.iter_namespaces(strict=True)
    )
    descriptions = {
        tool.name: tool_contract_payload(tool)["description"] for tool in await server.list_tools()
    }

    assert descriptions["health_ping"] == (
        "Confirm that the MCP server can execute tools.\n\n"
        "Returns:\n"
        "    Structured liveness state and acknowledgement.\n"
    )
    assert descriptions["health_runtime_config"] == (
        "Return non-secret runtime configuration for development.\n\n"
        "Returns:\n"
        "    Validated public runtime settings with secrets omitted.\n"
    )


@pytest.mark.asyncio
async def test_governance_reports_missing_baseline(tmp_path) -> None:
    """Verify CI fails clearly when its reviewed baseline is absent."""
    missing = tmp_path / "missing.json"
    assert await governance.check_repository(missing) == (
        f"contract baseline is missing: {missing}",
    )


def test_governance_main_reports_failures(monkeypatch, tmp_path) -> None:
    """Verify the governance CLI exits nonzero with actionable errors."""

    async def fail_check(path):
        return (f"bad baseline: {path}",)

    monkeypatch.setattr(governance, "check_repository", fail_check)
    with pytest.raises(SystemExit, match="bad baseline"):
        governance.main(["check", "--baseline", str(tmp_path / "baseline.json")])
