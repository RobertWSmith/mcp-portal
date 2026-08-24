"""Run privileged operator commands for persistent wiki administration."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mcp_portal.clients import default_client_factories
from mcp_portal.config import Settings
from mcp_portal.errors import ConfigurationPortalError, PortalError
from mcp_portal.redaction import Redactor
from mcp_portal.security import InvocationContext, InvocationIdentity
from mcp_portal.tenancy import TenantScope
from mcp_portal.wiki.ingestion import (
    DEFAULT_MAX_BYTES,
    WikiIngestionOptions,
    ingest_file,
)
from mcp_portal.wiki.repository import WikiRepository


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the trusted wiki administration parser.

    Returns:
        Parser containing the local document ingestion command.
    """
    parser = argparse.ArgumentParser(
        prog="mcp-portal-wiki",
        description="Run trusted persistent wiki administration commands.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    ingest = subcommands.add_parser(
        "ingest",
        help="Parse and publish one trusted local document.",
    )
    ingest.add_argument("path", type=Path, help="Local Markdown, text, HTML, PDF, or DOCX file.")
    tenant = ingest.add_mutually_exclusive_group(required=True)
    tenant.add_argument(
        "--tenant-id",
        help="Verified external tenant ID selected by the trusted operator.",
    )
    tenant.add_argument(
        "--single-tenant",
        action="store_true",
        help="Use the portal's stable single-tenant partition.",
    )
    commit = ingest.add_mutually_exclusive_group(required=True)
    commit.add_argument(
        "--publish",
        action="store_true",
        help="Atomically publish the page and replace this source's passages.",
    )
    commit.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate without changing persistent state.",
    )
    ingest.add_argument("--slug", help="Explicit page slug; defaults to the filename stem.")
    ingest.add_argument("--title", help="Explicit page title; defaults to document metadata.")
    ingest.add_argument(
        "--source-id",
        help="Stable logical source ID; defaults to a non-reversible path-derived ID.",
    )
    ingest.add_argument(
        "--source-uri",
        help="Canonical citation URI; defaults to a generated URN that hides the local path.",
    )
    ingest.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Slug-like document tag; repeat for multiple tags.",
    )
    ingest.add_argument(
        "--required-scope",
        action="append",
        default=[],
        help="Additional scope required to retrieve the document; repeat as needed.",
    )
    ingest.add_argument(
        "--chunk-characters",
        type=int,
        default=1_800,
        help="Maximum passage characters. Defaults to 1800.",
    )
    ingest.add_argument(
        "--chunk-overlap",
        type=int,
        default=200,
        help="Characters repeated between adjacent passage windows. Defaults to 200.",
    )
    ingest.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help="Maximum accepted file size in bytes. Defaults to 25 MiB.",
    )
    ingest.add_argument(
        "--env-file",
        type=Path,
        help="Dotenv file containing the persistent wiki database URL.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one trusted wiki administration command.

    Args:
        argv: Optional arguments; when omitted, values are read from ``sys.argv``.
    """
    parser = build_arg_parser()
    options = parser.parse_args(argv)
    settings: Settings | None = None
    try:
        settings = Settings.from_env(
            options.env_file,
            override=options.env_file is not None,
        )
        ingestion_options = _ingestion_options(options)
        if options.dry_run:
            result = ingest_file(None, ingestion_options)
        else:
            if not settings.wiki.postgresql_configured:
                raise ConfigurationPortalError(
                    "Trusted wiki ingestion requires MCP_PORTAL_WIKI_DATABASE_URL."
                )
            with _repository_session(settings) as repository:
                result = ingest_file(repository, ingestion_options)
        print(result.model_dump_json(indent=2))
    except (PortalError, ValueError) as error:
        payload = _public_error(error, settings)
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from error


def _ingestion_options(options: argparse.Namespace) -> WikiIngestionOptions:
    """Convert parsed operator arguments into trusted ingestion options.

    Args:
        options: Parsed command-line namespace.

    Returns:
        Validated ingestion inputs with a non-reversible tenant partition.
    """
    return WikiIngestionOptions(
        path=options.path,
        tenant_partition=_tenant_partition(options),
        slug=options.slug,
        title=options.title,
        source_id=options.source_id,
        source_uri=options.source_uri,
        tags=tuple(options.tag),
        required_scopes=frozenset(options.required_scope),
        chunk_characters=options.chunk_characters,
        chunk_overlap=options.chunk_overlap,
        max_bytes=options.max_bytes,
        dry_run=options.dry_run,
    )


def _tenant_partition(options: argparse.Namespace) -> str:
    """Derive the storage partition from an explicit trusted operator selection.

    Args:
        options: Parsed arguments containing one tenant selection mode.

    Returns:
        Stable non-reversible tenant storage partition.
    """
    tenant_id = None if options.single_tenant else _normalize_tenant_id(options.tenant_id)
    identity = InvocationIdentity(
        subject="wiki-ingestion-operator",
        tenant_id=tenant_id,
        auth_method="trusted_operator_cli",
        principal_type="application",
    )
    invocation = InvocationContext(
        request_id="trusted-wiki-ingestion",
        tool_name="operator:wiki:ingest",
        identity=identity,
        deadline_seconds=300.0,
    )
    return TenantScope.from_invocation(
        invocation,
        require_tenant=not options.single_tenant,
    ).partition


def _normalize_tenant_id(value: str | None) -> str:
    """Validate the trusted operator's external tenant selection.

    Args:
        value: Tenant ID selected on the command line.

    Returns:
        Bounded tenant identifier used only to derive a partition hash.
    """
    selected = (value or "").strip()
    if not selected or len(selected) > 256:
        raise ValueError("Tenant ID must contain 1 to 256 characters")
    return selected


@contextmanager
def _repository_session(settings: Settings) -> Iterator[WikiRepository]:
    """Open and reliably close the configured persistent wiki repository.

    Args:
        settings: Runtime configuration with a dedicated PostgreSQL wiki URL.

    Yields:
        Lifecycle-managed persistent wiki repository.

    Returns:
        Context manager that disposes shared database clients on exit.
    """
    clients = default_client_factories(settings)
    try:
        yield clients.shared("wiki_repository", namespace="wiki")
    finally:
        asyncio.run(clients.aclose())


def _public_error(error: Exception, settings: Settings | None) -> dict[str, Any]:
    """Return a redacted stable CLI error payload.

    Args:
        error: Predictable portal or input validation failure.
        settings: Optional loaded settings containing secrets to redact.

    Returns:
        JSON-compatible public error metadata.
    """
    secrets = (
        settings.wiki.sqlalchemy_url if settings is not None else None,
        settings.openai.api_key if settings is not None else None,
        settings.azure_identity.client_secret if settings is not None else None,
    )
    redactor = Redactor.from_secrets(secrets)
    if isinstance(error, PortalError):
        return error.to_public_dict(redactor)
    return {
        "code": "validation_error",
        "category": "validation",
        "message": redactor.redact(str(error)),
        "namespace": "wiki",
        "details": {},
    }


if __name__ == "__main__":
    main()
