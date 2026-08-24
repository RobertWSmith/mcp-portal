"""Durable PostgreSQL/pgvector repository for wiki pages and evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from sqlalchemy import (
    Boolean,
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    and_,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, insert as postgresql_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateSchema

from mcp_portal.config import WikiSettings
from mcp_portal.errors import UpstreamPortalError, ValidationPortalError
from mcp_portal.wiki.models import (
    WikiAccess,
    WikiCitation,
    WikiPage,
    WikiPageRecord,
    WikiPassageRecord,
    WikiSearchHit,
    WikiSourceRecord,
)
from mcp_portal.wiki.repository import _validate_ingestion_records


@dataclass(frozen=True)
class WikiTables:
    """SQLAlchemy table set owned by one configured wiki schema.

    Attributes:
        metadata: SQLAlchemy metadata bound to the configured schema.
        pages: Published page-pointer table.
        revisions: Immutable page-revision table.
        passages: Searchable source-passage table.
        sources: Current ingested-source metadata table.
    """

    metadata: Annotated[MetaData, "SQLAlchemy metadata bound to the configured schema."]
    pages: Annotated[Table, "Published page-pointer table."]
    revisions: Annotated[Table, "Immutable page-revision table."]
    passages: Annotated[Table, "Searchable source-passage table."]
    sources: Annotated[Table, "Current ingested-source metadata table."]


class PgVectorWikiRepository:
    """Persist wiki revisions and perform ACL-filtered hybrid retrieval."""

    def __init__(self, engine: Engine, settings: WikiSettings) -> None:
        """Bind a repository to one lifecycle-managed PostgreSQL engine.

        Args:
            engine: Dedicated PostgreSQL SQLAlchemy engine.
            settings: Wiki schema, dimensions, and initialization settings.
        """
        if not settings.postgresql_configured:
            raise ValidationPortalError("Wiki persistence requires PostgreSQL.")
        self.engine = engine
        self.settings = settings
        self.tables = _build_tables(settings)
        if settings.auto_initialize:
            self.initialize()

    def initialize(self) -> None:
        """Create pgvector, the configured schema, tables, and indexes."""
        try:
            with self.engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                connection.execute(CreateSchema(self.settings.schema, if_not_exists=True))
                self.tables.metadata.create_all(connection)
        except SQLAlchemyError as error:
            raise _database_error("initialize", error) from error

    def ping(self) -> None:
        """Verify that the durable wiki schema is queryable."""
        try:
            with self.engine.connect() as connection:
                connection.execute(select(self.tables.pages.c.slug).limit(0))
        except SQLAlchemyError as error:
            raise _database_error("readiness", error) from error

    def save_page(self, record: WikiPageRecord) -> None:
        """Append a revision and update the published-page pointer.

        Args:
            record: Tenant-partitioned immutable page revision to store.
        """
        revision_values = _page_values(record)
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(self.tables.revisions.c.content_hash).where(
                        self.tables.revisions.c.tenant_partition == record.tenant_partition,
                        self.tables.revisions.c.slug == record.page.slug,
                        self.tables.revisions.c.revision_id == record.page.revision_id,
                    )
                ).scalar_one_or_none()
                if existing is not None and existing != record.page.content_hash:
                    raise ValidationPortalError(
                        "Wiki revisions are immutable.",
                        details={"slug": record.page.slug, "revision_id": record.page.revision_id},
                    )
                connection.execute(
                    postgresql_insert(self.tables.revisions)
                    .values(**revision_values)
                    .on_conflict_do_nothing(
                        index_elements=("tenant_partition", "slug", "revision_id")
                    )
                )
                if record.page.status == "published":
                    pointer = {
                        "tenant_partition": record.tenant_partition,
                        "slug": record.page.slug,
                        "published_revision_id": record.page.revision_id,
                        "required_scopes": sorted(record.required_scopes),
                        "updated_at": record.page.updated_at,
                    }
                    connection.execute(
                        postgresql_insert(self.tables.pages)
                        .values(**pointer)
                        .on_conflict_do_update(
                            index_elements=("tenant_partition", "slug"),
                            set_={
                                "published_revision_id": record.page.revision_id,
                                "required_scopes": sorted(record.required_scopes),
                                "updated_at": record.page.updated_at,
                            },
                        )
                    )
        except ValidationPortalError:
            raise
        except SQLAlchemyError as error:
            raise _database_error("save_page", error) from error

    def save_passage(self, record: WikiPassageRecord) -> None:
        """Upsert a source passage and its optional semantic embedding.

        Args:
            record: Tenant-partitioned evidence passage to store.
        """
        if (
            record.embedding is not None
            and len(record.embedding) != self.settings.embedding_dimensions
        ):
            raise ValidationPortalError(
                "Wiki passage embedding has the wrong dimensions.",
                details={
                    "expected_dimensions": self.settings.embedding_dimensions,
                    "actual_dimensions": len(record.embedding),
                },
            )
        values = _passage_values(record)
        try:
            statement = postgresql_insert(self.tables.passages).values(**values)
            updates = {
                name: getattr(statement.excluded, name)
                for name in values
                if name not in {"tenant_partition", "passage_id"}
            }
            with self.engine.begin() as connection:
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=("tenant_partition", "passage_id"), set_=updates
                    )
                )
        except SQLAlchemyError as error:
            raise _database_error("save_passage", error) from error

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
        for passage in passages:
            if (
                passage.embedding is not None
                and len(passage.embedding) != self.settings.embedding_dimensions
            ):
                raise ValidationPortalError(
                    "Wiki passage embedding has the wrong dimensions.",
                    details={
                        "expected_dimensions": self.settings.embedding_dimensions,
                        "actual_dimensions": len(passage.embedding),
                    },
                )
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(self.tables.revisions.c.content_hash).where(
                        self.tables.revisions.c.tenant_partition == page.tenant_partition,
                        self.tables.revisions.c.slug == page.page.slug,
                        self.tables.revisions.c.revision_id == page.page.revision_id,
                    )
                ).scalar_one_or_none()
                if existing is not None and existing != page.page.content_hash:
                    raise ValidationPortalError(
                        "Wiki revisions are immutable.",
                        details={
                            "slug": page.page.slug,
                            "revision_id": page.page.revision_id,
                        },
                    )
                connection.execute(
                    postgresql_insert(self.tables.revisions)
                    .values(**_page_values(page))
                    .on_conflict_do_nothing(
                        index_elements=("tenant_partition", "slug", "revision_id")
                    )
                )
                pointer = {
                    "tenant_partition": page.tenant_partition,
                    "slug": page.page.slug,
                    "published_revision_id": page.page.revision_id,
                    "required_scopes": sorted(page.required_scopes),
                    "updated_at": page.page.updated_at,
                }
                connection.execute(
                    postgresql_insert(self.tables.pages)
                    .values(**pointer)
                    .on_conflict_do_update(
                        index_elements=("tenant_partition", "slug"),
                        set_={
                            "published_revision_id": page.page.revision_id,
                            "required_scopes": sorted(page.required_scopes),
                            "updated_at": page.page.updated_at,
                        },
                    )
                )
                connection.execute(
                    delete(self.tables.passages).where(
                        self.tables.passages.c.tenant_partition == source.tenant_partition,
                        self.tables.passages.c.source_id == source.source_id,
                    )
                )
                passage_values = [_passage_values(record) for record in passages]
                passage_insert = postgresql_insert(self.tables.passages).values(passage_values)
                passage_updates = {
                    name: getattr(passage_insert.excluded, name)
                    for name in passage_values[0]
                    if name not in {"tenant_partition", "passage_id"}
                }
                connection.execute(
                    passage_insert.on_conflict_do_update(
                        index_elements=("tenant_partition", "passage_id"),
                        set_=passage_updates,
                    )
                )
                source_values = _source_values(source)
                source_insert = postgresql_insert(self.tables.sources).values(**source_values)
                source_updates = {
                    name: getattr(source_insert.excluded, name)
                    for name in source_values
                    if name not in {"tenant_partition", "source_id"}
                }
                connection.execute(
                    source_insert.on_conflict_do_update(
                        index_elements=("tenant_partition", "source_id"),
                        set_=source_updates,
                    )
                )
        except ValidationPortalError:
            raise
        except SQLAlchemyError as error:
            raise _database_error("ingest_source", error) from error

    def get_page(self, access: WikiAccess, slug: str) -> WikiPage | None:
        """Return a page after tenant and ACL filtering in PostgreSQL.

        Args:
            access: Trusted tenant and authorization predicates.
            slug: Normalized page slug.

        Returns:
            Visible published page, or None when absent or unauthorized.
        """
        join = self.tables.pages.join(
            self.tables.revisions,
            and_(
                self.tables.pages.c.tenant_partition == self.tables.revisions.c.tenant_partition,
                self.tables.pages.c.slug == self.tables.revisions.c.slug,
                self.tables.pages.c.published_revision_id == self.tables.revisions.c.revision_id,
            ),
        )
        statement = (
            select(*self.tables.revisions.c)
            .select_from(join)
            .where(
                *_access_predicates(self.tables.pages, access),
                self.tables.pages.c.slug == slug,
            )
            .limit(1)
        )
        try:
            with self.engine.connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as error:
            raise _database_error("get_page", error) from error
        return _page_from_row(row) if row is not None else None

    def list_pages(
        self,
        access: WikiAccess,
        *,
        prefix: str,
        tags: Sequence[str],
        include_stale: bool,
        limit: int,
    ) -> tuple[WikiPage, ...]:
        """List published pages with filters evaluated by PostgreSQL.

        Args:
            access: Trusted tenant and authorization predicates.
            prefix: Optional normalized page-slug prefix.
            tags: Tags every returned page must contain.
            include_stale: Whether stale published revisions may be returned.
            limit: Maximum number of pages to return.

        Returns:
            Authorized pages in stable slug order.
        """
        join = self.tables.pages.join(
            self.tables.revisions,
            and_(
                self.tables.pages.c.tenant_partition == self.tables.revisions.c.tenant_partition,
                self.tables.pages.c.slug == self.tables.revisions.c.slug,
                self.tables.pages.c.published_revision_id == self.tables.revisions.c.revision_id,
            ),
        )
        conditions = [*_access_predicates(self.tables.pages, access)]
        if prefix:
            conditions.append(self.tables.pages.c.slug.like(f"{prefix}%"))
        if tags:
            conditions.append(self.tables.revisions.c.tags.contains(list(tags)))
        if not include_stale:
            conditions.append(self.tables.revisions.c.stale.is_(False))
        statement = (
            select(*self.tables.revisions.c)
            .select_from(join)
            .where(*conditions)
            .order_by(self.tables.pages.c.slug)
            .limit(limit)
        )
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as error:
            raise _database_error("list_pages", error) from error
        return tuple(_page_from_row(row) for row in rows)

    def search(
        self,
        access: WikiAccess,
        query: str,
        *,
        tags: Sequence[str],
        limit: int,
        query_embedding: Sequence[float] | None = None,
    ) -> tuple[WikiSearchHit, ...]:
        """Merge full-text and pgvector candidates using weighted RRF.

        Args:
            access: Trusted tenant and authorization predicates.
            query: Normalized natural-language search query.
            tags: Tags every returned passage must contain.
            limit: Maximum number of passages to return.
            query_embedding: Optional semantic embedding for hybrid retrieval.

        Returns:
            Authorized cited passages in fused relevance order.
        """
        if (
            query_embedding is not None
            and len(query_embedding) != self.settings.embedding_dimensions
        ):
            raise ValidationPortalError(
                "Wiki query embedding has the wrong dimensions.",
                details={
                    "expected_dimensions": self.settings.embedding_dimensions,
                    "actual_dimensions": len(query_embedding),
                },
            )
        candidate_limit = max(limit, self.settings.search_candidates)
        conditions = [*_access_predicates(self.tables.passages, access)]
        if tags:
            conditions.append(self.tables.passages.c.tags.contains(list(tags)))
        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank_cd(self.tables.passages.c.search_vector, tsquery)
        lexical = (
            select(*self.tables.passages.c, rank.label("retrieval_value"))
            .where(*conditions, self.tables.passages.c.search_vector.op("@@")(tsquery))
            .order_by(rank.desc(), self.tables.passages.c.passage_id)
            .limit(candidate_limit)
        )
        vector = None
        if query_embedding is not None:
            distance = self.tables.passages.c.embedding.cosine_distance(list(query_embedding))
            vector = (
                select(*self.tables.passages.c, distance.label("retrieval_value"))
                .where(*conditions, self.tables.passages.c.embedding.is_not(None))
                .order_by(distance, self.tables.passages.c.passage_id)
                .limit(candidate_limit)
            )
        try:
            with self.engine.connect() as connection:
                lexical_rows = connection.execute(lexical).mappings().all()
                vector_rows = (
                    connection.execute(vector).mappings().all() if vector is not None else []
                )
        except SQLAlchemyError as error:
            raise _database_error("search", error) from error
        return _fuse_candidates(lexical_rows, vector_rows, limit)


def _build_tables(settings: WikiSettings) -> WikiTables:
    """Build PostgreSQL-specific SQLAlchemy metadata for one wiki schema.

    Args:
        settings: Wiki schema name and semantic embedding dimensions.

    Returns:
        SQLAlchemy metadata and its four persistent wiki tables.
    """
    try:
        from pgvector.sqlalchemy import HALFVEC
    except ImportError as error:
        raise ValidationPortalError(
            "Wiki persistence requires the optional pgvector package.",
            details={"install_extra": "wiki"},
        ) from error

    metadata = MetaData(schema=settings.schema)
    revisions = Table(
        "page_revisions",
        metadata,
        Column("tenant_partition", String(64), nullable=False),
        Column("slug", String(200), nullable=False),
        Column("revision_id", String(160), nullable=False),
        Column("title", String(500), nullable=False),
        Column("summary", Text, nullable=False),
        Column("markdown", Text, nullable=False),
        Column("status", String(20), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("source_updated_at", DateTime(timezone=True)),
        Column("content_hash", String(71), nullable=False),
        Column("tags", JSONB, nullable=False, default=list),
        Column("stale", Boolean, nullable=False, default=False),
        Column("citations", JSONB, nullable=False, default=list),
        Column("required_scopes", JSONB, nullable=False, default=list),
        PrimaryKeyConstraint("tenant_partition", "slug", "revision_id"),
        CheckConstraint("status IN ('draft', 'published', 'archived')"),
        CheckConstraint("jsonb_typeof(tags) = 'array'"),
        CheckConstraint("jsonb_typeof(citations) = 'array'"),
        CheckConstraint("jsonb_typeof(required_scopes) = 'array'"),
    )
    pages = Table(
        "pages",
        metadata,
        Column("tenant_partition", String(64), nullable=False),
        Column("slug", String(200), nullable=False),
        Column("published_revision_id", String(160), nullable=False),
        Column("required_scopes", JSONB, nullable=False, default=list),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_partition", "slug"),
        ForeignKeyConstraint(
            ("tenant_partition", "slug", "published_revision_id"),
            (
                revisions.c.tenant_partition,
                revisions.c.slug,
                revisions.c.revision_id,
            ),
        ),
        CheckConstraint("jsonb_typeof(required_scopes) = 'array'"),
    )
    sources = Table(
        "sources",
        metadata,
        Column("tenant_partition", String(64), nullable=False),
        Column("source_id", String(512), nullable=False),
        Column("source_revision", String(160), nullable=False),
        Column("source_uri", String(2_048), nullable=False),
        Column("title", String(500), nullable=False),
        Column("document_format", String(40), nullable=False),
        Column("content_hash", String(71), nullable=False),
        Column("source_updated_at", DateTime(timezone=True), nullable=False),
        Column("ingested_at", DateTime(timezone=True), nullable=False),
        Column("byte_count", BigInteger, nullable=False),
        Column("page_slug", String(200), nullable=False),
        Column("page_revision_id", String(160), nullable=False),
        Column("tags", JSONB, nullable=False, default=list),
        Column("required_scopes", JSONB, nullable=False, default=list),
        PrimaryKeyConstraint("tenant_partition", "source_id"),
        ForeignKeyConstraint(
            ("tenant_partition", "page_slug", "page_revision_id"),
            (
                revisions.c.tenant_partition,
                revisions.c.slug,
                revisions.c.revision_id,
            ),
        ),
        CheckConstraint("byte_count > 0"),
        CheckConstraint("jsonb_typeof(tags) = 'array'"),
        CheckConstraint("jsonb_typeof(required_scopes) = 'array'"),
    )
    passages = Table(
        "passages",
        metadata,
        Column("tenant_partition", String(64), nullable=False),
        Column("passage_id", String(160), nullable=False),
        Column("page_slug", String(200)),
        Column("text", Text, nullable=False),
        Column("source_id", String(512), nullable=False),
        Column("source_revision", String(160), nullable=False),
        Column("title", String(500), nullable=False),
        Column("heading", String(500)),
        Column("source_uri", String(2_048), nullable=False),
        Column("source_updated_at", DateTime(timezone=True), nullable=False),
        Column("content_hash", String(71), nullable=False),
        Column("citation_id", String(160), nullable=False),
        Column("tags", JSONB, nullable=False, default=list),
        Column("required_scopes", JSONB, nullable=False, default=list),
        Column("embedding", HALFVEC(settings.embedding_dimensions)),
        Column(
            "search_vector",
            TSVECTOR,
            Computed(
                "to_tsvector('english'::regconfig, "
                "coalesce(title, '') || ' ' || coalesce(heading, '') || ' ' || text)",
                persisted=True,
            ),
        ),
        PrimaryKeyConstraint("tenant_partition", "passage_id"),
        CheckConstraint("jsonb_typeof(tags) = 'array'"),
        CheckConstraint("jsonb_typeof(required_scopes) = 'array'"),
    )
    Index("ix_wiki_passages_search", passages.c.search_vector, postgresql_using="gin")
    Index(
        "ix_wiki_passages_embedding_hnsw",
        passages.c.embedding,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )
    Index(
        "ix_wiki_pages_updated",
        pages.c.tenant_partition,
        pages.c.updated_at.desc(),
    )
    Index(
        "ix_wiki_passages_source",
        passages.c.tenant_partition,
        passages.c.source_id,
        passages.c.source_revision,
    )
    Index(
        "ix_wiki_sources_updated",
        sources.c.tenant_partition,
        sources.c.ingested_at.desc(),
    )
    return WikiTables(metadata, pages, revisions, passages, sources)


def _access_predicates(table: Table, access: WikiAccess) -> tuple[Any, ...]:
    """Build tenant and JSONB ACL predicates evaluated inside PostgreSQL.

    Args:
        table: Wiki table containing tenant and required-scope columns.
        access: Trusted caller access context.

    Returns:
        SQL expressions that enforce tenant equality and scope containment.
    """
    return (
        table.c.tenant_partition == access.tenant_partition,
        table.c.required_scopes.contained_by(sorted(access.scopes)),
    )


def _page_values(record: WikiPageRecord) -> dict[str, Any]:
    """Serialize an immutable page record for PostgreSQL.

    Args:
        record: Tenant-partitioned page revision.

    Returns:
        Column values suitable for a page-revision insert.
    """
    page = record.page
    return {
        "tenant_partition": record.tenant_partition,
        "slug": page.slug,
        "revision_id": page.revision_id,
        "title": page.title,
        "summary": page.summary,
        "markdown": page.markdown,
        "status": page.status,
        "updated_at": page.updated_at,
        "source_updated_at": page.source_updated_at,
        "content_hash": page.content_hash,
        "tags": page.tags,
        "stale": page.stale,
        "citations": [item.model_dump(mode="json") for item in page.citations],
        "required_scopes": sorted(record.required_scopes),
    }


def _passage_values(record: WikiPassageRecord) -> dict[str, Any]:
    """Serialize a passage and citation for PostgreSQL.

    Args:
        record: Tenant-partitioned evidence passage.

    Returns:
        Column values suitable for a passage upsert.
    """
    citation = record.citation
    return {
        "tenant_partition": record.tenant_partition,
        "passage_id": record.passage_id,
        "page_slug": record.page_slug,
        "text": record.text,
        "source_id": citation.source_id,
        "source_revision": citation.source_revision,
        "title": citation.title,
        "heading": citation.heading,
        "source_uri": citation.source_uri,
        "source_updated_at": citation.source_updated_at,
        "content_hash": citation.content_hash,
        "citation_id": citation.citation_id,
        "tags": list(record.tags),
        "required_scopes": sorted(record.required_scopes),
        "embedding": list(record.embedding) if record.embedding is not None else None,
    }


def _source_values(record: WikiSourceRecord) -> dict[str, Any]:
    """Serialize current source metadata for PostgreSQL.

    Args:
        record: Tenant-partitioned ingested source metadata.

    Returns:
        Column values suitable for a source metadata upsert.
    """
    return {
        "tenant_partition": record.tenant_partition,
        "source_id": record.source_id,
        "source_revision": record.source_revision,
        "source_uri": record.source_uri,
        "title": record.title,
        "document_format": record.document_format,
        "content_hash": record.content_hash,
        "source_updated_at": record.source_updated_at,
        "ingested_at": record.ingested_at,
        "byte_count": record.byte_count,
        "page_slug": record.page_slug,
        "page_revision_id": record.page_revision_id,
        "tags": list(record.tags),
        "required_scopes": sorted(record.required_scopes),
    }


def _page_from_row(row: Any) -> WikiPage:
    """Deserialize one page revision mapping.

    Args:
        row: SQLAlchemy mapping containing revision columns.

    Returns:
        Validated immutable wiki page revision.
    """
    return WikiPage(
        slug=row["slug"],
        revision_id=row["revision_id"],
        title=row["title"],
        summary=row["summary"],
        markdown=row["markdown"],
        status=row["status"],
        updated_at=row["updated_at"],
        source_updated_at=row["source_updated_at"],
        content_hash=row["content_hash"],
        tags=list(row["tags"]),
        stale=row["stale"],
        citations=[WikiCitation.model_validate(item) for item in row["citations"]],
    )


def _search_hit(row: Any, score: float, retrieval: list[str]) -> WikiSearchHit:
    """Deserialize a ranked passage mapping.

    Args:
        row: SQLAlchemy mapping containing passage and citation columns.
        score: Normalized fused relevance score.
        retrieval: Retrieval methods that selected the passage.

    Returns:
        Validated cited search hit.
    """
    return WikiSearchHit(
        passage_id=row["passage_id"],
        page_slug=row["page_slug"],
        snippet=row["text"][:4_000],
        citation=WikiCitation(
            citation_id=row["citation_id"],
            source_id=row["source_id"],
            source_revision=row["source_revision"],
            title=row["title"],
            heading=row["heading"],
            source_uri=row["source_uri"],
            source_updated_at=row["source_updated_at"],
            content_hash=row["content_hash"],
        ),
        tags=list(row["tags"]),
        relevance_score=score,
        retrieval=retrieval,
    )


def _fuse_candidates(
    lexical_rows: Sequence[Any], vector_rows: Sequence[Any], limit: int
) -> tuple[WikiSearchHit, ...]:
    """Fuse candidate ranks without comparing incomparable raw scores.

    Args:
        lexical_rows: Full-text candidates in rank order.
        vector_rows: Semantic candidates in rank order.
        limit: Maximum fused results to return.

    Returns:
        Cited hits ordered by normalized weighted reciprocal rank.
    """
    rows: dict[str, Any] = {}
    scores: dict[str, float] = {}
    methods: dict[str, set[str]] = {}
    for weight, method, candidates in (
        (0.45, "lexical", lexical_rows),
        (0.55, "vector", vector_rows),
    ):
        for rank, row in enumerate(candidates, start=1):
            passage_id = row["passage_id"]
            rows[passage_id] = row
            scores[passage_id] = scores.get(passage_id, 0.0) + weight / (60 + rank)
            methods.setdefault(passage_id, set()).add(method)
    ordered = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
    maximum = max((scores[item] for item in ordered), default=1.0)
    return tuple(
        _search_hit(
            rows[item],
            score=scores[item] / maximum,
            retrieval=sorted(methods[item]),
        )
        for item in ordered
    )


def _database_error(operation: str, error: SQLAlchemyError) -> UpstreamPortalError:
    """Return a sanitized durable-backend failure.

    Args:
        operation: Stable repository operation name.
        error: SQLAlchemy failure retained only as an internal cause.

    Returns:
        Portal error without connection details or SQL text.
    """
    return UpstreamPortalError(
        "Persistent wiki database operation failed.",
        namespace="wiki",
        details={
            "backend": "postgresql_pgvector",
            "operation": operation,
            "error_type": type(error).__name__,
        },
        cause=error,
    )
