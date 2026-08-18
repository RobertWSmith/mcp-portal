"""Provide generally available tools for authenticated portal users."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import httpx
from bs4 import BeautifulSoup, Tag
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import BaseTool
from markdownify import markdownify
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_portal.errors import PortalError, UpstreamPortalError, ValidationPortalError
from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceMetadata,
    NamespaceProvider,
    register_namespace,
)

_MAX_RESPONSE_BYTES = 2_000_000
_DEFAULT_MAX_CHARACTERS = 50_000
_REMOVABLE_ELEMENTS = (
    "script, style, noscript, template, svg, canvas, iframe, object, embed, "
    "nav, header, footer, aside, form, dialog, picture, img, "
    "[hidden], [aria-hidden='true']"
)
_BLOCK_ELEMENTS = (
    "address, article, blockquote, dd, div, dl, dt, figcaption, figure, h1, h2, h3, h4, "
    "h5, h6, li, main, p, pre, section, table, tr"
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


class WebLinkContentResult(BaseModel):
    """Structured text extracted from a public web page.

    Attributes:
        requested_url: Normalized URL supplied by the caller.
        resolved_url: Final URL after redirects.
        title: Document title when the page provides one.
        content: Visible page content after markup and boilerplate removal.
        format: Format used for the returned content.
        content_type: HTTP media type returned by the web server.
        truncated: Whether text exceeded the caller's character limit.
    """

    model_config = ConfigDict(extra="forbid")

    requested_url: str = Field(description="Normalized URL supplied by the caller.")
    resolved_url: str = Field(description="Final URL after redirects.")
    title: str | None = Field(description="Document title, when available.")
    content: str = Field(description="Visible page content with markup and boilerplate removed.")
    format: Literal["markdown", "text"] = Field(description="Format of the returned content.")
    content_type: str = Field(description="HTTP media type returned by the web server.")
    truncated: bool = Field(description="Whether the extracted text was shortened to the limit.")


@dataclass(frozen=True)
class _FetchedPage:
    """Raw HTML response collected within resource limits.

    Attributes:
        url: Validated final URL after redirects.
        content_type: Normalized HTTP media type.
        encoding: Character encoding selected from the HTTP response.
        body: Raw HTML response body.
    """

    url: Annotated[str, "Validated final URL after redirects."]
    content_type: Annotated[str, "Normalized HTTP media type."]
    encoding: Annotated[str, "Character encoding selected from the HTTP response."]
    body: Annotated[bytes, "Raw HTML response body."]


def _create_search_tool() -> BaseTool:
    """Create the LangChain community DuckDuckGo search tool.

    Returns:
        A DuckDuckGo search tool using the community integration's defaults.
    """
    return DuckDuckGoSearchRun()


def _create_http_client(
    request_hook: Callable[[httpx.Request], Awaitable[None]],
) -> httpx.AsyncClient:
    """Create the bounded HTTP client used by the link resolver.

    Args:
        request_hook: Asynchronous policy hook called before each redirect request.

    Returns:
        HTTP client configured with redirect, time, header, and policy limits.
    """
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        timeout=httpx.Timeout(20.0),
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "MCP-Portal-Link-Resolver/1.0",
        },
        event_hooks={"request": [request_hook]},
    )


async def _fetch_web_page(url: str, validate_url: Callable[[str], str]) -> _FetchedPage:
    """Fetch one HTML page while validating every redirect destination.

    Args:
        url: Initial normalized HTTPS URL.
        validate_url: Portal policy function applied to every requested and final URL.

    Returns:
        Bounded HTML response and its validated metadata.
    """

    async def validate_request(request: httpx.Request) -> None:
        """Apply portal egress policy immediately before one HTTP request.

        Args:
            request: HTTP request that is about to be sent.
        """
        validate_url(str(request.url))

    try:
        async with (
            _create_http_client(validate_request) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code >= 400:
                raise UpstreamPortalError(
                    "Web page returned an unsuccessful HTTP status.",
                    details={"status_code": response.status_code},
                )
            content_type = (
                response.headers.get("content-type", "").partition(";")[0].strip().lower()
            )
            if content_type not in {"", "text/html", "application/xhtml+xml"}:
                raise ValidationPortalError(
                    "Web link must resolve to an HTML document.",
                    details={"content_type": content_type},
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise UpstreamPortalError(
                        "Web page exceeds the resolver's response-size limit.",
                        details={"max_bytes": _MAX_RESPONSE_BYTES},
                    )
                chunks.append(chunk)
            resolved_url = validate_url(str(response.url))
            return _FetchedPage(
                url=resolved_url,
                content_type=content_type or "text/html",
                encoding=response.encoding or "utf-8",
                body=b"".join(chunks),
            )
    except PortalError:
        raise
    except httpx.TimeoutException as exc:
        raise UpstreamPortalError("Web page request timed out.", cause=exc) from exc
    except httpx.HTTPError as exc:
        raise UpstreamPortalError("Web page could not be retrieved.", cause=exc) from exc


def _scrub_html(
    html: str, max_characters: int, output_format: Literal["markdown", "text"] = "markdown"
) -> tuple[str | None, str, bool]:
    """Extract readable Markdown or plain text from HTML with Beautiful Soup.

    Args:
        html: Raw HTML document to scrub.
        max_characters: Maximum number of content characters to retain.
        output_format: Whether to render compact Markdown or plain text.

    Returns:
        Optional title, cleaned content, and whether the content was truncated.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = None
    if soup.title is not None:
        selected_title = " ".join(soup.title.stripped_strings)
        title = selected_title or None

    root = soup.find("main") or soup.find("article") or soup.body or soup
    for element in root.select(_REMOVABLE_ELEMENTS):
        element.decompose()
    if output_format == "markdown":
        content = markdownify(
            str(root),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
        )
        content = "\n".join(line.rstrip() for line in content.splitlines()).strip()
        content = re.sub(r"\n{3,}", "\n\n", content)
    else:
        for element in root.select(_BLOCK_ELEMENTS):
            if isinstance(element, Tag):
                element.append("\n")
        lines: list[str] = []
        for line in root.get_text(" ", strip=False).splitlines():
            normalized = " ".join(line.split())
            normalized = re.sub(r"\s+([,.;:!?%)\]])", r"\1", normalized)
            normalized = re.sub(r"([(\[])\s+", r"\1", normalized)
            if normalized and (not lines or normalized != lines[-1]):
                lines.append(normalized)
        content = "\n".join(lines)
    truncated = len(content) > max_characters
    if truncated:
        content = content[:max_characters].rstrip()
    return title, content, truncated


@register_namespace(
    NamespaceMetadata(
        name="public",
        description="Generally available tools for every authenticated portal user.",
        tags=frozenset({"public", "search", "web", "readonly"}),
        owner="platform-engineering",
        version="1.1.0",
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

    @provider.tool(
        name="resolve_web_link",
        title="Resolve Web Link",
        description=(
            "Retrieve an HTTPS web page and return readable Markdown or plain text using "
            "Beautiful Soup. "
            "Scripts, styles, navigation, forms, hidden elements, and other non-content markup "
            "are removed. This tool reads server-rendered HTML and does not execute JavaScript.\n\n"
            "Args:\n"
            "    url: Absolute HTTPS URL of the public web page.\n"
            "    output_format: Return token-efficient Markdown or plain text.\n"
            "    max_characters: Maximum number of cleaned text characters to return.\n\n"
            "Returns:\n"
            "    Requested and resolved URLs, page title, cleaned content and its format, content "
            "type, and whether the content was truncated.\n"
        ),
        annotations=ToolAnnotations(
            title="Resolve Web Link",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        meta={"tags": ["public", "web", "content", "readonly"]},
        structured_output=True,
    )
    async def resolve_web_link(
        url: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2048,
                description="Absolute HTTPS URL of the public web page to retrieve.",
            ),
        ],
        output_format: Annotated[
            Literal["markdown", "text"],
            Field(description="Return cleaned Markdown or plain text."),
        ] = "markdown",
        max_characters: Annotated[
            int,
            Field(
                ge=1_000,
                le=100_000,
                description="Maximum number of cleaned text characters to return.",
            ),
        ] = _DEFAULT_MAX_CHARACTERS,
    ) -> WebLinkContentResult:
        """Resolve a public HTTPS page to its readable content.

        Args:
            url: Absolute HTTPS URL of the public web page.
            output_format: Whether to return compact Markdown or plain text.
            max_characters: Maximum number of cleaned content characters to return.

        Returns:
            Structured cleaned page content and retrieval metadata.
        """
        requested_url = context.outbound_url(url)
        context.logger.debug("Public web-link resolution requested")
        page = await _fetch_web_page(requested_url, context.outbound_url)
        html = page.body.decode(page.encoding, errors="replace")
        title, content, truncated = _scrub_html(html, max_characters, output_format)
        return WebLinkContentResult(
            requested_url=requested_url,
            resolved_url=page.url,
            title=title,
            content=content,
            format=output_format,
            content_type=page.content_type,
            truncated=truncated,
        )

    return provider
