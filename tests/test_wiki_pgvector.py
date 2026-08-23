"""Test the PostgreSQL and pgvector wiki repository without a live database."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from mcp_portal.config import WikiSettings
from mcp_portal.errors import UpstreamPortalError, ValidationPortalError
from mcp_portal.wiki.models import (
    WikiAccess,
    WikiCitation,
    WikiPage,
    WikiPageRecord,
    WikiPassageRecord,
)
from mcp_portal.wiki.pgvector import (
    PgVectorWikiRepository,
    _access_predicates,
    _build_tables,
    _database_error,
    _fuse_candidates,
    _page_from_row,
    _page_values,
    _passage_values,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
HASH = "sha256:" + ("b" * 64)


class FakeResult:
    """Provide the subset of SQLAlchemy result behavior used by the repository."""

    def __init__(
        self,
        *,
        scalar: Any = None,
        rows: Iterable[dict[str, Any]] = (),
    ) -> None:
        """Store a scalar and mapping rows for one simulated execution."""
        self.scalar = scalar
        self.rows = list(rows)

    def scalar_one_or_none(self) -> Any:
        """Return the configured optional scalar."""
        return self.scalar

    def mappings(self) -> FakeResult:
        """Return this mapping-result façade."""
        return self

    def first(self) -> dict[str, Any] | None:
        """Return the first configured row when present."""
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        """Return all configured rows."""
        return self.rows


class FakeConnection:
    """Record statements and return queued results or failures."""

    def __init__(self, outcomes: Iterable[FakeResult | BaseException] = ()) -> None:
        """Initialize the deterministic execution queue."""
        self.outcomes = list(outcomes)
        self.statements: list[Any] = []

    def __enter__(self) -> FakeConnection:
        """Enter a simulated engine context manager."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit a simulated engine context manager."""

    def execute(self, statement: Any) -> FakeResult:
        """Record a statement and consume its configured outcome."""
        self.statements.append(statement)
        outcome = self.outcomes.pop(0) if self.outcomes else FakeResult()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeEngine:
    """Return one fake connection for read and transaction contexts."""

    def __init__(self, connection: FakeConnection) -> None:
        """Store the fake connection returned by every context."""
        self.connection = connection

    def begin(self) -> FakeConnection:
        """Return a simulated transaction context."""
        return self.connection

    def connect(self) -> FakeConnection:
        """Return a simulated read context."""
        return self.connection


def settings(*, auto_initialize: bool = False) -> WikiSettings:
    """Create compact pgvector settings for unit tests."""
    return WikiSettings(
        sqlalchemy_url="postgresql+psycopg://wiki.invalid/wiki",
        embedding_dimensions=2,
        search_candidates=10,
        auto_initialize=auto_initialize,
    )


def access() -> WikiAccess:
    """Create one trusted repository access context."""
    return WikiAccess(
        tenant_partition="tenant-hash",
        subject="subject-1",
        client_id="client-1",
        scopes=frozenset({"wiki.read", "operations.read"}),
    )


def citation() -> WikiCitation:
    """Create one deterministic source citation."""
    return WikiCitation(
        citation_id="citation-1",
        source_id="source-1",
        source_revision="git:abc123",
        title="Operations Runbook",
        heading="Rollback",
        source_uri="https://docs.example/runbook",
        source_updated_at=NOW,
        content_hash=HASH,
    )


def page_record(*, status: str = "published") -> WikiPageRecord:
    """Create one deterministic page record."""
    return WikiPageRecord(
        tenant_partition="tenant-hash",
        required_scopes=frozenset({"operations.read"}),
        page=WikiPage(
            slug="deployments",
            revision_id="revision-1",
            title="Deployments",
            summary="Deployment and rollback guidance.",
            markdown="# Deployments\n\nUse the runbook. [citation-1]",
            status=status,
            updated_at=NOW,
            source_updated_at=NOW,
            content_hash=HASH,
            tags=["operations"],
            citations=[citation()],
        ),
    )


def passage_record(*, embedding: tuple[float, ...] | None = (1.0, 0.0)) -> WikiPassageRecord:
    """Create one deterministic passage record."""
    return WikiPassageRecord(
        tenant_partition="tenant-hash",
        passage_id="passage-1",
        page_slug="deployments",
        text="Rollback the last production deployment.",
        citation=citation(),
        tags=("operations",),
        required_scopes=frozenset({"operations.read"}),
        embedding=embedding,
    )


def page_row() -> dict[str, Any]:
    """Return a database-shaped page revision mapping."""
    return _page_values(page_record())


def passage_row(identifier: str = "passage-1") -> dict[str, Any]:
    """Return a database-shaped passage mapping."""
    row = _passage_values(passage_record())
    row["passage_id"] = identifier
    return row


def repository(
    *outcomes: FakeResult | BaseException,
) -> tuple[PgVectorWikiRepository, FakeConnection]:
    """Create a repository backed by a deterministic fake engine."""
    connection = FakeConnection(outcomes)
    return PgVectorWikiRepository(FakeEngine(connection), settings()), connection  # type: ignore[arg-type]


def test_table_metadata_uses_halfvec_full_text_and_database_acl() -> None:
    """Verify table construction captures the essential persistence invariants."""
    tables = _build_tables(settings())
    predicates = _access_predicates(tables.passages, access())

    assert str(tables.passages.c.embedding.type) == "HALFVEC(2)"
    assert tables.passages.c.search_vector.computed is not None
    assert {index.name for index in tables.passages.indexes} == {
        "ix_wiki_passages_embedding_hnsw",
        "ix_wiki_passages_search",
        "ix_wiki_passages_source",
    }
    assert {index.name for index in tables.pages.indexes} == {"ix_wiki_pages_updated"}
    assert len(tables.pages.foreign_key_constraints) == 1
    assert len(predicates) == 2


def test_serialization_and_rank_fusion_preserve_citations() -> None:
    """Verify persistent mappings round-trip and hybrid ranks are normalized."""
    restored = _page_from_row(page_row())
    lexical = passage_row("passage-lexical")
    hybrid = passage_row("passage-hybrid")
    results = _fuse_candidates([hybrid, lexical], [hybrid], limit=2)

    assert restored == page_record().page
    assert results[0].passage_id == "passage-hybrid"
    assert results[0].retrieval == ["lexical", "vector"]
    assert results[0].relevance_score == 1.0
    assert results[1].citation.citation_id == "citation-1"
    assert _fuse_candidates([], [], limit=5) == ()


def test_initialize_and_ping_execute_schema_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify initialization and readiness use lifecycle-managed connections."""
    storage, connection = repository()
    created: list[Any] = []
    monkeypatch.setattr(storage.tables.metadata, "create_all", created.append)

    storage.initialize()
    storage.ping()

    assert len(connection.statements) == 3
    assert created == [connection]


def test_save_page_enforces_revision_immutability_and_publication() -> None:
    """Verify revision inserts and published pointers follow immutable semantics."""
    storage, connection = repository(FakeResult(), FakeResult(), FakeResult())
    storage.save_page(page_record())
    assert len(connection.statements) == 3

    draft, draft_connection = repository(FakeResult(), FakeResult())
    draft.save_page(page_record(status="draft"))
    assert len(draft_connection.statements) == 2

    conflict, _ = repository(FakeResult(scalar="sha256:" + ("c" * 64)))
    with pytest.raises(ValidationPortalError, match="immutable"):
        conflict.save_page(page_record())


def test_save_passage_validates_dimensions_and_upserts() -> None:
    """Verify passage embeddings match schema dimensions before upsert."""
    storage, connection = repository(FakeResult())
    storage.save_passage(passage_record())
    assert len(connection.statements) == 1

    with pytest.raises(ValidationPortalError, match="dimensions"):
        storage.save_passage(passage_record(embedding=(1.0,)))


def test_get_and_list_pages_deserialize_authorized_database_results() -> None:
    """Verify page reads deserialize rows after database-side filters."""
    storage, _ = repository(FakeResult(rows=[page_row()]), FakeResult(rows=[page_row()]))

    assert storage.get_page(access(), "deployments") == page_record().page
    assert storage.list_pages(
        access(),
        prefix="deploy",
        tags=("operations",),
        include_stale=False,
        limit=5,
    ) == (page_record().page,)

    missing, _ = repository(FakeResult())
    assert missing.get_page(access(), "missing") is None


def test_search_runs_lexical_and_optional_vector_retrievers() -> None:
    """Verify search performs full-text-only or fused candidate retrieval."""
    lexical, lexical_connection = repository(FakeResult(rows=[passage_row()]))
    lexical_hits = lexical.search(access(), "rollback", tags=(), limit=5)
    assert lexical_hits[0].retrieval == ["lexical"]
    assert len(lexical_connection.statements) == 1

    hybrid, hybrid_connection = repository(
        FakeResult(rows=[passage_row()]), FakeResult(rows=[passage_row()])
    )
    hybrid_hits = hybrid.search(
        access(),
        "rollback",
        tags=("operations",),
        limit=5,
        query_embedding=(1.0, 0.0),
    )
    assert hybrid_hits[0].retrieval == ["lexical", "vector"]
    assert len(hybrid_connection.statements) == 2

    with pytest.raises(ValidationPortalError, match="dimensions"):
        hybrid.search(access(), "rollback", tags=(), limit=5, query_embedding=(1.0,))


@pytest.mark.parametrize(
    "operation",
    ["initialize", "readiness", "save_page", "save_passage", "get_page", "list_pages", "search"],
)
def test_database_failures_are_sanitized(operation: str) -> None:
    """Verify all database errors use stable public metadata without SQL details."""
    error = SQLAlchemyError("postgresql://user:secret@database/private")
    storage, _ = repository(error)

    with pytest.raises(UpstreamPortalError) as captured:
        if operation == "initialize":
            storage.initialize()
        elif operation == "readiness":
            storage.ping()
        elif operation == "save_page":
            storage.save_page(page_record())
        elif operation == "save_passage":
            storage.save_passage(passage_record())
        elif operation == "get_page":
            storage.get_page(access(), "deployments")
        elif operation == "list_pages":
            storage.list_pages(access(), prefix="", tags=(), include_stale=True, limit=5)
        else:
            storage.search(access(), "rollback", tags=(), limit=5)

    assert captured.value.details == {
        "backend": "postgresql_pgvector",
        "operation": operation,
        "error_type": "SQLAlchemyError",
    }
    assert "secret" not in str(captured.value.to_public_dict())


def test_repository_rejects_non_postgresql_configuration() -> None:
    """Verify the persistent adapter cannot silently use a non-PostgreSQL backend."""
    invalid = WikiSettings(
        sqlalchemy_url="sqlite:///wiki.db",
        embedding_dimensions=2,
        search_candidates=10,
    )
    with pytest.raises(ValidationPortalError, match="PostgreSQL"):
        PgVectorWikiRepository(FakeEngine(FakeConnection()), invalid)  # type: ignore[arg-type]


def test_database_error_helper_retains_internal_cause() -> None:
    """Verify sanitized errors preserve the original exception for internal logging."""
    cause = SQLAlchemyError("internal")
    error = _database_error("search", cause)
    assert error.__cause__ is cause
    assert error.namespace == "wiki"
