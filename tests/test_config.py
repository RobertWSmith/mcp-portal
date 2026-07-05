from __future__ import annotations

from pathlib import Path

from mcp_portal.config import Settings, _optional_env, _resolve_env_file


def test_settings_from_explicit_env_file(tmp_path: Path, monkeypatch) -> None:
    """Verify settings load from an explicit dotenv file."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=from-file",
                "OPENAI_LARGE_LANGUAGE_MODEL=large-from-file",
                "OPENAI_SMALL_LANGUAGE_MODEL=small-from-file",
                "OPENAI_EMBEDDING_MODEL=embedding-from-file",
            ]
        ),
        encoding="utf-8",
    )
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_LARGE_LANGUAGE_MODEL",
        "OPENAI_SMALL_LANGUAGE_MODEL",
        "OPENAI_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file)

    assert settings.openai_api_key == "from-file"
    assert settings.public_snapshot() == {
        "has_openai_api_key": True,
        "openai_large_language_model": "large-from-file",
        "openai_small_language_model": "small-from-file",
        "openai_embedding_model": "embedding-from-file",
    }


def test_settings_defaults_and_placeholder_key(monkeypatch) -> None:
    """Verify defaults are used and placeholder keys are not treated as configured."""
    for name in (
        "OPENAI_LARGE_LANGUAGE_MODEL",
        "OPENAI_SMALL_LANGUAGE_MODEL",
        "OPENAI_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "your-api-key")

    settings = Settings.from_env(env_file=Path("does-not-exist.env"))

    assert settings.has_openai_api_key is False
    assert settings.public_snapshot() == {
        "has_openai_api_key": False,
        "openai_large_language_model": "gpt-5.5",
        "openai_small_language_model": "gpt-5.5-mini",
        "openai_embedding_model": "text-embedding-3-large",
    }


def test_settings_from_env_file_can_override_existing_values(tmp_path: Path, monkeypatch) -> None:
    """Verify explicit override mode lets dotenv values win."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_LARGE_LANGUAGE_MODEL=large-from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_LARGE_LANGUAGE_MODEL", "large-from-env")

    settings = Settings.from_env(env_file, override=True)

    assert settings.openai_large_language_model == "large-from-file"


def test_resolve_env_file_prefers_explicit_path(tmp_path: Path) -> None:
    """Verify explicit dotenv paths are returned unchanged."""
    env_file = tmp_path / "custom.env"

    assert _resolve_env_file(env_file) == env_file


def test_optional_env_strips_blank_values(monkeypatch) -> None:
    """Verify blank optional environment variables normalize to None."""
    monkeypatch.setenv("OPTIONAL_VALUE", "   ")

    assert _optional_env("OPTIONAL_VALUE") is None


def test_optional_env_returns_missing_values(monkeypatch) -> None:
    """Verify missing optional environment variables normalize to None."""
    monkeypatch.delenv("OPTIONAL_VALUE", raising=False)

    assert _optional_env("OPTIONAL_VALUE") is None
