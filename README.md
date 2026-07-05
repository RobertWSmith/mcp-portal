# MCP Portal

A compact FastMCP Python scaffold for building one MCP server out of small, focused
namespaces. Each namespace owns its tools and is mounted into the main server with a
stable prefix, so adding functionality does not turn the main server into a grab bag.

## Setup

```powershell
py -3.14 -m venv .venv
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Use a 64-bit Python 3.11+ environment. Some FastMCP transitive dependencies ship native
wheels, and 32-bit Python on Windows may force source builds.

The scaffold reads the existing environment variables from `.env.example`:

- `OPENAI_API_KEY`
- `OPENAI_LARGE_LANGUAGE_MODEL`
- `OPENAI_SMALL_LANGUAGE_MODEL`
- `OPENAI_EMBEDDING_MODEL`
- `MCP_PORTAL_HEALTH_ENABLED`

The health namespace exposes only non-secret configuration metadata. It never returns
the raw API key.

## Run

```powershell
.\.venv\Scripts\python.exe -m mcp_portal
```

This starts the FastMCP server over the default stdio transport, which is the usual
transport for local MCP clients.

After installing the package, the console script exposes the same launcher:

```powershell
mcp-portal
```

Pick another transport and connection options with flags:

```powershell
mcp-portal --transport http --host 127.0.0.1 --port 8000 --path /mcp
```

Useful launch options include:

- `--transport stdio|http|sse|streamable-http`
- `--host`, `--port`, and `--path` for HTTP-based transports
- `--log-level debug|info|warning|error|critical`
- `--show-banner` or `--no-banner`
- `--stateless` or `--stateful`
- `--json-response` or `--no-json-response` for HTTP-based transports
- `--env-file path\to\.env`
- `--debug-ui` or `--no-debug-ui`

Run `mcp-portal --help` for the complete command-line reference.

## Debug UI

```powershell
.\.venv\Scripts\Activate.ps1
fastmcp dev apps src/mcp_portal/server.py
```

This starts the MCP server over HTTP on port 8000, starts the FastMCP Apps dev UI
on port 8080, and opens the browser to the interactive `portal_debug` dashboard.
Use `--mcp-port` or `--dev-port` if either port is already taken. Keep the virtual
environment activated so FastMCP's reload worker can find the `fastmcp` command.

The dashboard is composed centrally. Namespaces contribute status checks and debug
panels through their manifest, while the portal owns redaction and presentation.
Disabled namespaces still appear in the debug snapshot, but their tools are not mounted.

## Test

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Git Bash:

```bash
source .venv/Scripts/activate
pytest
```

Or without activating:

```bash
./.venv/Scripts/python.exe -m pytest
```

Pytest enforces at least 90% coverage for `mcp_portal`.
On Windows, run only one coverage-enabled test process at a time. Pytest writes a
`.coverage` database, and concurrent PyCharm/terminal test runs can lock that file and
raise a permission error.

## Format

```powershell
.\.venv\Scripts\python.exe -m black src tests
```

## Add A Namespace

Create a module under `src/mcp_portal/namespaces/` with a `create_server(context)`
factory and decorate it with the namespace manifest:

```python
from fastmcp import FastMCP

from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceDebugPanel,
    NamespaceStatus,
    register_namespace,
)


def example_status(context: NamespaceContext) -> NamespaceStatus:
    """Report example namespace status."""
    return NamespaceStatus(
        state="ok",
        message="Example namespace is ready.",
        details={"namespace": context.name},
    )


def example_debug_panel(context: NamespaceContext) -> NamespaceDebugPanel:
    """Build example namespace debug details."""
    return NamespaceDebugPanel(
        title="Example Namespace",
        summary="Example tools and runtime metadata.",
        snapshot={"large_model": context.settings.openai.large_language_model},
    )


@register_namespace(
    "example",
    description="Example tools for local development.",
    tags={"example", "readonly"},
    health_check=example_status,
    debug=example_debug_panel,
)
def create_server(context: NamespaceContext) -> FastMCP:
    """Create the example namespace server.

    Args:
        context: Runtime services shared with the namespace.

    Returns:
        A configured FastMCP child server.
    """
    server = FastMCP("Example")

    @server.tool(tags={"example", "readonly"})
    def hello(name: str) -> str:
        """Greet a user by name.

        Args:
            name: Name to greet.

        Returns:
            A friendly greeting.
        """
        context.logger.debug("Example greeting requested")
        return f"Hello, {name}!"

    return server
```

FastMCP mounts the namespace with a prefix, so `hello` becomes `example_hello`.
Namespace modules are discovered automatically; adding the decorated module is enough.

Each namespace receives a `NamespaceContext` with shared settings, a namespace-scoped
logger, a redactor for safe diagnostics, a clock, and lazy external client factories.
Use `mcp_portal.testing.create_namespace_test_client` or
`mcp_portal.testing.create_namespace_test_context` for focused namespace tests.
