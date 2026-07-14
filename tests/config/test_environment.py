"""Test low-level environment parsing and dotenv path resolution."""

from pathlib import Path

from mcp_portal.config.environment import _bool_env, _optional_env, _resolve_env_file


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


def test_bool_env_parses_boolean_values(monkeypatch) -> None:
    """Verify boolean environment values are normalized."""
    monkeypatch.setenv("BOOLEAN_VALUE", "off")

    assert _bool_env("BOOLEAN_VALUE", default=True) is False


def test_bool_env_uses_default_for_invalid_values(monkeypatch) -> None:
    """Verify invalid boolean environment values fall back to the default."""
    monkeypatch.setenv("BOOLEAN_VALUE", "sometimes")

    assert _bool_env("BOOLEAN_VALUE", default=True) is True
