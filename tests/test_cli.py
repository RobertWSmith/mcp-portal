from __future__ import annotations

import runpy
from pathlib import Path

import pytest

import mcp_portal.server as server_module


class FakeMcp:
    """Small test double for the module-level FastMCP server.

    Attributes:
        ran: Whether the fake server has been run.
    """

    def __init__(self) -> None:
        """Initialize the fake server state."""
        self.ran = False
        self.run_kwargs = {}

    def run(self, **kwargs) -> None:
        """Record that the server would have started."""
        self.ran = True
        self.run_kwargs = kwargs


def test_server_main_runs_module_server(monkeypatch) -> None:
    """Verify `server.main` delegates to the module-level FastMCP server."""
    fake_mcp = FakeMcp()

    monkeypatch.setattr(server_module, "mcp", fake_mcp)

    server_module.main([])

    assert fake_mcp.ran is True
    assert fake_mcp.run_kwargs == {"transport": "stdio", "show_banner": None}


def test_server_main_passes_custom_transport_options(monkeypatch) -> None:
    """Verify CLI options are passed to FastMCP's run method."""
    fake_mcp = FakeMcp()

    monkeypatch.setattr(server_module, "mcp", fake_mcp)

    server_module.main(
        [
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
            "--path",
            "/mcp",
            "--log-level",
            "debug",
            "--no-banner",
            "--json-response",
            "--stateless",
        ]
    )

    assert fake_mcp.ran is True
    assert fake_mcp.run_kwargs == {
        "transport": "http",
        "show_banner": False,
        "log_level": "debug",
        "stateless": True,
        "host": "127.0.0.1",
        "port": 9001,
        "path": "/mcp",
        "json_response": True,
    }


def test_server_main_rebuilds_server_for_env_file_and_debug_option(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify options that affect server construction create a tailored server."""
    fake_mcp = FakeMcp()
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_LARGE_LANGUAGE_MODEL=cli-large\n", encoding="utf-8")
    captured = {}

    for name in (
        "OPENAI_API_KEY",
        "OPENAI_LARGE_LANGUAGE_MODEL",
        "OPENAI_SMALL_LANGUAGE_MODEL",
        "OPENAI_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_LARGE_LANGUAGE_MODEL", "from-env")

    def fake_create_mcp(
        settings=None,
        namespaces=None,
        include_debug_ui=True,
    ):
        """Record server construction options and return a fake server."""
        captured["settings"] = settings
        captured["namespaces"] = namespaces
        captured["include_debug_ui"] = include_debug_ui
        return fake_mcp

    monkeypatch.setattr(server_module, "create_mcp", fake_create_mcp)

    server_module.main(["--env-file", str(env_file), "--no-debug-ui"])

    assert fake_mcp.ran is True
    assert captured["settings"].openai_large_language_model == "cli-large"
    assert captured["namespaces"] is None
    assert captured["include_debug_ui"] is False


def test_stdio_rejects_http_only_options() -> None:
    """Verify HTTP-only options are rejected for the stdio transport."""
    with pytest.raises(SystemExit) as exc_info:
        server_module.main(["--transport", "stdio", "--port", "9001"])

    assert exc_info.value.code == 2


def test_sse_rejects_stateless_mode() -> None:
    """Verify SSE rejects stateless mode before FastMCP raises at runtime."""
    with pytest.raises(SystemExit) as exc_info:
        server_module.main(["--transport", "sse", "--stateless"])

    assert exc_info.value.code == 2


def test_module_entrypoint_invokes_main(monkeypatch) -> None:
    """Verify `python -m mcp_portal` invokes the server main function."""
    called = False

    def fake_main() -> None:
        """Record that the package entrypoint invoked server main."""
        nonlocal called
        called = True

    monkeypatch.setattr(server_module, "main", fake_main)

    runpy.run_module("mcp_portal.__main__", run_name="__main__")

    assert called is True
