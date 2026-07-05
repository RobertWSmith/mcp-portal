from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Runtime settings sourced from the existing environment contract.

    Attributes:
        openai_api_key: Optional OpenAI API key used by namespaces that call OpenAI.
        openai_large_language_model: Model name for larger language-model tasks.
        openai_small_language_model: Model name for smaller language-model tasks.
        openai_embedding_model: Model name for embedding tasks.
    """

    openai_api_key: str | None
    openai_large_language_model: str
    openai_small_language_model: str
    openai_embedding_model: str

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, override: bool = False) -> "Settings":
        """Build settings from environment variables and an optional `.env` file.

        Args:
            env_file: Optional path to a dotenv file. When omitted, `.env` is resolved from
                the current working directory, then the project root.
            override: Whether dotenv values should override existing environment values.

        Returns:
            Settings populated from the existing environment-variable contract.
        """
        load_dotenv(_resolve_env_file(env_file), override=override)

        return cls(
            openai_api_key=_optional_env("OPENAI_API_KEY"),
            openai_large_language_model=os.getenv("OPENAI_LARGE_LANGUAGE_MODEL", "gpt-5.5"),
            openai_small_language_model=os.getenv("OPENAI_SMALL_LANGUAGE_MODEL", "gpt-5.5-mini"),
            openai_embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
        )

    @property
    def has_openai_api_key(self) -> bool:
        """Report whether a non-placeholder OpenAI API key is configured.

        Returns:
            True when `OPENAI_API_KEY` is set to a non-placeholder value.
        """
        return bool(self.openai_api_key and self.openai_api_key != "your-api-key")

    def public_snapshot(self) -> dict[str, str | bool]:
        """Return non-secret settings safe to expose through development tools.

        Returns:
            A dictionary containing model names and whether an API key is configured.
        """
        return {
            "has_openai_api_key": self.has_openai_api_key,
            "openai_large_language_model": self.openai_large_language_model,
            "openai_small_language_model": self.openai_small_language_model,
            "openai_embedding_model": self.openai_embedding_model,
        }


def _resolve_env_file(env_file: str | Path | None) -> Path:
    """Resolve the dotenv file path used for local development settings.

    Args:
        env_file: Optional path provided by the caller.

    Returns:
        The explicit dotenv path, current working directory `.env`, or project-root `.env`.
    """
    if env_file is not None:
        return Path(env_file)

    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env

    return PROJECT_ROOT / ".env"


def _optional_env(name: str) -> str | None:
    """Read an optional environment variable as a stripped non-empty string.

    Args:
        name: Environment variable name to read.

    Returns:
        The stripped value, or None when the variable is unset or blank.
    """
    value = os.getenv(name)
    if value is None:
        return None

    value = value.strip()
    return value or None
