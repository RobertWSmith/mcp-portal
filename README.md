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

The scaffold loads `.env`, which the setup command copies from `.env.example`. See
[docs/environment-variables.md](docs/environment-variables.md) for defaults,
accepted values, loading behavior, and production requirements.

- `MCP_PORTAL_MODEL_PROVIDER` selects `openai` or `azure_openai`
- `AZURE_OPENAI_*` for Azure OpenAI endpoint, API version, token scope, and deployments
- `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` for optional service-principal auth
- `OPENAI_API_KEY` and `OPENAI_*_MODEL` only when using the direct OpenAI provider
- `MCP_PORTAL_HEALTH_ENABLED`
- `MCP_PORTAL_AUTH_*` for HTTP authentication
- `MCP_PORTAL_AUTHZ_TAG_SCOPES` for tag policy metadata
- `MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES` for per-namespace catalog visibility and access
- `MCP_PORTAL_HTTP_PATH` and `MCP_PORTAL_HEALTH_PATH`
- `MCP_PORTAL_DATABASE_PROVIDER`, `MCP_PORTAL_DATABASE_SQLALCHEMY_URL`, and `MCP_PORTAL_ORACLE_*`
- `MCP_PORTAL_MONGODB_*` for LangChain MongoDB connectors
- `MCP_PORTAL_EGRESS_ALLOWED_HOSTS`, `MCP_PORTAL_EGRESS_DESTINATION_CLASSIFICATIONS`, and
  `MCP_PORTAL_EGRESS_SENSITIVE_FIELD_ACTION` for data-aware outbound policy
- `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, and `MCP_PORTAL_*` telemetry controls

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
- `--production` for the hardened production server profile

Run `mcp-portal --help` for the complete command-line reference.

## Production

Use the production profile when exposing the portal over HTTP. It attaches lifecycle
cleanup for shared clients, wires configured bearer-token
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
for a dependency-free liveness probe. `MCP_PORTAL_READINESS_PATH` evaluates namespace hooks,
registered dependency probes, and downstream circuit state. By default the routes are `/mcp`,
`/healthz`, and `/readyz`.

Remote HTTP deployments should set `MCP_PORTAL_AUTH_PROVIDER=jwt` with either
`MCP_PORTAL_AUTH_JWT_JWKS_URI` or `MCP_PORTAL_AUTH_JWT_PUBLIC_KEY`. Static bearer
tokens are available through `MCP_PORTAL_AUTH_PROVIDER=static`, but they are intended
only for local smoke tests.

Hardened deployments should also set `MCP_PORTAL_PRODUCTION_REQUIRE_AUTH=true` and
`MCP_PORTAL_AUTH_RESOURCE_SERVER_URL` to the canonical external HTTPS MCP resource URI.
JWT production validation then requires an issuer, audience, and resource URI before the
server starts. The active tool-call path enforces tag scopes, per-identity quota partitions,
concurrency, deadlines, response limits, standard safety annotations, approval requirements,
and sanitized audit events.

Enterprise namespaces receive trusted invocation identity/tenant context, a data-aware outbound
HTTPS policy, a downstream credential-broker boundary, and an authorization-bound task store. Tool
deadlines and concurrency can be overridden by fully-qualified tool name. Namespace code can
run external work through `context.downstream(...)` for destination classification, structured
payload inspection, pre-credential audit, bounded calls, closed/open/half-open circuit breaking,
and dependency-aware readiness.
See [the enterprise roadmap](docs/enterprise-roadmap.md) for production adapters and rollout
phases.

Inject production or test adapters as one explicit dependency bundle instead of extending
the server factory signature:

```python
from mcp_portal.server import PortalServices, create_mcp

server = create_mcp(
    settings,
    services=PortalServices(
        clients=client_factories,
        policy_engine=policy_engine,
        audit_sink=audit_sink,
    ),
)
```

For multi-tenant deployments set `MCP_PORTAL_REQUIRE_TENANT=true`. Namespace tools should
use `context.tenant_scope()` for cache keys and MongoDB filters/document metadata;
`context.tenant_sql()` for statements that explicitly bind `:portal_tenant`;
`context.tenant_tasks()` for tasks; and `context.mongodb()` for partitioned chat history,
caches, vector stores, and loaders. Tenant identifiers supplied as ordinary tool arguments are
rejected by default because the verified invocation claim is authoritative.

Enterprise deployments can instead use `MCP_PORTAL_AUTH_PROVIDER=ldap`, `kerberos`, or
`ldap+kerberos`. LDAP accepts HTTP Basic credentials and requires HTTPS plus an encrypted
LDAPS/StartTLS directory connection. Kerberos accepts HTTP Negotiate tickets for a configured
service principal. Install `.[ldap]`, `.[kerberos]`, or `.[enterprise-auth]` before enabling
those providers; the full settings and examples are in
[docs/environment-variables.md](docs/environment-variables.md).

Tag metadata can be attached to SDK tools through `_meta`. Keep using `readonly`,
`write`, `admin`, `external`, and `destructive` tags on namespace tools so access
policy can stay centralized.

Namespace catalogs are filtered per verified caller. Declare code-owned baseline access with
`required_scopes` in `NamespaceMetadata`, and apply deployment-specific access with
`MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES`. Linux deployments can additionally require host NSS
memberships globally with `MCP_PORTAL_AUTH_REQUIRED_LINUX_GROUPS` or per namespace with
`MCP_PORTAL_AUTHZ_NAMESPACE_LINUX_GROUPS`. A caller that lacks any requirement does not see that
namespace's tools, resources, templates, or prompts. Calling a hidden tool directly is still
denied by policy, while hidden resources and prompts respond as unknown to avoid disclosing
their existence.

Relational database access goes through SQLAlchemy engines. Oracle is the preferred
SQLAlchemy backend for portal integrations, but namespaces should depend on SQLAlchemy
APIs so engines can be swapped for tests or future relational targets.

SQLAlchemy is included in the base installation, so generic SQLAlchemy URLs require no
additional portal extra.

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

Configure `MCP_PORTAL_MONGODB_CONNECTION_STRING` and
`MCP_PORTAL_MONGODB_DATABASE`. Collection names are hard-coded in the portal
so deployments cannot drift by changing environment variables. The built-in aliases are
`documents`, `chat_history`, `cache`, and `semantic_cache`. Use
`MCP_PORTAL_MONGODB_VECTOR_SEARCH_INDEX` to override the Atlas Vector Search
index name.

Namespaces can request the lazy connector helper with:

```python
connectors = context.mongodb()
vector_store = connectors.vector_search(embedding=embeddings)
history = connectors.chat_message_history(session_id="chat-session")
cache = connectors.cache()
semantic_cache = connectors.semantic_cache(
    embedding=embeddings,
    policy_version="example-v1",
)
```

The tenant-aware façade also exposes `loader()` for governed document loading. Namespace code
should not request the raw `langchain_mongodb` client when tenant-aware storage is required.

Semantic caches require an authenticated subject or workload plus a namespace-owned
`policy_version`. Lookups and deletes use backend-enforced tenant and authorization metadata
filters; prompt prefixes are not treated as an isolation boundary. The authorization partition
includes the verified subject, client, scopes, Linux groups, authentication method, and current
tool. Change `policy_version` whenever authorization rules or source-data semantics change, and
configure the Atlas Vector Search index so `_portal_tenant` and `_portal_authorization` are
filter fields. Missing filter-index support causes lookups to fail instead of falling back to an
unfiltered search.

FastMCP emits spans and MCP Portal emits tool, admission, downstream, usage, and estimated-cost
metrics when an OpenTelemetry SDK is attached. Set `OTEL_SERVICE_NAME` and
`OTEL_EXPORTER_OTLP_ENDPOINT` to route telemetry to your collector. Namespaces report exact
provider usage and versioned cost estimates through `context.record_usage(...)`; detailed
tenant/request accounting records are kept separate from low-cardinality dashboard metrics.

## Contracts

CI strictly validates committed namespace ownership and lifecycle metadata and compares tool
contracts with `contracts/tool-contracts.json`:

```powershell
python -m mcp_portal.governance check
```

After intentionally reviewing a tool addition, removal, schema change, or permission expansion,
update and commit the baseline with:

```powershell
python -m mcp_portal.governance update-baseline
```

Tool contracts can also be fingerprinted programmatically:

```python
from mcp_portal.contracts import generate_tool_contract_manifest
from mcp_portal.server import create_mcp

manifest = await generate_tool_contract_manifest(create_mcp())
```

Each fingerprint is based on the MCP-facing tool payload, including the tool key and
input/output schema.

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

Create a module under `src/mcp_portal/namespaces/` with a `create_provider(context)`
factory and decorate it with the namespace manifest. A provider can contribute the
complete server-side MCP surface:

```python
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from mcp_portal.namespaces import (
    NamespaceContext,
    NamespaceMetadata,
    NamespaceProvider,
    NamespaceStatus,
    register_namespace,
)


class GreetingResult(BaseModel):
    """Structured greeting returned to MCP clients."""

    message: str


def example_status(context: NamespaceContext) -> NamespaceStatus:
    """Report example namespace status."""
    return NamespaceStatus(
        state="ok",
        message="Example namespace is ready.",
        details={"namespace": context.name},
    )


@register_namespace(
    NamespaceMetadata(
        name="example",
        description="Example tools for local development.",
        tags=frozenset({"example", "readonly"}),
        health_check=example_status,
    )
)
def create_provider(context: NamespaceContext) -> NamespaceProvider:
    """Create the example namespace provider.

    Args:
        context: Runtime services shared with the namespace.

    Returns:
        Tools, resources, resource templates, and prompts owned by the namespace.
    """
    provider = NamespaceProvider("Example")

    @provider.tool(
        title="Greet User",
        annotations=ToolAnnotations(
            title="Greet User",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"tags": ["example", "readonly"]},
        structured_output=True,
    )
    def hello(name: str) -> GreetingResult:
        """Greet a user by name.

        Args:
            name: Name to greet.

        Returns:
            A structured friendly greeting.
        """
        context.logger.debug("Example greeting requested")
        return GreetingResult(message=f"Hello, {name}!")

    @provider.resource("portal://example/about", mime_type="text/plain")
    def about() -> str:
        """Describe the example namespace."""
        return "Example MCP Portal namespace"

    @provider.resource("portal://example/users/{name}", mime_type="text/plain")
    def user(name: str) -> str:
        """Return a templated user resource."""
        return f"User: {name}"

    @provider.prompt(name="welcome")
    def welcome(name: str) -> str:
        """Create a user-controlled welcome prompt."""
        return f"Welcome {name} using the example namespace."

    return provider
```

The portal prefixes tool and prompt names, so `hello` becomes `example_hello` and
`welcome` becomes `example_welcome`. Resource URIs remain stable while their display
names are prefixed. Registrations are installed through public FastMCP APIs rather than
copying private manager state. See `src/mcp_portal/namespaces/health.py` for the complete
reference.
Register built-in namespace modules explicitly in `BUILTIN_NAMESPACE_MODULES`.
Use MCP `ToolAnnotations` for client-visible behavior and Pydantic response models for
stable `outputSchema` and `structuredContent`. Keep `_meta.tags` only as portal policy
metadata; tags are not a substitute for standard MCP semantics.

Built-in namespaces are explicitly admitted by the portal. Trusted namespace packages can
publish a `mcp_portal.namespaces` Python entry point that loads a `Namespace` manifest or a
zero-argument manifest factory. The portal does not scan arbitrary installed modules.

Namespaces that need independent deployment or security isolation can return a
`RemoteNamespaceProvider` from their manifest factory. The provider uses FastMCP's proxy
boundary while the portal retains local catalog, authorization, admission, and audit policy.

Each namespace receives a `NamespaceContext` with shared settings, a namespace-scoped
logger, a redactor for safe diagnostics, a clock, and lazy external client factories.
Use `mcp_portal.testing.create_namespace_test_client` or
`mcp_portal.testing.create_namespace_test_context` for focused namespace tests.
