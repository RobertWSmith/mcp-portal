"""Repository contracts and deterministic test storage for wiki knowledge."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Protocol

from mcp_portal.errors import ValidationPortalError
from mcp_portal.wiki.models import (
    WikiAccess,
    WikiPage,
    WikiPageRecord,
    WikiPassageRecord,
    WikiSearchHit,
    WikiSourceRecord,
    summarize_page,
)

_WORD = re.compile(r"[a-z0-9]+")


class WikiRepository(Protocol):
    """Persistent storage boundary used by the wiki namespace."""

    def ping(self) -> None:
        """Raise when the persistent backend is unavailable."""
        ...

    def search(
        self,
        access: WikiAccess,
        query: str,
        *,
        tags: Sequence[str],
        limit: int,
        query_embedding: Sequence[float] | None = None,
    ) -> tuple[WikiSearchHit, ...]:
        """Return authorized lexical or hybrid passage matches.

        Args:
            access: Trusted tenant and authorization predicates.
            query: Normalized natural-language search query.
            tags: Tags every returned passage must contain.
            limit: Maximum number of passages to return.
            query_embedding: Optional semantic embedding for hybrid retrieval.

        Returns:
            Authorized cited passages in relevance order.
        """
        ...

    def get_page(self, access: WikiAccess, slug: str) -> WikiPage | None:
        """Return the authorized published revision for a slug.

        Args:
            access: Trusted tenant and authorization predicates.
            slug: Normalized page slug.

        Returns:
            Visible published page, or None when absent or unauthorized.
        """
        ...

    def list_pages(
        self,
        access: WikiAccess,
        *,
        prefix: str,
        tags: Sequence[str],
        include_stale: bool,
        limit: int,
    ) -> tuple[WikiPage, ...]:
        """Return authorized published page revisions.

        Args:
            access: Trusted tenant and authorization predicates.
            prefix: Optional normalized page-slug prefix.
            tags: Tags every returned page must contain.
            include_stale: Whether stale published revisions may be returned.
            limit: Maximum number of pages to return.

        Returns:
            Authorized pages in stable slug order.
        """
        ...

    def save_page(self, record: WikiPageRecord) -> None:
        """Persist a page revision and publish its pointer when applicable.

        Args:
            record: Tenant-partitioned immutable page revision to store.
        """
        ...

    def save_passage(self, record: WikiPassageRecord) -> None:
        """Persist or replace one source passage revision.

        Args:
            record: Tenant-partitioned evidence passage to store.
        """
        ...

    def ingest_source(
        self,
        source: WikiSourceRecord,
        page: WikiPageRecord,
        passages: Sequence[WikiPassageRecord],
    ) -> None:
        """Atomically publish a source revision and replace its passages.

        Args:
            source: Durable current-source metadata.
            page: Immutable published page revision derived from the source.
            passages: Complete passage set for the new source revision.
        """
        ...


class InMemoryWikiRepository:
    """Non-durable repository used only for deterministic tests."""

    def __init__(
        self,
        *,
        pages: Iterable[WikiPageRecord] = (),
        passages: Iterable[WikiPassageRecord] = (),
    ) -> None:
        """Initialize records and published-page pointers.

        Args:
            pages: Initial immutable page revisions.
            passages: Initial evidence passages.
        """
        self._pages: dict[tuple[str, str, str], WikiPageRecord] = {}
        self._published: dict[tuple[str, str], str] = {}
        self._passages: dict[tuple[str, str], WikiPassageRecord] = {}
        self._sources: dict[tuple[str, str], WikiSourceRecord] = {}
        for page in pages:
            self.save_page(page)
        for passage in passages:
            self.save_passage(passage)

    def ping(self) -> None:
        """Confirm that the in-memory test adapter is available."""

    def save_page(self, record: WikiPageRecord) -> None:
        """Store one page revision and update its published pointer.

        Args:
            record: Tenant-partitioned immutable page revision to store.
        """
        key = (record.tenant_partition, record.page.slug, record.page.revision_id)
        existing = self._pages.get(key)
        if existing is not None and existing.page.content_hash != record.page.content_hash:
            raise ValueError("Wiki revisions are immutable")
        self._pages[key] = record
        if record.page.status == "published":
            self._published[(record.tenant_partition, record.page.slug)] = record.page.revision_id

    def save_passage(self, record: WikiPassageRecord) -> None:
        """Store or replace one passage.

        Args:
            record: Tenant-partitioned evidence passage to store.
        """
        self._passages[(record.tenant_partition, record.passage_id)] = record

    def ingest_source(
        self,
        source: WikiSourceRecord,
        page: WikiPageRecord,
        passages: Sequence[WikiPassageRecord],
    ) -> None:
        """Atomically publish a source revision and replace its passages.

        Args:
            source: Durable current-source metadata.
            page: Immutable published page revision derived from the source.
            passages: Complete passage set for the new source revision.
        """
        _validate_ingestion_records(source, page, passages)
        page_key = (page.tenant_partition, page.page.slug, page.page.revision_id)
        existing = self._pages.get(page_key)
        if existing is not None and existing.page.content_hash != page.page.content_hash:
            raise ValueError("Wiki revisions are immutable")
        pages = dict(self._pages)
        published = dict(self._published)
        stored_passages = {
            key: record
            for key, record in self._passages.items()
            if not (
                record.tenant_partition == source.tenant_partition
                and record.citation.source_id == source.source_id
            )
        }
        pages[page_key] = page
        published[(page.tenant_partition, page.page.slug)] = page.page.revision_id
        stored_passages.update(
            {(record.tenant_partition, record.passage_id): record for record in passages}
        )
        sources = dict(self._sources)
        sources[(source.tenant_partition, source.source_id)] = source
        self._pages = pages
        self._published = published
        self._passages = stored_passages
        self._sources = sources

    def get_page(self, access: WikiAccess, slug: str) -> WikiPage | None:
        """Return the visible published page for ``slug``.

        Args:
            access: Trusted tenant and authorization predicates.
            slug: Normalized page slug.

        Returns:
            Visible published page, or None when absent or unauthorized.
        """
        revision = self._published.get((access.tenant_partition, slug))
        if revision is None:
            return None
        record = self._pages[(access.tenant_partition, slug, revision)]
        return record.page if _visible(record.required_scopes, access) else None

    def list_pages(
        self,
        access: WikiAccess,
        *,
        prefix: str,
        tags: Sequence[str],
        include_stale: bool,
        limit: int,
    ) -> tuple[WikiPage, ...]:
        """Return a stable ordered page listing.

        Args:
            access: Trusted tenant and authorization predicates.
            prefix: Optional normalized page-slug prefix.
            tags: Tags every returned page must contain.
            include_stale: Whether stale published revisions may be returned.
            limit: Maximum number of pages to return.

        Returns:
            Authorized pages in stable slug order.
        """
        required_tags = set(tags)
        selected: list[WikiPage] = []
        for (tenant_partition, slug), revision in self._published.items():
            if tenant_partition != access.tenant_partition or not slug.startswith(prefix):
                continue
            record = self._pages[(tenant_partition, slug, revision)]
            page = record.page
            if not _visible(record.required_scopes, access):
                continue
            if required_tags and not required_tags <= set(page.tags):
                continue
            if page.stale and not include_stale:
                continue
            selected.append(page)
        return tuple(sorted(selected, key=lambda item: item.slug)[:limit])

    def search(
        self,
        access: WikiAccess,
        query: str,
        *,
        tags: Sequence[str],
        limit: int,
        query_embedding: Sequence[float] | None = None,
    ) -> tuple[WikiSearchHit, ...]:
        """Perform deterministic lexical or hybrid retrieval.

        Args:
            access: Trusted tenant and authorization predicates.
            query: Normalized natural-language search query.
            tags: Tags every returned passage must contain.
            limit: Maximum number of passages to return.
            query_embedding: Optional semantic embedding for hybrid retrieval.

        Returns:
            Authorized cited passages in relevance order.
        """
        terms = set(_WORD.findall(query.casefold()))
        required_tags = set(tags)
        scored: list[WikiSearchHit] = []
        for (tenant_partition, _), record in self._passages.items():
            if tenant_partition != access.tenant_partition:
                continue
            if not _visible(record.required_scopes, access):
                continue
            if required_tags and not required_tags <= set(record.tags):
                continue
            haystack = " ".join(
                (record.citation.title, record.citation.heading or "", record.text)
            ).casefold()
            matched = sum(1 for term in terms if term in haystack)
            lexical = matched / len(terms) if terms else 0.0
            vector = _cosine_similarity(query_embedding, record.embedding)
            retrieval = []
            if lexical > 0:
                retrieval.append("lexical")
            if vector is not None:
                retrieval.append("vector")
            if not retrieval:
                continue
            score = lexical if vector is None else (0.45 * lexical) + (0.55 * vector)
            scored.append(
                WikiSearchHit(
                    passage_id=record.passage_id,
                    page_slug=record.page_slug,
                    snippet=record.text[:4_000],
                    citation=record.citation,
                    tags=list(record.tags),
                    relevance_score=max(0.0, min(1.0, score)),
                    retrieval=retrieval,
                )
            )
        scored.sort(key=lambda item: (-item.relevance_score, item.citation.citation_id))
        return tuple(scored[:limit])


def _visible(required_scopes: frozenset[str], access: WikiAccess) -> bool:
    """Return whether trusted caller scopes satisfy a stored ACL.

    Args:
        required_scopes: Scopes stored with a page or passage.
        access: Verified caller access context.

    Returns:
        True when the caller possesses every required scope.
    """
    return required_scopes <= access.scopes


def _cosine_similarity(left: Sequence[float] | None, right: Sequence[float] | None) -> float | None:
    """Return normalized cosine similarity when both vectors are usable.

    Args:
        left: Optional query embedding.
        right: Optional stored passage embedding.

    Returns:
        Similarity normalized to zero through one, or None for unusable vectors.
    """
    if left is None or right is None or len(left) != len(right) or not left:
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    cosine = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    return (max(-1.0, min(1.0, cosine)) + 1.0) / 2.0


def page_summaries(pages: Sequence[WikiPage]) -> list:
    """Convert page revisions to their bounded listing forms.

    Args:
        pages: Complete page revisions to summarize.

    Returns:
        Bounded page summaries in the original order.
    """
    return [summarize_page(page) for page in pages]


def _validate_ingestion_records(
    source: WikiSourceRecord,
    page: WikiPageRecord,
    passages: Sequence[WikiPassageRecord],
) -> None:
    """Validate one complete source transaction before storage mutation.

    Args:
        source: Durable current-source metadata.
        page: Immutable published page revision derived from the source.
        passages: Complete passage set for the new source revision.
    """
    if page.page.status != "published" or not passages:
        raise ValidationPortalError(
            "Wiki ingestion requires a published page and at least one passage."
        )
    if (
        source.tenant_partition != page.tenant_partition
        or source.page_slug != page.page.slug
        or source.page_revision_id != page.page.revision_id
    ):
        raise ValidationPortalError("Wiki ingestion source and page metadata do not match.")
    if any(
        record.tenant_partition != source.tenant_partition
        or record.page_slug != source.page_slug
        or record.citation.source_id != source.source_id
        or record.citation.source_revision != source.source_revision
        for record in passages
    ):
        raise ValidationPortalError("Wiki ingestion passages do not match the source transaction.")
