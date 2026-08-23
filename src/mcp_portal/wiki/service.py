"""Application service for evidence-first wiki retrieval."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from mcp_portal.errors import ValidationPortalError
from mcp_portal.wiki.models import (
    SLUG_PATTERN,
    WikiAccess,
    WikiPage,
    WikiPageListResult,
    WikiProvenance,
    WikiSearchResult,
)
from mcp_portal.wiki.repository import WikiRepository, page_summaries

_SLUG = re.compile(SLUG_PATTERN)
_TAG = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class WikiEmbeddingProvider(Protocol):
    """Optional embedding boundary supplied by a deployment adapter."""

    def embed_query(self, text: str) -> Sequence[float]:
        """Return the configured-dimensional query embedding.

        Args:
            text: Normalized natural-language query.

        Returns:
            Semantic embedding matching the repository dimensions.
        """
        ...


class WikiService:
    """Validate MCP inputs and coordinate authorized repository operations."""

    def __init__(
        self,
        repository: WikiRepository,
        access: WikiAccess,
        *,
        embeddings: WikiEmbeddingProvider | None = None,
    ) -> None:
        """Bind one service instance to trusted request access.

        Args:
            repository: Durable storage boundary for wiki knowledge.
            access: Trusted tenant and authorization context.
            embeddings: Optional semantic query embedding provider.
        """
        self.repository = repository
        self.access = access
        self.embeddings = embeddings

    def search(self, query: str, *, tags: Sequence[str] = (), limit: int = 8) -> WikiSearchResult:
        """Return bounded, cited lexical or hybrid search results.

        Args:
            query: Natural-language question or search phrase.
            tags: Optional tags every result must contain.
            limit: Maximum number of passages to return.

        Returns:
            Structured authorized evidence and truncation metadata.
        """
        normalized_query = query.strip()
        if not normalized_query:
            raise ValidationPortalError("Wiki search query must not be blank.")
        selected_tags = _normalize_tags(tags)
        query_embedding = (
            tuple(float(value) for value in self.embeddings.embed_query(normalized_query))
            if self.embeddings is not None
            else None
        )
        hits = self.repository.search(
            self.access,
            normalized_query,
            tags=selected_tags,
            limit=limit + 1,
            query_embedding=query_embedding,
        )
        return WikiSearchResult(
            query=normalized_query,
            hits=list(hits[:limit]),
            truncated=len(hits) > limit,
        )

    def get_page(self, slug: str) -> WikiPage:
        """Return one authorized published wiki page.

        Args:
            slug: Page slug to normalize and resolve.

        Returns:
            Authorized published page revision.
        """
        normalized = _normalize_slug(slug)
        page = self.repository.get_page(self.access, normalized)
        if page is None:
            raise ValidationPortalError(
                "Wiki page was not found.",
                details={"failure_reason": "page_not_found", "slug": normalized},
            )
        return page

    def list_pages(
        self,
        *,
        prefix: str = "",
        tags: Sequence[str] = (),
        include_stale: bool = True,
        limit: int = 50,
    ) -> WikiPageListResult:
        """Return a bounded authorized page catalog.

        Args:
            prefix: Optional page-slug prefix.
            tags: Optional tags every page must contain.
            include_stale: Whether stale published revisions may be returned.
            limit: Maximum number of page summaries to return.

        Returns:
            Structured page summaries and truncation metadata.
        """
        normalized_prefix = prefix.strip().casefold()
        if normalized_prefix and not _SLUG.fullmatch(normalized_prefix):
            raise ValidationPortalError("Wiki page prefix is invalid.")
        pages = self.repository.list_pages(
            self.access,
            prefix=normalized_prefix,
            tags=_normalize_tags(tags),
            include_stale=include_stale,
            limit=limit + 1,
        )
        return WikiPageListResult(
            pages=page_summaries(pages[:limit]),
            truncated=len(pages) > limit,
        )

    def provenance(self, slug: str) -> WikiProvenance:
        """Return the complete citation set for a published revision.

        Args:
            slug: Page slug to normalize and resolve.

        Returns:
            Immutable revision identity, freshness, and citations.
        """
        page = self.get_page(slug)
        return WikiProvenance(
            slug=page.slug,
            revision_id=page.revision_id,
            content_hash=page.content_hash,
            stale=page.stale,
            citations=page.citations,
        )


def _normalize_slug(value: str) -> str:
    """Normalize and validate a page slug without tenant identifiers.

    Args:
        value: Caller-provided page slug.

    Returns:
        Normalized lowercase page slug.
    """
    selected = value.strip().casefold()
    if not _SLUG.fullmatch(selected):
        raise ValidationPortalError("Wiki page slug is invalid.")
    return selected


def _normalize_tags(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize tags and reject ambiguous or oversized filters.

    Args:
        values: Caller-provided tag filters.

    Returns:
        Deduplicated normalized tags in caller order.
    """
    selected = tuple(dict.fromkeys(value.strip().casefold() for value in values))
    if len(selected) > 20 or any(not _TAG.fullmatch(value) for value in selected):
        raise ValidationPortalError("Wiki tags must be 1 to 20 slug-like values.")
    return selected
