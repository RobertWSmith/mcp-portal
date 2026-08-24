"""Persistent, provenance-first wiki domain services."""

# ruff: noqa: F401 - expose the public wiki model surface

from mcp_portal.wiki.models import (
    WikiAccess,
    WikiCitation,
    WikiIngestionResult,
    WikiPage,
    WikiPageListResult,
    WikiPageRecord,
    WikiPageStatus,
    WikiPageSummary,
    WikiPassageRecord,
    WikiProvenance,
    WikiSearchHit,
    WikiSearchResult,
    WikiSourceRecord,
)

__all__ = [name for name in globals() if not name.startswith("__")]
