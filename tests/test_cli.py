from __future__ import annotations

import runpy

import mcp_portal.server as server_module


class FakeMcp:
    """Small test double for the module-level FastMCP server.

    Attributes:
        ran: Whether the fake server has been run.
    """

    def __init__(self) -> None:
        """Initialize the fake server state."""
        self.ran = False

    def run(self) -> None:
        """Record that the server would have started."""
        self.ran = True


def test_server_main_runs_module_server(monkeypatch) -> None:
    """Verify `server.main` delegates to the module-level FastMCP server."""
    fake_mcp = FakeMcp()

    monkeypatch.setattr(server_module, "mcp", fake_mcp)

    server_module.main()

    assert fake_mcp.ran is True


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
