"""Validate namespace metadata and committed MCP tool contracts in CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import anyio

from mcp_portal.config import OpenAISettings, Settings
from mcp_portal.contracts import compare_tool_contract_manifests, generate_tool_contract_manifest
from mcp_portal.namespaces import iter_namespaces, validate_namespaces
from mcp_portal.server import create_mcp

DEFAULT_BASELINE = Path("contracts/tool-contracts.json")


def governance_settings() -> Settings:
    """Return environment-independent settings for repository contract generation.

    Returns:
        Deterministic portal settings that do not load a developer environment file.
    """
    return Settings(
        openai=OpenAISettings(
            api_key=None,
            large_language_model="gpt-5.5",
            small_language_model="gpt-5.5-mini",
            embedding_model="text-embedding-3-large",
        )
    )


async def check_repository(baseline_path: Path = DEFAULT_BASELINE) -> tuple[str, ...]:
    """Return strict namespace and contract-governance violations.

    Args:
        baseline_path: Reviewed contract manifest to compare.

    Returns:
        Human-readable governance violations.
    """
    namespaces = iter_namespaces(strict=True)
    errors = list(validate_namespaces(namespaces))
    if not baseline_path.is_file():
        errors.append(f"contract baseline is missing: {baseline_path}")
        return tuple(errors)

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = await generate_tool_contract_manifest(
        create_mcp(governance_settings(), namespaces=namespaces)
    )
    changes = compare_tool_contract_manifests(baseline, current)
    for classification, names in changes.items():
        if names:
            errors.append(f"tool contracts {classification}: {', '.join(names)}")
    return tuple(errors)


async def write_baseline(baseline_path: Path = DEFAULT_BASELINE) -> None:
    """Write the current governed tool contracts as the reviewed baseline.

    Args:
        baseline_path: Destination for the generated contract manifest.
    """
    namespaces = iter_namespaces(strict=True)
    errors = validate_namespaces(namespaces)
    if errors:
        raise ValueError("\n".join(errors))
    manifest = await generate_tool_contract_manifest(
        create_mcp(governance_settings(), namespaces=namespaces)
    )
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Build the repository-governance command parser.

    Returns:
        Configured command-line parser.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "update-baseline"))
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run strict namespace validation or update the reviewed contract baseline.

    Args:
        argv: Optional command-line arguments.
    """
    args = build_parser().parse_args(argv)
    if args.command == "update-baseline":
        anyio.run(write_baseline, args.baseline)
        return
    errors = anyio.run(check_repository, args.baseline)
    if errors:
        raise SystemExit("Governance check failed:\n- " + "\n- ".join(errors))


if __name__ == "__main__":
    main()
