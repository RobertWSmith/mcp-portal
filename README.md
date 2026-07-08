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

The scaffold reads the existing environment variables from `.env.example`. See
[docs/environment-variables.md](docs/environment-variables.md) for defaults,
accepted values, loading behavior, and production requirements.

- `MCP_PORTAL_MODEL_PROVIDER` selects `openai` or `azure_openai`
- `AZURE_OPENAI_*` for Azure OpenAI endpoint, API version, token scope, and deployments
- `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` for optional service-principal auth
- `OPENAI_API_KEY` and `OPENAI_*_MODEL` only when using the direct OpenAI provider
- `MCP_PORTAL_HEALTH_ENABLED`
- `MCP_PORTAL_AUTH_*` for HTTP authentication
- `MCP_PORTAL_AUTHZ_TAG_SCOPES` for tag policy metadata
- `MCP_PORTAL_HTTP_PATH` and `MCP_PORTAL_HEALTH_PATH`
- `MCP_PORTAL_DATABASE_PROVIDER`, `MCP_PORTAL_DATABASE_SQLALCHEMY_URL`, and `MCP_PORTAL_ORACLE_*`
- `MCP_PORTAL_LANGCHAIN_MONGODB_*` for LangChain MongoDB connectors
- `OTEL_SERVICE_NAME` and `OTEL_EXPORTER_OTLP_ENDPOINT`

The health namespace exposes only non-secret configuration metadata. It never returns
raw API keys or Azure client secrets.

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
- `--production` for the hardened production server profile

Run `mcp-portal --help` for the complete command-line reference.

## Production

Use the production profile when exposing the portal over HTTP. It disables the debug
tools, attaches lifecycle cleanup for shared clients, wires configured bearer-token
authentication, and adds an unauthenticated operational health route:

```powershell
mcp-portal --production --transport streamable-http --host 0.0.0.0 --port 8000 --path /mcp
```

For ASGI deployments, point Uvicorn, Gunicorn, or another ASGI server at the production
entrypoint:

```powershell
uvicorn mcp_portal.asgi:app --host 0.0.0.0 --port 8000
```

The app uses `MCP_PORTAL_HTTP_PATH` for the MCP endpoint and `MCP_PORTAL_HEALTH_PATH`
for the health probe. By default those are `/mcp` and `/healthz`.

Remote HTTP deployments should set `MCP_PORTAL_AUTH_PROVIDER=jwt` with either
`MCP_PORTAL_AUTH_JWT_JWKS_URI` or `MCP_PORTAL_AUTH_JWT_PUBLIC_KEY`. Static bearer
tokens are available through `MCP_PORTAL_AUTH_PROVIDER=static`, but they are intended
only for local smoke tests.

Tag metadata can be attached to SDK tools through `_meta`. Keep using `readonly`,
`write`, `admin`, `external`, and `destructive` tags on namespace tools so access
policy can stay centralized.

Relational database access goes through SQLAlchemy engines. Oracle is the preferred
SQLAlchemy backend for portal integrations, but namespaces should depend on SQLAlchemy
APIs so engines can be swapped for tests or future relational targets.

Install the portable database extra for generic SQLAlchemy URLs:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[database]"
```

Install the Oracle extra when using the preferred Oracle backend:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[oracle]"
```

Configure `MCP_PORTAL_DATABASE_PROVIDER=oracle` plus `MCP_PORTAL_ORACLE_DSN`,
`MCP_PORTAL_ORACLE_USER`, and `MCP_PORTAL_ORACLE_PASSWORD`. The portal builds an
`oracle+oracledb` SQLAlchemy engine from those values. For non-Oracle testing or future
portable backends, set `MCP_PORTAL_DATABASE_PROVIDER=sqlalchemy` and
`MCP_PORTAL_DATABASE_SQLALCHEMY_URL`.

Namespaces must request the shared lifecycle-managed SQLAlchemy engine with:

```python
engine = context.clients.create("database")
```

Keep raw driver APIs such as `oracledb.connect()` out of namespaces; use SQLAlchemy
Core/ORM sessions or connections from the shared engine instead.

LangChain MongoDB connectors are configured separately from the SQLAlchemy database
provider, so they can be used with `MCP_PORTAL_DATABASE_PROVIDER=none`, `oracle`, or
`sqlalchemy`.

Install the MongoDB connector extra without Oracle dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mongodb]"
```

Configure `MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING` and
`MCP_PORTAL_LANGCHAIN_MONGODB_DATABASE`. Collection names are hard-coded in the portal
so deployments cannot drift by changing environment variables. The built-in aliases are
`documents`, `chat_history`, `cache`, and `semantic_cache`. Use
`MCP_PORTAL_LANGCHAIN_MONGODB_VECTOR_SEARCH_INDEX` to override the Atlas Vector Search
index name.

Namespaces can request the lazy connector helper with:

```python
connectors = context.clients.create("langchain_mongodb")
vector_store = connectors.vector_search(embedding=embeddings)
history = connectors.chat_message_history(session_id="chat-session")
cache = connectors.cache()
```

The helper also exposes `cache()`, `semantic_cache()`, `loader()`, `doc_store()`, and
`agent_database()` for the matching `langchain-mongodb` integration classes.

FastMCP project configuration is included:

```powershell
fastmcp run
fastmcp run fastmcp.prod.json
```

The production config includes SQLAlchemy plus the Oracle dialect driver and uses the
production server factory.

FastMCP emits OpenTelemetry spans when launched with an OpenTelemetry SDK or
`opentelemetry-instrument`. Set `OTEL_SERVICE_NAME` and
`OTEL_EXPORTER_OTLP_ENDPOINT` to route traces to your collector.

## Contracts

Tool contracts can be fingerprinted in CI to catch accidental schema drift:

```python
from mcp_portal.contracts import generate_tool_contract_manifest
from mcp_portal.server import create_mcp

manifest = await generate_tool_contract_manifest(create_mcp(include_debug_ui=False))
```

Each fingerprint is based on the MCP-facing tool payload, including the tool key and
input/output schema.

## Debug Tools

```powershell
.\.venv\Scripts\Activate.ps1
mcp-portal --transport streamable-http --port 8000
```

This starts the MCP server over streamable HTTP on port 8000 and exposes the
`portal_debug` and `debug_snapshot` tools for local diagnostics. Use `--port` if
the port is already taken.

The debug payload is composed centrally. Namespaces contribute status checks and
debug panels through their manifest, while the portal owns redaction and presentation.
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
from mcp.server.fastmcp import FastMCP

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
        snapshot={"large_model": context.settings.large_language_model},
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

    @server.tool(meta={"tags": ["example", "readonly"]})
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
