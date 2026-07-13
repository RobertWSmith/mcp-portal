"""Test lifecycle-managed client registry behavior."""

from mcp_portal.clients import ClientFactories

async def test_lifecycle_managed_clients_are_reused_and_closed() -> None:
    """Verify shared client factories reuse and close clients."""

    class ClosableClient:
        """Small closeable client for lifecycle tests."""

        def __init__(self) -> None:
            """Initialize close state."""
            self.closed = False

        async def aclose(self) -> None:
            """Close the test client asynchronously."""
            self.closed = True

    registry = ClientFactories().with_factory("example", ClosableClient, shared=True)
    first = registry.create("example")
    second = registry.create("example")

    await registry.aclose()

    assert first is second
    assert first.closed is True

    class DisposableEngine:
        """Small SQLAlchemy-like engine for disposal tests."""

        def __init__(self) -> None:
            """Initialize disposal state."""
            self.disposed = False

        def dispose(self) -> None:
            """Dispose the test engine."""
            self.disposed = True

    engine_registry = ClientFactories().with_factory("database", DisposableEngine, shared=True)
    engine = engine_registry.create("database")

    await engine_registry.aclose()

    assert engine.disposed is True

