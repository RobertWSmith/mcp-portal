-- Persistent MCP Portal wiki schema for the default 3072-dimension embedding contract.
-- Run as a migration owner. The portal runtime role needs DML privileges, not CREATE EXTENSION.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS mcp_portal_wiki;

CREATE TABLE IF NOT EXISTS mcp_portal_wiki.page_revisions (
    tenant_partition varchar(64) NOT NULL,
    slug varchar(200) NOT NULL,
    revision_id varchar(160) NOT NULL,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    markdown text NOT NULL,
    status varchar(20) NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
    updated_at timestamptz NOT NULL,
    source_updated_at timestamptz,
    content_hash varchar(71) NOT NULL,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    stale boolean NOT NULL DEFAULT false,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    required_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (tenant_partition, slug, revision_id),
    CHECK (jsonb_typeof(tags) = 'array'),
    CHECK (jsonb_typeof(citations) = 'array'),
    CHECK (jsonb_typeof(required_scopes) = 'array')
);

CREATE TABLE IF NOT EXISTS mcp_portal_wiki.pages (
    tenant_partition varchar(64) NOT NULL,
    slug varchar(200) NOT NULL,
    published_revision_id varchar(160) NOT NULL,
    required_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_partition, slug),
    FOREIGN KEY (tenant_partition, slug, published_revision_id)
        REFERENCES mcp_portal_wiki.page_revisions
        (tenant_partition, slug, revision_id),
    CHECK (jsonb_typeof(required_scopes) = 'array')
);

CREATE TABLE IF NOT EXISTS mcp_portal_wiki.passages (
    tenant_partition varchar(64) NOT NULL,
    passage_id varchar(160) NOT NULL,
    page_slug varchar(200),
    text text NOT NULL,
    source_id varchar(512) NOT NULL,
    source_revision varchar(160) NOT NULL,
    title varchar(500) NOT NULL,
    heading varchar(500),
    source_uri varchar(2048) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    content_hash varchar(71) NOT NULL,
    citation_id varchar(160) NOT NULL,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    required_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
    embedding halfvec(3072),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english'::regconfig,
            coalesce(title, '') || ' ' || coalesce(heading, '') || ' ' || text
        )
    ) STORED,
    PRIMARY KEY (tenant_partition, passage_id),
    CHECK (jsonb_typeof(tags) = 'array'),
    CHECK (jsonb_typeof(required_scopes) = 'array')
);

CREATE INDEX IF NOT EXISTS ix_wiki_passages_search
    ON mcp_portal_wiki.passages USING gin (search_vector);

CREATE INDEX IF NOT EXISTS ix_wiki_passages_embedding_hnsw
    ON mcp_portal_wiki.passages
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_wiki_pages_updated
    ON mcp_portal_wiki.pages (tenant_partition, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_wiki_passages_source
    ON mcp_portal_wiki.passages (tenant_partition, source_id, source_revision);
