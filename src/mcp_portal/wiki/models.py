"""Typed domain and MCP result models for persistent wiki knowledge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_portal.security import InvocationContext
from mcp_portal.tenancy import TenantScope

WikiPageStatus = Literal["draft", "published", "archived"]
WikiRetrievalMethod = Literal["lexical", "vector"]
SLUG_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


class WikiCitation(BaseModel):
    """Stable evidence reference attached to a passage or generated page.

    Attributes:
        citation_id: Stable identifier for this evidence reference.
        source_id: Stable identifier for the source object.
        source_revision: Version identifier reported by the source system.
        title: Human-readable source title.
        heading: Optional heading surrounding the cited passage.
        source_uri: Canonical URI for the source object.
        source_updated_at: Timestamp when the source revision was last updated.
        content_hash: SHA-256 digest of the cited source content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str = Field(
        min_length=1,
        max_length=160,
        description="Stable identifier for this evidence reference.",
    )
    source_id: str = Field(
        min_length=1,
        max_length=512,
        description="Stable identifier for the source object.",
    )
    source_revision: str = Field(
        min_length=1,
        max_length=160,
        description="Version identifier reported by the source system.",
    )
    title: str = Field(
        min_length=1,
        max_length=500,
        description="Human-readable source title.",
    )
    heading: str | None = Field(
        default=None,
        max_length=500,
        description="Optional heading surrounding the cited passage.",
    )
    source_uri: str = Field(
        min_length=1,
        max_length=2_048,
        description="Canonical URI for the source object.",
    )
    source_updated_at: datetime = Field(
        description="Timestamp when the source revision was last updated."
    )
    content_hash: str = Field(
        pattern=HASH_PATTERN,
        description="SHA-256 digest of the cited source content.",
    )


class WikiSearchHit(BaseModel):
    """One authorized passage returned by hybrid wiki retrieval.

    Attributes:
        passage_id: Stable identifier for the indexed passage.
        page_slug: Optional wiki page associated with the passage.
        snippet: Bounded source text suitable for model context.
        citation: Evidence reference for the returned text.
        tags: Normalized passage tags.
        relevance_score: Normalized score used to order the result.
        retrieval: Retrieval methods that selected this passage.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    passage_id: str = Field(
        min_length=1,
        max_length=160,
        description="Stable identifier for the indexed passage.",
    )
    page_slug: str | None = Field(
        default=None,
        pattern=SLUG_PATTERN,
        description="Optional wiki page associated with the passage.",
    )
    snippet: str = Field(
        min_length=1,
        max_length=4_000,
        description="Bounded source text suitable for model context.",
    )
    citation: WikiCitation = Field(description="Evidence reference for the returned text.")
    tags: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Normalized passage tags.",
    )
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Normalized score used to order the result.",
    )
    retrieval: list[WikiRetrievalMethod] = Field(
        min_length=1,
        max_length=2,
        description="Retrieval methods that selected this passage.",
    )


class WikiSearchResult(BaseModel):
    """Structured result returned by ``wiki_search``.

    Attributes:
        query: Normalized query evaluated by the retrieval backend.
        hits: Authorized, cited passages in relevance order.
        truncated: Whether more authorized results were available.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Normalized query evaluated by the retrieval backend.",
    )
    hits: list[WikiSearchHit] = Field(description="Authorized, cited passages in relevance order.")
    truncated: bool = Field(description="Whether more authorized results were available.")


class WikiPage(BaseModel):
    """One immutable wiki page revision.

    Attributes:
        slug: Stable URL-safe page identifier.
        revision_id: Immutable page revision identifier.
        title: Human-readable page title.
        summary: Bounded page abstract.
        markdown: Complete cited page content in Markdown.
        status: Review and publication state of this revision.
        updated_at: Timestamp when this revision was created or updated.
        source_updated_at: Latest update time among contributing sources.
        content_hash: SHA-256 digest of the revision content.
        tags: Normalized page tags.
        stale: Whether a contributing source has advanced past this revision.
        citations: Complete evidence set used by the page revision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=SLUG_PATTERN, description="Stable URL-safe page identifier.")
    revision_id: str = Field(
        min_length=1,
        max_length=160,
        description="Immutable page revision identifier.",
    )
    title: str = Field(min_length=1, max_length=500, description="Human-readable page title.")
    summary: str = Field(max_length=2_000, description="Bounded page abstract.")
    markdown: str = Field(min_length=1, description="Complete cited page content in Markdown.")
    status: WikiPageStatus = Field(description="Review and publication state of this revision.")
    updated_at: datetime = Field(description="Timestamp when this revision was created or updated.")
    source_updated_at: datetime | None = Field(
        default=None,
        description="Latest update time among contributing sources.",
    )
    content_hash: str = Field(
        pattern=HASH_PATTERN,
        description="SHA-256 digest of the revision content.",
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Normalized page tags.",
    )
    stale: bool = Field(
        default=False,
        description="Whether a contributing source has advanced past this revision.",
    )
    citations: list[WikiCitation] = Field(
        default_factory=list,
        description="Complete evidence set used by the page revision.",
    )


class WikiPageSummary(BaseModel):
    """Compact page metadata returned by wiki listings.

    Attributes:
        slug: Stable URL-safe page identifier.
        revision_id: Published revision identifier.
        title: Human-readable page title.
        summary: Bounded page abstract.
        updated_at: Timestamp of the published revision.
        tags: Normalized page tags.
        stale: Whether the published revision needs refresh.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=SLUG_PATTERN, description="Stable URL-safe page identifier.")
    revision_id: str = Field(
        min_length=1,
        max_length=160,
        description="Published revision identifier.",
    )
    title: str = Field(min_length=1, max_length=500, description="Human-readable page title.")
    summary: str = Field(max_length=2_000, description="Bounded page abstract.")
    updated_at: datetime = Field(description="Timestamp of the published revision.")
    tags: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Normalized page tags.",
    )
    stale: bool = Field(description="Whether the published revision needs refresh.")


class WikiPageListResult(BaseModel):
    """Bounded page listing returned by ``wiki_list_pages``.

    Attributes:
        pages: Authorized page summaries in stable slug order.
        truncated: Whether more authorized pages were available.
    """

    model_config = ConfigDict(extra="forbid")

    pages: list[WikiPageSummary] = Field(
        description="Authorized page summaries in stable slug order."
    )
    truncated: bool = Field(description="Whether more authorized pages were available.")


class WikiProvenance(BaseModel):
    """Published page revision and its complete source evidence.

    Attributes:
        slug: Stable URL-safe page identifier.
        revision_id: Published revision identifier.
        content_hash: SHA-256 digest of the published content.
        stale: Whether the published revision needs refresh.
        citations: Complete evidence set for the revision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=SLUG_PATTERN, description="Stable URL-safe page identifier.")
    revision_id: str = Field(
        min_length=1,
        max_length=160,
        description="Published revision identifier.",
    )
    content_hash: str = Field(
        pattern=HASH_PATTERN,
        description="SHA-256 digest of the published content.",
    )
    stale: bool = Field(description="Whether the published revision needs refresh.")
    citations: list[WikiCitation] = Field(description="Complete evidence set for the revision.")


@dataclass(frozen=True)
class WikiAccess:
    """Trusted tenant and authorization context used by repository predicates.

    Attributes:
        tenant_partition: Non-reversible verified tenant partition.
        subject: Verified human or workload subject.
        client_id: Verified OAuth client identifier.
        scopes: Verified authorization scopes.
        roles: Verified application roles.
        linux_groups: Verified host groups.
    """

    tenant_partition: Annotated[str, "Non-reversible verified tenant partition."]
    subject: Annotated[str | None, "Verified human or workload subject."]
    client_id: Annotated[str | None, "Verified OAuth client identifier."]
    scopes: Annotated[frozenset[str], "Verified authorization scopes."] = field(
        default_factory=frozenset
    )
    roles: Annotated[frozenset[str], "Verified application roles."] = field(
        default_factory=frozenset
    )
    linux_groups: Annotated[frozenset[str], "Verified host groups."] = field(
        default_factory=frozenset
    )

    @classmethod
    def from_invocation(
        cls, invocation: InvocationContext, tenant_scope: TenantScope
    ) -> "WikiAccess":
        """Build repository access exclusively from trusted invocation state.

        Args:
            invocation: Verified invocation identity and request metadata.
            tenant_scope: Tenant scope derived from verified identity.

        Returns:
            Repository access predicates safe to use for storage filtering.
        """
        identity = invocation.identity
        return cls(
            tenant_partition=tenant_scope.partition,
            subject=identity.subject,
            client_id=identity.client_id,
            scopes=identity.scopes,
            roles=identity.roles,
            linux_groups=identity.linux_groups,
        )


@dataclass(frozen=True)
class WikiPageRecord:
    """Stored page revision plus backend-enforced authorization metadata.

    Attributes:
        tenant_partition: Non-reversible tenant storage partition.
        page: Immutable wiki page revision.
        required_scopes: Scopes required to read this page.
    """

    tenant_partition: Annotated[str, "Non-reversible tenant storage partition."]
    page: Annotated[WikiPage, "Immutable wiki page revision."]
    required_scopes: Annotated[frozenset[str], "Scopes required to read this page."] = field(
        default_factory=frozenset
    )


@dataclass(frozen=True)
class WikiPassageRecord:
    """Stored source passage and optional semantic embedding.

    Attributes:
        tenant_partition: Non-reversible tenant storage partition.
        passage_id: Stable identifier for the indexed passage.
        text: Source text indexed for retrieval.
        citation: Evidence reference for the source text.
        page_slug: Optional wiki page associated with the passage.
        tags: Normalized passage tags.
        required_scopes: Scopes required to retrieve this passage.
        embedding: Optional semantic vector matching configured dimensions.
    """

    tenant_partition: Annotated[str, "Non-reversible tenant storage partition."]
    passage_id: Annotated[str, "Stable identifier for the indexed passage."]
    text: Annotated[str, "Source text indexed for retrieval."]
    citation: Annotated[WikiCitation, "Evidence reference for the source text."]
    page_slug: Annotated[str | None, "Optional wiki page associated with the passage."] = None
    tags: Annotated[tuple[str, ...], "Normalized passage tags."] = ()
    required_scopes: Annotated[frozenset[str], "Scopes required to retrieve this passage."] = field(
        default_factory=frozenset
    )
    embedding: Annotated[
        tuple[float, ...] | None,
        "Optional semantic vector matching configured dimensions.",
    ] = None


def summarize_page(page: WikiPage) -> WikiPageSummary:
    """Return the bounded listing representation of a page revision.

    Args:
        page: Complete page revision to summarize.

    Returns:
        Bounded page metadata safe for catalog listings.
    """
    return WikiPageSummary(
        slug=page.slug,
        revision_id=page.revision_id,
        title=page.title,
        summary=page.summary,
        updated_at=page.updated_at,
        tags=page.tags,
        stale=page.stale,
    )
