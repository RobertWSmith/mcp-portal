# Persistent LLM Wiki Implementation Plan

## Outcome

The wiki is a provenance-first knowledge system, not an unconstrained answer generator. Approved
sources become immutable revisions and cited passages. PostgreSQL persists governance metadata,
page history, and full-text indexes; pgvector stores embeddings beside the source records. The MCP
surface exposes retrieval and published context first. LLM drafting and publishing arrive only
after durable ingestion, authorization, and review paths exist.

The `wiki` namespace is mounted only when `MCP_PORTAL_WIKI_DATABASE_URL` is configured. The URL is
independent of the portal's Oracle or generic application database so enabling pgvector never
changes another namespace's database contract.

## Invariants

1. Tenant identifiers are derived only from verified invocation identity.
2. Tenant and document-scope predicates execute in PostgreSQL before content is returned or ranked.
3. A page revision key `(tenant_partition, slug, revision_id)` is immutable.
4. A published page is an explicit pointer to one immutable revision.
5. Every generated factual claim must cite a stored `citation_id` and `source_revision`.
6. Source content is untrusted data. Instructions found inside a source are never control input.
7. Missing and unauthorized pages produce the same public not-found result.
8. Publishing and archiving are separate destructive/write tools protected by approval receipts.
9. Embedding dimensions are a schema contract; a mismatch fails instead of truncating vectors.
10. LLM model, prompt, embedding, and source revisions are retained in future generation records.

## Current Phase: Durable Read-Only Retrieval

Implemented components:

- `WikiSettings` for a dedicated PostgreSQL URL, schema, dimensions, candidate count, and optional
  development schema initialization.
- `PgVectorWikiRepository` with page pointers, immutable revisions, passages, JSONB ACLs,
  PostgreSQL full-text retrieval, pgvector cosine retrieval, and weighted reciprocal-rank fusion.
- `InMemoryWikiRepository` as a test double only.
- `wiki_search`, `wiki_get_page`, and `wiki_list_pages` with structured Pydantic results.
- `portal://wiki/pages/{slug}` and `portal://wiki/pages/{slug}/provenance` resource templates.
- `wiki_research` prompt for evidence-first, citation-preserving answers.
- Request identity propagation for tenant-aware resource reads without granting resource handlers an
  execution cell.
- Lifecycle-managed `wiki_database` and `wiki_repository` clients with readiness checks.

Remaining work in this phase:

- Run the PostgreSQL migration in a test container and add an opt-in integration test.
- Add a command-line ingestion utility that accepts a trusted tenant and source manifest outside the
  model-controlled MCP tool surface.
- Add embedding client adapters for direct OpenAI and Azure OpenAI through the client registry.
- Add checksum-based passage upserts and deletion of passages removed by a newer source revision.
- Add backup/restore and retention tests for page revisions.

## Phase 2: Source Ingestion and Freshness

Add connector-neutral interfaces:

- `WikiSourceConnector.discover()` returns authorized source identifiers and revision metadata.
- `WikiSourceConnector.fetch()` returns bytes plus media type; it never returns credentials.
- `WikiParser.parse()` normalizes Markdown, HTML, PDF, Office, and repository content.
- `WikiChunker.chunk()` emits stable heading-aware passage IDs and character offsets.
- `WikiEmbeddingProvider.embed_documents()` returns exactly the configured dimensions.

Ingestion runs through `context.downstream(...)` or a non-MCP worker with the same credential broker,
egress policy, audit sink, and tenant partition rules. Each run records source URI, external revision,
content hash, parser/chunker versions, embedding model, dimensions, timestamps, classifications, and
required scopes.

When a source revision changes:

1. Fetch and hash the source.
2. Stop if the content hash is unchanged.
3. Parse and chunk into deterministic passage IDs.
4. Embed changed chunks only.
5. Commit passages and the source revision in one transaction.
6. Mark dependent wiki pages stale.
7. Queue optional refresh drafts; never republish automatically.

## Phase 3: Cited LLM Drafting

Add deployment-injected `wiki_language_model` and `wiki_embeddings` clients. Generation is a durable
job rather than part of ordinary search.

Planned tools:

- `wiki_draft_page(topic, source_ids, expected_slug)`
- `wiki_refresh_page(slug, expected_revision)`
- `wiki_compare_revisions(slug, left_revision, right_revision)`
- `wiki_list_stale_pages()`

The generator receives only authorized retrieved passages. Its structured output separates Markdown,
claim-to-citation mappings, unresolved conflicts, and unsupported statements. A validator rejects
unknown citations and verifies that every citation still points to the recorded source hash.

Long-running ingestion, embedding, and drafting should use the MCP Tasks extension when the caller
advertises support. The portal's task adapter must be durable before this is enabled in multi-instance
deployments.

## Phase 4: Reviewed Publishing

Planned tools and controls:

- `wiki_publish_page(slug, revision_id, expected_published_revision)` carries `write` and
  `destructive` policy tags and requires a single-use approval receipt.
- `wiki_archive_page(slug, expected_published_revision)` requires approval.
- `wiki_restore_page(slug, revision_id, expected_published_revision)` creates a new revision rather
  than mutating history.

Publishing uses optimistic concurrency. The approval receipt is bound to actor, tenant, exact tool,
slug, revision, expected pointer, content hash, and expiry. An append-only audit record captures the
reviewer and resulting pointer without logging page content.

## PostgreSQL and pgvector Operations

Install `.[wiki]`, provision PostgreSQL with pgvector, and run
`docs/sql/wiki-pgvector.sql` as a migration owner. Give the portal runtime role only `USAGE` on the
schema and the required `SELECT`, `INSERT`, and `UPDATE` permissions. Do not grant runtime extension or
schema creation privileges.

The default `halfvec(3072)` contract supports the configured `text-embedding-3-large` dimensions and
an HNSW cosine index. If dimensions change, create a new embedding column/table or a versioned schema,
backfill it, validate retrieval, and switch readers; do not alter populated dimensions in place.

Monitor query latency, HNSW recall, dead tuples, index size, ingestion lag, stale-page count, embedding
cost, and citation validation failures. PostgreSQL backups must include both tables and the installed
extension version. Restore exercises should verify published pointers and page/source revision hashes.

## Testing Strategy

- Domain tests: model validation, immutable revisions, freshness, citation serialization.
- Repository contract tests: the same suite against in-memory and PostgreSQL adapters.
- Security tests: tenant isolation, scope-subset ACLs, uniform not-found behavior, resource identity.
- Retrieval tests: lexical-only, vector-only, hybrid rank fusion, filters, dimension mismatches.
- MCP contract tests: schemas, annotations, required scopes, resources, and prompts.
- Migration tests: blank database, repeated migration, upgrade, backup/restore, pgvector extension
  absence, and least-privilege runtime role.
- Failure tests: unavailable database, invalid embeddings, stale approvals, concurrent publishing,
  malformed source content, and prompt injection inside retrieved text.
