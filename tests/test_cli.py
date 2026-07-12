from __future__ import annotations

import os
import runpy
import subprocess
import sys
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


def test_server_main_rebuilds_server_for_env_file(monkeypatch, tmp_path: Path) -> None:
    """Verify an environment file creates a server with tailored settings."""
    fake_mcp = FakeMcp()
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_LARGE_LANGUAGE_MODEL=cli-large\n", encoding="utf-8")
    captured = {}

    for name in (
        "MCP_PORTAL_MODEL_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_LARGE_LANGUAGE_MODEL",
        "OPENAI_SMALL_LANGUAGE_MODEL",
        "OPENAI_EMBEDDING_MODEL",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_TOKEN_SCOPE",
        "AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT",
        "AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_LARGE_LANGUAGE_MODEL", "from-env")

    def fake_create_mcp(
        settings=None,
        namespaces=None,
    ):
        """Record server construction options and return a fake server."""
        captured["settings"] = settings
        captured["namespaces"] = namespaces
        return fake_mcp

    monkeypatch.setattr(server_module, "create_mcp", fake_create_mcp)

    server_module.main(["--env-file", str(env_file)])

    assert fake_mcp.ran is True
    assert captured["settings"].openai_large_language_model == "cli-large"
    assert captured["namespaces"] is None


def test_server_main_uses_production_profile(monkeypatch) -> None:
    """Verify the production CLI flag uses the production server factory."""
    fake_mcp = FakeMcp()
    captured = {}

    def fake_create_production_mcp(settings=None):
        """Record production server construction options and return a fake server."""
        captured["settings"] = settings
        return fake_mcp

    monkeypatch.setattr(server_module, "create_production_mcp", fake_create_production_mcp)

    server_module.main(["--production"])

    assert fake_mcp.ran is True
    assert captured["settings"] is not None


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


def test_portal_fastmcp_run_maps_legacy_transport_options(monkeypatch) -> None:
    """Verify the SDK adapter maps legacy CLI run options onto SDK settings."""
    calls = []
    server = server_module.PortalFastMCP("Test")

    def fake_run(self, transport="stdio", mount_path=None) -> None:
        """Record the SDK transport that would have run."""
        calls.append((self, transport, mount_path))

    monkeypatch.setattr(server_module.FastMCP, "run", fake_run)

    server.run(
        transport="http",
        show_banner=False,
        host="127.0.0.1",
        port=9001,
        path="/custom",
        log_level="debug",
        json_response=True,
        stateless=True,
    )
    server.run(transport="sse", path="/events")

    assert [(transport, mount_path) for _, transport, mount_path in calls] == [
        ("streamable-http", None),
        ("sse", "/events"),
    ]
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 9001
    assert server.settings.streamable_http_path == "/events"
    assert server.settings.log_level == "DEBUG"
    assert server.settings.json_response is True
    assert server.settings.stateless_http is True


def test_portal_fastmcp_http_app_applies_legacy_options(monkeypatch) -> None:
    """Verify the ASGI compatibility wrapper applies HTTP options."""
    server = server_module.PortalFastMCP("Test")

    def fake_streamable_http_app(self):
        """Return a placeholder ASGI app."""
        return {"app": self.settings.streamable_http_path}

    monkeypatch.setattr(
        server_module.FastMCP,
        "streamable_http_app",
        fake_streamable_http_app,
    )

    app = server.http_app(path="/mcp", json_response=True, stateless_http=True)

    assert app == {"app": "/mcp"}
    assert server.settings.json_response is True
    assert server.settings.stateless_http is True


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


def test_server_file_imports_without_src_on_pythonpath() -> None:
    """Verify `mcp dev src/mcp_portal/server.py` can import the package."""
    repo_root = Path(__file__).resolve().parents[1]
    import_script = f"""
import importlib.util
import sys
from pathlib import Path

repo_root = Path({str(repo_root)!r})
src_path = repo_root / "src"
sys.path = [
    path
    for path in sys.path
    if Path(path or ".").resolve() != src_path.resolve()
]

spec = importlib.util.spec_from_file_location(
    "portal_server_for_mcp_dev",
    src_path / "mcp_portal" / "server.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert callable(module.create_mcp)
assert any(Path(path).resolve() == src_path.resolve() for path in sys.path)
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    subprocess.run(
        [sys.executable, "-c", import_script],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_server_file_runs_as_script_without_src_on_pythonpath() -> None:
    """Verify `mcp dev src/mcp_portal/server.py` launches the server script."""
    repo_root = Path(__file__).resolve().parents[1]
    server_file = repo_root / "src" / "mcp_portal" / "server.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(server_file), "--help"],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Run the MCP Portal FastMCP server." in result.stdout
