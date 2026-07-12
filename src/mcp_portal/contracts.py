from __future__ import annotations

import hashlib
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import Tool


async def generate_tool_contract_manifest(server: FastMCP) -> dict[str, str]:
    """Generate stable fingerprints for every tool exposed by a server.

    Args:
        server: FastMCP server whose tool contracts should be fingerprinted.

    Returns:
        Mapping of FastMCP tool keys to deterministic SHA-256 fingerprints.
    """
    manifest: dict[str, str] = {}
    for tool in await server.list_tools():
        manifest[tool.name] = fingerprint_tool_contract(tool)

    return dict(sorted(manifest.items()))


def fingerprint_tool_contract(tool: Tool) -> str:
    """Generate a deterministic fingerprint for one tool contract.

    Args:
        tool: FastMCP tool to fingerprint.

    Returns:
        SHA-256 hash of the canonicalized contract payload.
    """
    return hashlib.sha256(
        json.dumps(
            tool_contract_payload(tool),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def tool_contract_payload(tool: Tool) -> dict[str, Any]:
    """Build the public contract payload used for fingerprinting.

    Args:
        tool: FastMCP tool to describe.

    Returns:
        JSON-serializable contract fields visible to MCP clients.
    """
    dumped = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {
        key: value
        for key, value in {
            "name": dumped.get("name"),
            "title": dumped.get("title"),
            "description": dumped.get("description"),
            "inputSchema": dumped.get("inputSchema"),
            "outputSchema": dumped.get("outputSchema"),
            "icons": dumped.get("icons"),
            "annotations": dumped.get("annotations"),
            "execution": dumped.get("execution"),
            "_meta": dumped.get("_meta"),
        }.items()
        if value is not None
    }


def compare_tool_contract_manifests(
    baseline: dict[str, str], current: dict[str, str]
) -> dict[str, tuple[str, ...]]:
    """Classify added, removed, and changed tool contracts for CI governance.

    Args:
        baseline: Previously approved tool fingerprint mapping.
        current: Newly generated tool fingerprint mapping.

    Returns:
        Sorted contract names grouped by change classification.
    """
    baseline_names = set(baseline)
    current_names = set(current)
    return {
        "added": tuple(sorted(current_names - baseline_names)),
        "removed": tuple(sorted(baseline_names - current_names)),
        "changed": tuple(
            sorted(
                name for name in baseline_names & current_names if baseline[name] != current[name]
            )
        ),
    }
