"""Test persistent wiki domain behavior and MCP contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from fastmcp import Client
import pytest

from mcp_portal.clients import default_client_factories
from mcp_portal.config import WikiSettings
from mcp_portal.errors import ValidationPortalError
from mcp_portal.namespaces import iter_namespaces
from mcp_portal.security import InvocationContext, InvocationIdentity
from mcp_portal.server import PortalServices, create_mcp
from mcp_portal.tenancy import TenantScope
from mcp_portal.testing import create_namespace_test_context, create_test_settings
from mcp_portal.wiki.models import (
    WikiAccess,
    WikiCitation,
    WikiPage,
    WikiPageRecord,
    WikiPassageRecord,
)
from mcp_portal.wiki.repository import InMemoryWikiRepository
from mcp_portal.wiki.service import WikiService

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
HASH = "sha256:" + ("a" * 64)


def access(*scopes: str, tenant: str = "single-tenant") -> WikiAccess:
    """Create deterministic trusted repository access."""
    identity = InvocationIdentity(scopes=frozenset(scopes))
    invocation = InvocationContext("request", "wiki_search", identity, 30.0)
    scope = TenantScope.from_invocation(invocation)
    if tenant == "single-tenant":
        return WikiAccess.from_invocation(invocation, scope)
    other_identity = InvocationIdentity(tenant_id=tenant, scopes=frozenset(scopes))
    other_invocation = InvocationContext("request", "wiki_search", other_identity, 30.0)
    return WikiAccess.from_invocation(
        other_invocation, TenantScope.from_invocation(other_invocation)
    )


def citation(identifier: str = "cite-deploy") -> WikiCitation:
    """Create one immutable test citation."""
    return WikiCitation(
        citation_id=identifier,
        source_id="runbook",
        source_revision="git:abc123",
        title="Production Runbook",
        heading="Rollback",
        source_uri="https://docs.example/runbook",
        source_updated_at=NOW,
        content_hash=HASH,
    )


def page_record(
    tenant_partition: str,
    *,
    slug: str = "deployments",
    revision: str = "rev-1",
    required_scopes: frozenset[str] = frozenset(),
    stale: bool = False,
) -> WikiPageRecord:
    """Create one published page record."""
    return WikiPageRecord(
        tenant_partition=tenant_partition,
        required_scopes=required_scopes,
        page=WikiPage(
            slug=slug,
            revision_id=revision,
            title="Deployments",
            summary="How production deployments and rollbacks work.",
            markdown="# Deployments\n\nRollback with the approved runbook. [cite-deploy]",
            status="published",
            updated_at=NOW,
            source_updated_at=NOW,
            content_hash=HASH,
            tags=["operations", "production"],
            stale=stale,
            citations=[citation()],
        ),
    )


def repository() -> InMemoryWikiRepository:
    """Create a repository containing public and scope-protected evidence."""
    selected = access()
    page = page_record(selected.tenant_partition)
    secret_page = page_record(
        selected.tenant_partition,
        slug="security",
        required_scopes=frozenset({"security.read"}),
    )
    return InMemoryWikiRepository(
        pages=(page, secret_page),
        passages=(
            WikiPassageRecord(
                tenant_partition=selected.tenant_partition,
                passage_id="passage-rollback",
                text="Rollback production by selecting the last approved deployment.",
                citation=citation(),
                page_slug="deployments",
                tags=("operations", "production"),
                embedding=(1.0, 0.0),
            ),
            WikiPassageRecord(
                tenant_partition=selected.tenant_partition,
                passage_id="passage-secret",
                text="Restricted signing procedure.",
                citation=citation("cite-security"),
                page_slug="security",
                tags=("security",),
                required_scopes=frozenset({"security.read"}),
                embedding=(0.0, 1.0),
            ),
        ),
    )


def test_in_memory_repository_enforces_tenant_acl_and_hybrid_retrieval() -> None:
    """Verify authorization is applied before records are ranked or returned."""
    storage = repository()
    ordinary = WikiService(storage, access())
    privileged = WikiService(storage, access("security.read"))

    result = ordinary.search("production rollback", tags=("operations",), limit=5)
    hybrid = storage.search(access(), "unmatched", tags=(), limit=5, query_embedding=(1.0, 0.0))

    assert [hit.passage_id for hit in result.hits] == ["passage-rollback"]
    assert result.hits[0].retrieval == ["lexical"]
    assert hybrid[0].retrieval == ["vector"]
    assert ordinary.list_pages().pages[0].slug == "deployments"
    assert ordinary.get_page("deployments").revision_id == "rev-1"
    assert [item.slug for item in privileged.list_pages().pages] == [
        "deployments",
        "security",
    ]
    assert WikiService(storage, access(tenant="other")).list_pages().pages == []


def test_wiki_service_validates_inputs_and_reports_uniform_not_found() -> None:
    """Verify public input normalization does not expose unauthorized records."""
    service = WikiService(repository(), access())

    with pytest.raises(ValidationPortalError, match="not found"):
        service.get_page("security")
    with pytest.raises(ValidationPortalError, match="query"):
        service.search("   ")
    with pytest.raises(ValidationPortalError, match="slug"):
        service.get_page("../secret")
    with pytest.raises(ValidationPortalError, match="tags"):
        service.list_pages(tags=("not a tag",))


async def test_wiki_namespace_exposes_persistent_tools_resources_and_prompt() -> None:
    """Verify the mounted namespace returns cited structured data and resources."""
    settings = replace(
        create_test_settings(),
        wiki=WikiSettings(sqlalchemy_url="postgresql+psycopg://wiki.invalid/wiki"),
    )
    storage = repository()
    clients = default_client_factories().with_factory(
        "wiki_repository", lambda: storage, shared=True
    )
    server = create_mcp(settings, services=PortalServices(clients=clients))

    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        search_result = await client.call_tool(
            "wiki_search", {"query": "production rollback", "tags": ["operations"]}
        )
        page_result = await client.call_tool("wiki_get_page", {"slug": "deployments"})
        list_result = await client.call_tool("wiki_list_pages", {"include_stale": True})
        page_contents = await client.read_resource("portal://wiki/pages/deployments")
        provenance = await client.read_resource("portal://wiki/pages/deployments/provenance")
        prompt = await client.get_prompt("wiki_research", {"topic": "rollbacks"})

    assert {"wiki_search", "wiki_get_page", "wiki_list_pages"} <= set(tools)
    assert tools["wiki_search"].meta["required_scopes"] == ["wiki.read"]
    assert tools["wiki_search"].annotations.openWorldHint is False
    assert search_result.structured_content["hits"][0]["citation"]["citation_id"] == ("cite-deploy")
    assert page_result.structured_content["slug"] == "deployments"
    assert list_result.structured_content["pages"][0]["revision_id"] == "rev-1"
    assert page_contents[0].text.startswith("# Deployments")
    assert '"citation_id":"cite-deploy"' in provenance[0].text
    assert "do not invent a source" in prompt.messages[0].content.text


def test_wiki_namespace_mounts_only_with_persistent_configuration() -> None:
    """Verify an unconfigured deployment does not advertise unusable wiki tools."""
    assert create_test_settings().namespace_enabled("wiki") is False
    configured = replace(
        create_test_settings(), wiki=WikiSettings(sqlalchemy_url="postgresql://db/wiki")
    )
    assert configured.namespace_enabled("wiki") is True
    namespace = next(item for item in iter_namespaces(strict=True) if item.name == "wiki")
    context = create_namespace_test_context(settings=configured)
    assert namespace.health_check(context).state == "ok"
    with pytest.raises(RuntimeError, match="Resource invocation"):
        context.resource_invocation()
