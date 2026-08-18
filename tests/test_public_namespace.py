"""Test the generally available public namespace and web-search tool."""

from __future__ import annotations

from fastmcp import Client
import httpx
import pytest

import mcp_portal.namespaces.public as public_namespace
from mcp_portal.errors import UpstreamPortalError, ValidationPortalError
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


async def test_public_resolve_web_link_returns_scrubbed_content(monkeypatch) -> None:
    """Verify the MCP tool returns content while removing page chrome and active markup."""
    html = b"""
        <html>
          <head><title> Example article </title><style>.hidden {display:none}</style></head>
          <body>
            <nav>Home Products Account</nav>
            <main>
              <h1>Useful heading</h1>
              <p>Hello <strong>readable world</strong>.</p>
              <script>window.secret = 'ignore me'</script>
              <aside>Related links</aside>
              <p aria-hidden="true">Invisible text</p>
            </main>
            <footer>Copyright boilerplate</footer>
          </body>
        </html>
    """

    async def fake_fetch(url, validate_url):
        assert url == "https://example.com/article"
        assert validate_url("https://example.com/final") == "https://example.com/final"
        return public_namespace._FetchedPage(
            url="https://example.com/final",
            content_type="text/html",
            encoding="utf-8",
            body=html,
        )

    monkeypatch.setattr(public_namespace, "_fetch_web_page", fake_fetch)

    async with Client(create_mcp(create_test_settings())) as client:
        result = await client.call_tool(
            "public_resolve_web_link", {"url": "https://example.com/article"}
        )

    assert result.structured_content == {
        "requested_url": "https://example.com/article",
        "resolved_url": "https://example.com/final",
        "title": "Example article",
        "content": "# Useful heading\n\nHello **readable world**.",
        "format": "markdown",
        "content_type": "text/html",
        "truncated": False,
    }


def test_scrub_html_prefers_main_deduplicates_and_truncates() -> None:
    """Verify extraction selects primary content and reports bounded output."""
    title, text, truncated = public_namespace._scrub_html(
        "<title>Title words</title><body><p>Outside</p><main>"
        "<p>Repeated</p><p>Repeated</p><p>More useful content here</p></main></body>",
        20,
        "text",
    )

    assert title == "Title words"
    assert text == "Repeated\nMore useful"
    assert truncated is True


def test_scrub_html_returns_compact_markdown_with_structure() -> None:
    """Verify cleaned Markdown keeps useful semantics without raw tags or page chrome."""
    title, content, truncated = public_namespace._scrub_html(
        "<html><title>Guide</title><body><main><h2>Steps</h2><ol><li>Read the "
        "<a href='https://example.com/docs'>docs</a></li><li><code>run</code> it</li></ol>"
        "<footer>Noise</footer></main></body></html>",
        1_000,
    )

    assert title == "Guide"
    assert content == ("## Steps\n\n1. Read the [docs](https://example.com/docs)\n2. `run` it")
    assert truncated is False


async def test_fetch_web_page_validates_redirects_and_reads_html(monkeypatch) -> None:
    """Verify each HTTP request is policy checked and the final HTML is bounded and decoded."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<main>Resolved</main>",
        )

    def client_factory(request_hook):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
            event_hooks={"request": [request_hook]},
        )

    validated: list[str] = []

    def validate_url(url: str) -> str:
        validated.append(url)
        return url

    monkeypatch.setattr(public_namespace, "_create_http_client", client_factory)
    page = await public_namespace._fetch_web_page("https://example.com/start", validate_url)

    assert requested == ["https://example.com/start", "https://example.com/article"]
    assert validated == [
        "https://example.com/start",
        "https://example.com/article",
        "https://example.com/article",
    ]
    assert page == public_namespace._FetchedPage(
        url="https://example.com/article",
        content_type="text/html",
        encoding="utf-8",
        body=b"<main>Resolved</main>",
    )


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (
            httpx.Response(404, text="missing"),
            UpstreamPortalError,
            "unsuccessful HTTP status",
        ),
        (
            httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf"),
            ValidationPortalError,
            "HTML document",
        ),
        (
            httpx.Response(200, content=b"x" * (public_namespace._MAX_RESPONSE_BYTES + 1)),
            UpstreamPortalError,
            "response-size limit",
        ),
    ],
)
async def test_fetch_web_page_rejects_bad_responses(monkeypatch, response, error, message) -> None:
    """Verify HTTP status, media type, and byte limits fail with stable portal errors."""

    def client_factory(request_hook):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: response),
            event_hooks={"request": [request_hook]},
        )

    monkeypatch.setattr(public_namespace, "_create_http_client", client_factory)
    with pytest.raises(error, match=message):
        await public_namespace._fetch_web_page("https://example.com", lambda url: url)


async def test_public_resolver_contract_requires_no_namespace_scope() -> None:
    """Verify authenticated callers can discover the public read-only resolver."""
    async with Client(create_mcp(create_test_settings())) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    resolver = tools["public_resolve_web_link"]
    assert resolver.meta["required_scopes"] == []
    assert resolver.annotations is not None
    assert resolver.annotations.readOnlyHint is True
    assert resolver.annotations.openWorldHint is True
    assert resolver.inputSchema["properties"]["output_format"]["default"] == "markdown"
    assert resolver.inputSchema["properties"]["max_characters"]["default"] == 50_000
