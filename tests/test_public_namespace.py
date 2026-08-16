"""Test the generally available public namespace and web-search tool."""

from __future__ import annotations

from fastmcp import Client

import mcp_portal.namespaces.public as public_namespace
from mcp_portal.namespaces import iter_namespaces
from mcp_portal.server import create_mcp
from mcp_portal.testing import create_test_settings


class FakeDuckDuckGoSearch:
    """Return deterministic results without making an external request."""

    async def ainvoke(self, input: str) -> str:
        """Return a stable result for the supplied query."""
        return f"Result for {input}: https://example.com"


async def test_public_duckduckgo_search_uses_langchain_tool(monkeypatch) -> None:
    """Verify the MCP tool delegates to the LangChain DuckDuckGo implementation."""
    monkeypatch.setattr(
        public_namespace,
        "_create_search_tool",
        lambda: FakeDuckDuckGoSearch(),
    )

    async with Client(create_mcp(create_test_settings())) as client:
        result = await client.call_tool("public_duckduckgo_search", {"query": "MCP protocol"})

    assert result.structured_content == {
        "query": "MCP protocol",
        "results": "Result for MCP protocol: https://example.com",
    }


async def test_public_search_contract_requires_no_namespace_scope() -> None:
    """Verify every authenticated caller can discover the public search tool."""
    namespace = next(item for item in iter_namespaces(strict=True) if item.name == "public")
    assert namespace.required_scopes == frozenset()

    async with Client(create_mcp(create_test_settings())) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    search = tools["public_duckduckgo_search"]
    assert search.meta["required_scopes"] == []
    assert search.annotations is not None
    assert search.annotations.readOnlyHint is True
    assert search.annotations.openWorldHint is True
