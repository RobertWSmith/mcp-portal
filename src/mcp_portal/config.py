from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class OpenAISettings:
    """OpenAI-related runtime settings.

    Attributes:
        api_key: Optional OpenAI API key used by namespaces that call OpenAI.
        large_language_model: Model name for larger language-model tasks.
        small_language_model: Model name for smaller language-model tasks.
        embedding_model: Model name for embedding tasks.
    """

    api_key: str | None
    large_language_model: str
    small_language_model: str
    embedding_model: str

    @property
    def has_api_key(self) -> bool:
        """Report whether a non-placeholder OpenAI API key is configured.

        Returns:
            True when `OPENAI_API_KEY` is set to a non-placeholder value.
        """
        return bool(self.api_key and self.api_key != "your-api-key")

    def public_snapshot(self) -> dict[str, str | bool]:
        """Return OpenAI settings safe to expose through development tools.

        Returns:
            Public model names and whether an API key is configured.
        """
        return {
            "has_api_key": self.has_api_key,
            "large_language_model": self.large_language_model,
            "small_language_model": self.small_language_model,
            "embedding_model": self.embedding_model,
        }


@dataclass(frozen=True)
class HealthSettings:
    """Health namespace runtime settings.

    Attributes:
        enabled: Whether the health namespace tools should be mounted.
    """

    enabled: bool = True

    def public_snapshot(self) -> dict[str, bool]:
        """Return health settings safe to expose through development tools.

        Returns:
            Public health namespace configuration.
        """
        return {"enabled": self.enabled}


@dataclass(frozen=True)
class Settings:
    """Runtime settings grouped by namespace or provider boundary.

    Attributes:
        openai: Settings used by OpenAI-backed namespaces.
        health: Settings used by the health namespace.
    """

    openai: OpenAISettings
    health: HealthSettings = field(default_factory=HealthSettings)

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
            openai=OpenAISettings(
                api_key=_optional_env("OPENAI_API_KEY"),
                large_language_model=os.getenv("OPENAI_LARGE_LANGUAGE_MODEL", "gpt-5.5"),
                small_language_model=os.getenv("OPENAI_SMALL_LANGUAGE_MODEL", "gpt-5.5-mini"),
                embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
            ),
            health=HealthSettings(
                enabled=_bool_env("MCP_PORTAL_HEALTH_ENABLED", default=True),
            ),
        )

    @property
    def openai_api_key(self) -> str | None:
        """Return the configured OpenAI API key.

        Returns:
            The optional OpenAI API key.
        """
        return self.openai.api_key

    @property
    def openai_large_language_model(self) -> str:
        """Return the configured large language model.

        Returns:
            The model name for larger language-model tasks.
        """
        return self.openai.large_language_model

    @property
    def openai_small_language_model(self) -> str:
        """Return the configured small language model.

        Returns:
            The model name for smaller language-model tasks.
        """
        return self.openai.small_language_model

    @property
    def openai_embedding_model(self) -> str:
        """Return the configured embedding model.

        Returns:
            The model name for embedding tasks.
        """
        return self.openai.embedding_model

    @property
    def has_openai_api_key(self) -> bool:
        """Report whether a non-placeholder OpenAI API key is configured.

        Returns:
            True when `OPENAI_API_KEY` is set to a non-placeholder value.
        """
        return self.openai.has_api_key

    def namespace_enabled(self, name: str) -> bool:
        """Report whether a namespace should mount its tools.

        Args:
            name: Namespace prefix.

        Returns:
            True when tools for the namespace should be mounted.
        """
        if name == "health":
            return self.health.enabled
        return True

    def public_snapshot(self) -> dict[str, dict[str, str | bool]]:
        """Return non-secret settings safe to expose through development tools.

        Returns:
            Grouped public runtime settings.
        """
        return {
            "openai": self.openai.public_snapshot(),
            "health": self.health.public_snapshot(),
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


def _bool_env(name: str, *, default: bool) -> bool:
    """Read an optional boolean environment variable.

    Args:
        name: Environment variable name to read.
        default: Value returned when the environment variable is unset or blank.

    Returns:
        Parsed boolean value.
    """
    value = _optional_env(name)
    if value is None:
        return default

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    return default
