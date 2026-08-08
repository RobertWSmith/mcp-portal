"""Provide generally available tools for authenticated portal users."""

from __future__ import annotations

from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import BaseTool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceMetadata,
    NamespaceProvider,
    register_namespace,
)


class DuckDuckGoSearchResult(BaseModel):
    """Structured DuckDuckGo search response returned to MCP clients.

    Attributes:
        query: Search query supplied by the caller.
        results: Text results produced by the LangChain community search tool.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Search query supplied by the caller.")
    results: str = Field(description="DuckDuckGo search results with sources and snippets.")


def _create_search_tool() -> BaseTool:
    """Create the LangChain community DuckDuckGo search tool.

    Returns:
        A DuckDuckGo search tool using the community integration's defaults.
    """
    return DuckDuckGoSearchRun()


@register_namespace(
    NamespaceMetadata(
        name="public",
        description="Generally available tools for every authenticated portal user.",
        tags=frozenset({"public", "search", "readonly"}),
        owner="platform-engineering",
        version="1.0.0",
        maturity="stable",
        data_classification="public",
        required_scopes=frozenset(),
        timeout_seconds=30.0,
    )
)
def create_provider(context: NamespaceContext) -> NamespaceProvider:
    """Create the public namespace provider.

    Args:
        context: Runtime services shared with the public namespace.

    Returns:
        Public tools available without namespace-specific scopes.
    """
    provider = NamespaceProvider("Public")

    @provider.tool(
        name="duckduckgo_search",
        title="Search DuckDuckGo",
        description=(
            "Search the public web with DuckDuckGo through the LangChain community "
            "integration.\n\n"
            "Args:\n"
            "    query: Natural-language or keyword search query.\n\n"
            "Returns:\n"
            "    The query and rendered search-result snippets with their sources.\n"
        ),
        annotations=ToolAnnotations(
            title="Search DuckDuckGo",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        meta={"tags": ["public", "search", "readonly"]},
        structured_output=True,
    )
    async def duckduckgo_search(query: str) -> DuckDuckGoSearchResult:
        """Search the public web with DuckDuckGo.

        Args:
            query: Natural-language or keyword search query.

        Returns:
            The query and rendered search-result snippets with their sources.
        """
        context.logger.debug("Public DuckDuckGo search requested")
        results = await _create_search_tool().ainvoke(query)
        return DuckDuckGoSearchResult(query=query, results=results)

    return provider
