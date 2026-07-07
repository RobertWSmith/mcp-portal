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
    canonical = json.dumps(
        tool_contract_payload(tool),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def tool_contract_payload(tool: Tool) -> dict[str, Any]:
    """Build the public contract payload used for fingerprinting.

    Args:
        tool: FastMCP tool to describe.

    Returns:
        JSON-serializable contract fields visible to MCP clients.
    """
    dumped = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload = {
        "name": dumped.get("name"),
        "description": dumped.get("description"),
        "inputSchema": dumped.get("inputSchema"),
        "outputSchema": dumped.get("outputSchema"),
        "annotations": dumped.get("annotations"),
        "_meta": dumped.get("_meta"),
    }
    return {key: value for key, value in payload.items() if value is not None}
