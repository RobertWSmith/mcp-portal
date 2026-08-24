"""Test trusted local document parsing, ingestion, and operator CLI behavior."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mcp_portal.wiki.cli as wiki_cli
from mcp_portal.config import WikiSettings
from mcp_portal.security import InvocationContext, InvocationIdentity
from mcp_portal.tenancy import TenantScope
from mcp_portal.testing import create_test_settings
from mcp_portal.wiki.ingestion import (
    WikiIngestionOptions,
    chunk_markdown,
    ingest_file,
    parse_document,
)
from mcp_portal.wiki.models import WikiAccess
from mcp_portal.wiki.repository import InMemoryWikiRepository
from mcp_portal.wiki.service import WikiService

NOW = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)


def tenant_partition(tenant_id: str | None = None) -> str:
    """Create the same stable tenant partition used by trusted ingestion."""
    identity = InvocationIdentity(tenant_id=tenant_id)
    invocation = InvocationContext("request", "wiki_search", identity, 30.0)
    return TenantScope.from_invocation(invocation).partition


def access(*scopes: str, tenant_id: str | None = None) -> WikiAccess:
    """Create repository access matching a trusted ingestion tenant."""
    return WikiAccess(
        tenant_partition=tenant_partition(tenant_id),
        subject="reader",
        client_id=None,
        scopes=frozenset(scopes),
    )


def options(path: Path, **overrides) -> WikiIngestionOptions:
    """Create deterministic ingestion options for one test file."""
    values = {
        "path": path,
        "tenant_partition": tenant_partition(),
        "source_id": "runbook-source",
        "source_uri": "https://docs.example/runbook",
        "tags": ("operations",),
        "required_scopes": frozenset({"operations.read"}),
        "chunk_characters": 500,
        "chunk_overlap": 50,
    }
    values.update(overrides)
    return WikiIngestionOptions(**values)


def test_markdown_ingestion_publishes_cited_searchable_content(tmp_path: Path) -> None:
    """Verify a trusted Markdown file becomes a page and cited passages."""
    document = tmp_path / "Production Runbook.md"
    document.write_text(
        "# Production Runbook\n\n## Rollback\n\nSelect the last approved deployment.\n",
        encoding="utf-8",
    )
    storage = InMemoryWikiRepository()

    result = ingest_file(storage, options(document), ingested_at=NOW)
    service = WikiService(storage, access("operations.read"))
    search = service.search("approved deployment", tags=("operations",))

    assert result.page_slug == "production-runbook"
    assert result.document_format == "markdown"
    assert result.passage_count == 1
    assert result.source_uri == "https://docs.example/runbook"
    assert service.get_page("production-runbook").updated_at == NOW
    assert search.hits[0].citation.heading == "Rollback"
    assert search.hits[0].citation.source_revision == result.source_revision


def test_reingestion_atomically_replaces_old_source_passages(tmp_path: Path) -> None:
    """Verify a newer source revision removes obsolete retrieval passages."""
    document = tmp_path / "runbook.txt"
    document.write_text("Use the legacy blue deployment procedure.", encoding="utf-8")
    storage = InMemoryWikiRepository()
    selected = options(document, required_scopes=frozenset())
    ingest_file(storage, selected, ingested_at=NOW)

    document.write_text("Use the current green deployment procedure.", encoding="utf-8")
    second = ingest_file(storage, selected, ingested_at=NOW)
    service = WikiService(storage, access())

    assert service.search("legacy blue").hits == []
    assert service.search("current green").hits[0].page_slug == "runbook"
    assert service.get_page("runbook").revision_id == second.page_revision_id
    assert len(storage._sources) == 1


def test_dry_run_validates_without_mutating_repository(tmp_path: Path) -> None:
    """Verify dry-run output contains no local path and commits no records."""
    document = tmp_path / "private-path.txt"
    document.write_text("A private local document.", encoding="utf-8")
    storage = InMemoryWikiRepository()

    result = ingest_file(
        storage,
        options(
            document,
            source_id=None,
            source_uri=None,
            required_scopes=frozenset(),
            dry_run=True,
        ),
        ingested_at=NOW,
    )

    assert result.dry_run is True
    assert result.source_id.startswith("local:")
    assert result.source_uri.startswith("urn:mcp-portal:wiki:source:")
    assert str(tmp_path) not in result.model_dump_json()
    assert WikiService(storage, access()).list_pages().pages == []


def test_html_parser_removes_active_content_and_preserves_title(tmp_path: Path) -> None:
    """Verify HTML ingestion extracts readable Markdown without active elements."""
    document = tmp_path / "guide.html"
    document.write_text(
        "<html><head><title>Deployment Guide</title></head><body><main>"
        "<h1>Guide</h1><script>ignore()</script><p>Deploy safely.</p>"
        "</main></body></html>",
        encoding="utf-8",
    )

    parsed = parse_document(document)

    assert parsed.title == "Deployment Guide"
    assert parsed.document_format == "html"
    assert "# Guide" in parsed.markdown
    assert "Deploy safely" in parsed.markdown
    assert "ignore" not in parsed.markdown


def test_chunker_is_heading_aware_bounded_and_deterministic() -> None:
    """Verify long sections split reproducibly while retaining nearest headings."""
    markdown = "# Guide\n\n## Recovery\n\n" + ("recovery step " * 100)

    first = chunk_markdown(markdown, chunk_characters=500, overlap=50)
    second = chunk_markdown(markdown, chunk_characters=500, overlap=50)

    assert first == second
    assert len(first) > 1
    assert all(chunk.heading == "Recovery" for chunk in first)
    assert all(len(chunk.text) <= 500 for chunk in first)


def test_parser_rejects_unsupported_empty_and_oversized_files(tmp_path: Path) -> None:
    """Verify local parser limits fail closed before persistent mutation."""
    unsupported = tmp_path / "data.csv"
    unsupported.write_text("a,b", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    large = tmp_path / "large.txt"
    large.write_text("too large", encoding="utf-8")

    with pytest.raises(Exception, match="format"):
        parse_document(unsupported)
    with pytest.raises(Exception, match="size"):
        parse_document(empty)
    with pytest.raises(Exception, match="size"):
        parse_document(large, max_bytes=2)


def test_docx_parser_extracts_headings_paragraphs_and_tables(tmp_path: Path) -> None:
    """Verify the Word adapter produces useful source-backed Markdown."""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.core_properties.title = "Word Runbook"
    document.add_heading("Recovery", level=1)
    document.add_paragraph("Restart the service safely.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Step"
    table.cell(0, 1).text = "Owner"
    table.cell(1, 0).text = "Restart"
    table.cell(1, 1).text = "SRE"
    path = tmp_path / "runbook.docx"
    document.save(path)

    parsed = parse_document(path)

    assert parsed.title == "Word Runbook"
    assert "# Recovery" in parsed.markdown
    assert "Restart the service safely" in parsed.markdown
    assert "| Step | Owner |" in parsed.markdown


def test_pdf_parser_records_page_boundaries_and_metadata(tmp_path: Path) -> None:
    """Verify the PDF adapter records pages even when a page has no extractable text."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/Title": "PDF Runbook"})
    path = tmp_path / "runbook.pdf"
    with path.open("wb") as output:
        writer.write(output)

    parsed = parse_document(path)

    assert parsed.title == "PDF Runbook"
    assert parsed.document_format == "pdf"
    assert parsed.markdown == "## Page 1"


def test_cli_dry_run_uses_explicit_tenant_and_emits_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify the operator CLI derives tenant state and returns machine-readable output."""
    document = tmp_path / "runbook.md"
    document.write_text("# Runbook\n\nDeploy safely.", encoding="utf-8")
    storage = InMemoryWikiRepository()
    settings = create_test_settings()
    monkeypatch.setattr(wiki_cli.Settings, "from_env", lambda *args, **kwargs: settings)

    @contextmanager
    def fake_session(_settings):
        """Fail if dry-run processing attempts to open PostgreSQL."""
        raise AssertionError("dry run opened the repository")
        yield storage  # pragma: no cover - required for a contextmanager test seam

    monkeypatch.setattr(wiki_cli, "_repository_session", fake_session)

    wiki_cli.main(
        [
            "ingest",
            str(document),
            "--tenant-id",
            "tenant-a",
            "--dry-run",
            "--tag",
            "operations",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["page_slug"] == "runbook"
    assert payload["dry_run"] is True
    assert str(document) not in json.dumps(payload)
    assert storage._sources == {}


def test_cli_publish_commits_to_the_selected_tenant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify explicit CLI publication commits content to the selected tenant only."""
    document = tmp_path / "runbook.md"
    document.write_text("# Runbook\n\nDeploy safely.", encoding="utf-8")
    storage = InMemoryWikiRepository()
    settings = replace(
        create_test_settings(),
        wiki=WikiSettings(sqlalchemy_url="postgresql+psycopg://wiki.invalid/wiki"),
    )
    monkeypatch.setattr(wiki_cli.Settings, "from_env", lambda *args, **kwargs: settings)

    @contextmanager
    def fake_session(_settings):
        """Yield the deterministic repository instead of opening PostgreSQL."""
        yield storage

    monkeypatch.setattr(wiki_cli, "_repository_session", fake_session)

    wiki_cli.main(
        [
            "ingest",
            str(document),
            "--tenant-id",
            "tenant-a",
            "--publish",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is False
    assert WikiService(storage, access(tenant_id="tenant-a")).get_page("runbook").title == (
        "Runbook"
    )
    assert WikiService(storage, access(tenant_id="tenant-b")).list_pages().pages == []


def test_cli_returns_structured_error_for_invalid_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify predictable parser failures produce redacted JSON and a failure exit code."""
    document = tmp_path / "private.csv"
    document.write_text("secret,data", encoding="utf-8")
    monkeypatch.setattr(
        wiki_cli.Settings,
        "from_env",
        lambda *args, **kwargs: create_test_settings(),
    )

    with pytest.raises(SystemExit) as captured:
        wiki_cli.main(["ingest", str(document), "--single-tenant", "--dry-run"])

    payload = json.loads(capsys.readouterr().err)
    assert captured.value.code == 1
    assert payload["code"] == "validation_error"
    assert payload["details"]["suffix"] == ".csv"
    assert str(tmp_path) not in json.dumps(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"chunk_characters": 499},
        {"chunk_characters": 8_001},
        {"chunk_overlap": -1},
        {"chunk_overlap": 900},
        {"max_bytes": 0},
        {"max_bytes": (100 * 1024 * 1024) + 1},
    ],
)
def test_ingestion_options_reject_unsafe_limits(tmp_path: Path, overrides) -> None:
    """Verify parser and chunking limits remain within bounded operator ranges."""
    with pytest.raises(ValueError):
        options(tmp_path / "document.txt", **overrides)


def test_cli_requires_explicit_publish_or_dry_run(tmp_path: Path) -> None:
    """Verify the trusted command cannot publish through an omitted confirmation flag."""
    document = tmp_path / "runbook.md"
    document.write_text("# Runbook\n\nDeploy safely.", encoding="utf-8")

    with pytest.raises(SystemExit) as captured:
        wiki_cli.main(["ingest", str(document), "--single-tenant"])

    assert captured.value.code == 2
