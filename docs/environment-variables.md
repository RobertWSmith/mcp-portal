# Environment Variables

MCP Portal reads runtime configuration through `Settings.from_env()` in
`src/mcp_portal/config.py`. Copy `.env.example` to `.env` for local development, then
override values in your shell, deployment platform, or an explicit dotenv file.

## Loading Rules

- If `--env-file` is provided to `mcp-portal`, that file is loaded and its values
  override existing process environment values.
- Without `--env-file`, the portal looks for `.env` in the current working directory,
  then falls back to the project root `.env`. In this mode, existing process
  environment values win over dotenv values.
- Blank optional values are treated as unset after trimming whitespace.
- Boolean values accept `1`, `true`, `yes`, and `on` for true, and `0`, `false`,
  `no`, and `off` for false. Invalid boolean values fall back to the setting default.
- Optional booleans use the same accepted values, but invalid values become unset.
- Integer and float values fall back to their defaults when unset or invalid.
- Comma-separated lists may also use spaces, so `scope-a,scope-b` and
  `scope-a scope-b` are equivalent.

## Model Provider Settings

`MCP_PORTAL_MODEL_PROVIDER` selects the active model provider. Direct OpenAI and
Azure OpenAI settings can exist side-by-side; the generic `Settings.large_language_model`,
`Settings.small_language_model`, and `Settings.embedding_model` properties resolve to
the active provider's model or deployment names.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_MODEL_PROVIDER` | `openai` | No | Active model provider. Accepted values are `openai` and `azure_openai`; unsupported values fall back to `openai`. |

### Direct OpenAI

These settings are used only when `MCP_PORTAL_MODEL_PROVIDER=openai`.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | unset | Required when provider is `openai` | Direct OpenAI platform API key. The placeholder value `your-api-key` is treated as not configured in public status snapshots. |
| `OPENAI_LARGE_LANGUAGE_MODEL` | `gpt-5.5` | No | Model name for larger language-model tasks. |
| `OPENAI_SMALL_LANGUAGE_MODEL` | `gpt-5.5-mini` | No | Model name for smaller language-model tasks. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | No | Model name for embedding tasks. |

### Azure OpenAI

Azure OpenAI uses Azure Identity / RBAC. MCP Portal does not read an Azure OpenAI API
key. Service principals are represented through the standard Azure Identity environment
variables, and managed identity, workload identity, Azure CLI login, or other
`DefaultAzureCredential` sources can be used without setting those values.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | unset | Required when provider is `azure_openai` | Azure OpenAI resource endpoint. The raw endpoint is omitted from public settings snapshots. |
| `AZURE_OPENAI_API_VERSION` | unset | Required when provider is `azure_openai` | Azure OpenAI API version passed to SDK clients. |
| `AZURE_OPENAI_TOKEN_SCOPE` | `https://cognitiveservices.azure.com/.default` | No | Token scope requested from Azure Identity credentials. |
| `AZURE_OPENAI_LARGE_LANGUAGE_MODEL_DEPLOYMENT` | unset | Required when provider is `azure_openai` | Azure OpenAI deployment name for larger language-model tasks. |
| `AZURE_OPENAI_SMALL_LANGUAGE_MODEL_DEPLOYMENT` | unset | Required when provider is `azure_openai` | Azure OpenAI deployment name for smaller language-model tasks. |
| `AZURE_OPENAI_EMBEDDING_MODEL_DEPLOYMENT` | unset | Required when provider is `azure_openai` | Azure OpenAI deployment name for embedding tasks. |

Optional service-principal environment variables consumed by Azure Identity:

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `AZURE_TENANT_ID` | unset | Required for service-principal auth | Azure tenant id. |
| `AZURE_CLIENT_ID` | unset | Required for service-principal auth | Azure application/client id. Also used by some managed identity flows. |
| `AZURE_CLIENT_SECRET` | unset | Required for service-principal auth | Azure client secret. This value is omitted from public settings snapshots and redacted from diagnostics. |

## Health And HTTP Settings

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_HEALTH_ENABLED` | `true` | No | Enables or disables the namespaced health tools. This does not remove the production operational health route. |
| `MCP_PORTAL_HTTP_PATH` | `/mcp` | No | MCP endpoint path for ASGI and production HTTP deployments. CLI `--path` can override the run path for HTTP-based transports. |
| `MCP_PORTAL_HEALTH_PATH` | `/healthz` | No | Dependency-free liveness endpoint. It reports only whether the portal process can serve HTTP. |
| `MCP_PORTAL_READINESS_PATH` | `/readyz` | No | Readiness endpoint. Returns 503 when a namespace, registered dependency probe, or downstream circuit is unhealthy. |
| `MCP_PORTAL_JSON_RESPONSE` | unset | No | Optional FastMCP JSON response mode for HTTP-based transports. Leave blank to use FastMCP defaults. |
| `MCP_PORTAL_STATELESS_HTTP` | unset | No | Optional FastMCP stateless HTTP mode. Leave blank to use FastMCP defaults. |

## Authentication Settings

`MCP_PORTAL_AUTH_PROVIDER` selects the authentication strategy for HTTP-based
production transports.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_AUTH_PROVIDER` | `none` | No | Authentication provider. Accepted values are `none`, `static`, `jwt`, `ldap`, `kerberos`, and `ldap+kerberos`; unsupported values fall back to `none`. |
| `MCP_PORTAL_AUTH_REQUIRED_SCOPES` | unset | No | Scopes required on every accepted bearer token. Accepts comma-separated or space-separated values. |
| `MCP_PORTAL_AUTH_STATIC_TOKEN` | unset | When provider is `static` | Static bearer token for local smoke tests. Static auth is not recommended for remote production deployments. |
| `MCP_PORTAL_AUTH_STATIC_CLIENT_ID` | `mcp-portal-static` | No | Client id attached to the static token. |
| `MCP_PORTAL_AUTH_STATIC_SCOPES` | unset | No | Scopes attached to the static token. If unset, static auth uses `MCP_PORTAL_AUTH_REQUIRED_SCOPES`. |
| `MCP_PORTAL_AUTH_JWT_PUBLIC_KEY` | unset | One of this or `MCP_PORTAL_AUTH_JWT_JWKS_URI` when provider is `jwt` | Static JWT verification key or shared secret accepted by FastMCP's `JWTVerifier`. |
| `MCP_PORTAL_AUTH_JWT_JWKS_URI` | unset | One of this or `MCP_PORTAL_AUTH_JWT_PUBLIC_KEY` when provider is `jwt` | Remote JWKS endpoint used to verify JWTs. |
| `MCP_PORTAL_AUTH_JWT_ISSUER` | unset | No | Optional expected JWT issuer. |
| `MCP_PORTAL_AUTH_JWT_AUDIENCE` | unset | No | Optional expected JWT audience. |
| `MCP_PORTAL_AUTH_JWT_ALGORITHM` | `RS256` | No | JWT signing algorithm to accept. |
| `MCP_PORTAL_AUTH_RESOURCE_SERVER_URL` | unset | Required for hardened JWT production | Canonical external HTTPS URI used for Protected Resource Metadata and resource/audience binding. |
| `MCP_PORTAL_AUTH_LDAP_URI` | unset | When LDAP is enabled | Directory URI. Must use `ldaps://`, or `ldap://` together with StartTLS. |
| `MCP_PORTAL_AUTH_LDAP_BASE_DN` | unset | When LDAP search mode is used | Base DN beneath which the username is searched. |
| `MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE` | unset | Alternative to base-DN search | Direct bind DN template containing `{username}`, for example `uid={username},ou=people,dc=example,dc=com`. |
| `MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER` | `(uid={username})` | No | Search filter used in base-DN search mode. It must contain `{username}`; the supplied username is RFC 4515 escaped. |
| `MCP_PORTAL_AUTH_LDAP_BIND_DN` | unset | No | Service-account DN used to find a user's DN. Configure it together with the bind password; omit both for anonymous search. |
| `MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD` | unset | With bind DN | Service-account password. Omitted from public settings snapshots. |
| `MCP_PORTAL_AUTH_LDAP_START_TLS` | `false` | With an `ldap://` URI | Upgrades the directory connection with StartTLS before any bind. |
| `MCP_PORTAL_AUTH_LDAP_CA_CERT_FILE` | system trust store | No | Optional CA bundle for LDAPS or StartTLS certificate validation. Certificate verification is always required. |
| `MCP_PORTAL_AUTH_LDAP_CONNECT_TIMEOUT` | `5` | No | Directory connection, receive, and search timeout in seconds. |
| `MCP_PORTAL_AUTH_LDAP_SCOPES` | required scopes | No | Scopes granted to successfully authenticated LDAP users. |
| `MCP_PORTAL_AUTH_KERBEROS_HOSTNAME` | unset | When Kerberos is enabled | Hostname portion of the HTTP service principal, such as `portal.example.com`. |
| `MCP_PORTAL_AUTH_KERBEROS_SERVICE` | `HTTP` | No | Service portion of the Kerberos service principal. |
| `MCP_PORTAL_AUTH_KERBEROS_KEYTAB` | platform credentials | No | Optional acceptor keytab. When set, MCP Portal initializes `KRB5_KTNAME` only if the process has not already set it. |
| `MCP_PORTAL_AUTH_KERBEROS_SCOPES` | required scopes | No | Scopes granted to successfully authenticated Kerberos principals. |

For remote HTTP deployments, prefer `MCP_PORTAL_AUTH_PROVIDER=jwt` with a JWKS URI or
public key. The static provider exists mainly for local smoke tests.

LDAP accepts standard HTTP Basic credentials and validates them with a directory bind.
Kerberos accepts standard HTTP `Negotiate` service tickets and deliberately requests the
Kerberos protocol rather than allowing NTLM fallback. When both are needed, set
`MCP_PORTAL_AUTH_PROVIDER=ldap+kerberos`; a request succeeds through either configured
mechanism. Serve the portal itself over HTTPS whenever Basic authentication is enabled.

Install the matching optional dependency before enabling a provider:

```powershell
python -m pip install -e ".[ldap]"
python -m pip install -e ".[kerberos]"
# Or install both:
python -m pip install -e ".[enterprise-auth]"
```

Example using an LDAP search account and a Kerberos keytab:

```dotenv
MCP_PORTAL_AUTH_PROVIDER=ldap+kerberos
MCP_PORTAL_AUTH_REQUIRED_SCOPES=portal
MCP_PORTAL_AUTH_LDAP_URI=ldaps://directory.example.com:636
MCP_PORTAL_AUTH_LDAP_BASE_DN=ou=people,dc=example,dc=com
MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER=(uid={username})
MCP_PORTAL_AUTH_LDAP_BIND_DN=cn=mcp-portal,ou=services,dc=example,dc=com
MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD=change-me
MCP_PORTAL_AUTH_KERBEROS_HOSTNAME=portal.example.com
MCP_PORTAL_AUTH_KERBEROS_SERVICE=HTTP
MCP_PORTAL_AUTH_KERBEROS_KEYTAB=/run/secrets/mcp-portal.keytab
```

## Authorization Settings

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_AUTHZ_TAG_SCOPES` | `admin=admin;destructive=admin;external=external;write=write` | No | Maps FastMCP component tags to required OAuth scopes for production authorization checks. |
| `MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES` | unset | No | Maps namespace names to scopes required for discovery and access. Unauthorized namespace tools, resources, templates, and prompts are omitted from catalog responses. |

Rules are separated by semicolons. Each rule uses `=` or `:` between the tag and its
scopes. Scopes can be comma-separated or space-separated.

Example:

```dotenv
MCP_PORTAL_AUTHZ_TAG_SCOPES=admin=portal.admin;write=portal.write portal.audit
MCP_PORTAL_AUTHZ_NAMESPACE_SCOPES=finance=finance.read;hr=hr.read hr.audit
```

If the tag value is malformed, MCP Portal falls back to the default tag rules. A malformed
namespace value falls back to no deployment-specific namespace rules; manifest-level
`required_scopes` continue to apply.

## Production Middleware Settings

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_MIDDLEWARE_ENABLED` | `false` | No | Enables production middleware for normal `create_mcp()` startup. The `--production` CLI flag and `create_production_mcp()` force production middleware on regardless of this value. |
| `MCP_PORTAL_STRUCTURED_LOGGING` | `true` | No | Emits request logs as structured JSON when middleware is enabled. When false, standard logging middleware is used. |
| `MCP_PORTAL_LOG_PAYLOAD_LENGTHS` | `true` | No | Includes request payload lengths in middleware logs. |
| `MCP_PORTAL_RATE_LIMIT_PER_SECOND` | `25` | No | Sustained global request rate for the token-bucket rate limiter. Set to `0` or lower to skip rate limiting. |
| `MCP_PORTAL_RATE_LIMIT_BURST` | `50` | No | Burst capacity for the rate limiter. |
| `MCP_PORTAL_RESPONSE_MAX_BYTES` | `1000000` | No | Maximum serialized tool response size. Set to `0` or lower to skip response-size limiting. |

Production middleware includes error handling, optional rate limiting, tag-based
authorization checks, request logging, timing, and optional response-size limiting.

## Namespace Discovery

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_NAMESPACE_DISCOVERY_STRICT` | `false` | No | When true, namespace import failures stop server startup. When false, discovery can continue past optional namespace import failures. |

## SQLAlchemy Database Settings

Relational database access should go through a shared SQLAlchemy engine. Oracle is the
preferred SQLAlchemy backend, but a portable SQLAlchemy URL can be used for tests or
future relational database targets.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_DATABASE_PROVIDER` | `oracle` | No | Database provider. Accepted values are `none`, `oracle`, and `sqlalchemy`; unsupported values fall back to `oracle`. Set to `none` to skip registering the shared database client. |
| `MCP_PORTAL_DATABASE_SQLALCHEMY_URL` | unset | Required when provider is `sqlalchemy`; optional override when provider is `oracle` | SQLAlchemy engine URL. When set, it takes precedence over Oracle DSN/user/password settings. |
| `MCP_PORTAL_ORACLE_DSN` | unset | Required with Oracle settings when no SQLAlchemy URL is set | Oracle DSN used with the `oracle+oracledb` SQLAlchemy dialect. |
| `MCP_PORTAL_ORACLE_USER` | unset | Required with Oracle settings when no SQLAlchemy URL is set | Oracle database username. |
| `MCP_PORTAL_ORACLE_PASSWORD` | unset | Required with Oracle settings when no SQLAlchemy URL is set | Oracle database password. |
| `MCP_PORTAL_ORACLE_POOL_MIN` | `1` | No | SQLAlchemy pool size. |
| `MCP_PORTAL_ORACLE_POOL_MAX` | `4` | No | Maximum Oracle checked-out connections including overflow. Overflow is computed as `max(0, MCP_PORTAL_ORACLE_POOL_MAX - MCP_PORTAL_ORACLE_POOL_MIN)`. |

For Oracle deployments:

```dotenv
MCP_PORTAL_DATABASE_PROVIDER=oracle
MCP_PORTAL_ORACLE_DSN=db.example/orclpdb1
MCP_PORTAL_ORACLE_USER=portal
MCP_PORTAL_ORACLE_PASSWORD=change-me
```

For portable SQLAlchemy deployments:

```dotenv
MCP_PORTAL_DATABASE_PROVIDER=sqlalchemy
MCP_PORTAL_DATABASE_SQLALCHEMY_URL=sqlite:///portal.db
```

Install the matching optional dependency before using a database backend:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[database]"
```

Use `".[oracle]"` instead when connecting to Oracle.

## LangChain MongoDB Settings

LangChain MongoDB connectors are independent of `MCP_PORTAL_DATABASE_PROVIDER`. Use
these settings when namespaces need `langchain-mongodb` integrations such as Atlas
Vector Search, chat history, caches, loaders, docstores, or agent-toolkit database
wrappers. Collection names are hard-coded in the portal and are not configured through
environment variables.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING` | unset | Required to register the `langchain_mongodb` client factory | MongoDB connection URI. This value is omitted from public settings snapshots and redacted from diagnostics. |
| `MCP_PORTAL_LANGCHAIN_MONGODB_DATABASE` | unset | Required for vector search, loader, docstore, and agent database helpers unless a namespace passes an override | Default MongoDB database name. |
| `MCP_PORTAL_LANGCHAIN_MONGODB_VECTOR_SEARCH_INDEX` | `vector_index` | No | Default Atlas Vector Search index name used by vector search and semantic cache helpers. |

Hard-coded collection aliases:

| Alias | Collection | Default Uses |
| --- | --- | --- |
| `documents` | `documents` | Vector search, loader, docstore |
| `chat_history` | `chat_history` | Chat message history |
| `cache` | `cache` | MongoDB cache |
| `semantic_cache` | `semantic_cache` | Atlas semantic cache |

Example:

```dotenv
MCP_PORTAL_DATABASE_PROVIDER=none
MCP_PORTAL_LANGCHAIN_MONGODB_CONNECTION_STRING=mongodb+srv://user:password@cluster.example/
MCP_PORTAL_LANGCHAIN_MONGODB_DATABASE=portal
MCP_PORTAL_LANGCHAIN_MONGODB_VECTOR_SEARCH_INDEX=portal_vector
```

Install the optional connector dependency before using these helpers:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mongodb]"
```

Namespaces request the helper independently from the SQLAlchemy engine:

```python
connectors = context.clients.create("langchain_mongodb")
vector_store = connectors.vector_search(embedding=embeddings)
history = connectors.chat_message_history(session_id="chat-session")
cache = connectors.cache()
```

## Enterprise Control Plane Settings

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `MCP_PORTAL_PRODUCTION_REQUIRE_AUTH` | `false` | No | Fail hardened production startup when no authentication provider is configured. Enable for every remotely reachable deployment. |
| `MCP_PORTAL_REQUIRE_TENANT` | `false` | No | Deny authenticated tool calls without the configured verified tenant claim. Enable for multi-tenant deployments. |
| `MCP_PORTAL_TENANT_CLAIM` | `tenant_id` | No | Verified token claim used to partition tenant state. |
| `MCP_PORTAL_AUDIT_ENABLED` | `true` | No | Emit sanitized authorization and completion audit events. |
| `MCP_PORTAL_TOOL_TIMEOUT_SECONDS` | `45` | No | Default deadline applied to every tool invocation. |
| `MCP_PORTAL_TOOL_TIMEOUT_OVERRIDES` | unset | No | Semicolon-separated fully-qualified tool deadlines, for example `finance_export=120;search_query=5`. Deployment values override tool metadata. |
| `MCP_PORTAL_MAX_CONCURRENT_REQUESTS` | `100` | No | Maximum concurrent in-process tool executions. |
| `MCP_PORTAL_TOOL_CONCURRENCY_LIMITS` | unset | No | Semicolon-separated fully-qualified per-tool limits, for example `finance_export=2;search_query=20`. Per-tool slots are acquired before global capacity to avoid starvation. |
| `MCP_PORTAL_DOWNSTREAM_TIMEOUT_SECONDS` | `45` | No | Default deadline for operations executed through the namespace downstream boundary. |
| `MCP_PORTAL_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | No | Consecutive downstream failures required to open a dependency circuit. |
| `MCP_PORTAL_CIRCUIT_BREAKER_RECOVERY_SECONDS` | `30` | No | Open-circuit cooldown before one half-open recovery probe is admitted. |
| `MCP_PORTAL_TASK_MAX_TTL_SECONDS` | `3600` | No | Maximum task retention duration accepted by the task store. |
| `MCP_PORTAL_TASK_MAX_CONCURRENT_PER_SUBJECT` | `10` | No | Maximum working tasks owned by one authenticated subject. |
| `MCP_PORTAL_EGRESS_ALLOWED_HOSTS` | unset | Recommended | Exact comma- or space-separated HTTPS hostname allowlist exposed to namespaces. |
| `MCP_PORTAL_NAMESPACE_ALLOWLIST` | unset | Recommended | Namespace names admitted into this deployment. Empty retains automatic discovery behavior. |

Tools may declare `timeout_seconds` and `max_concurrency` in their portal `_meta`; the
fully-qualified environment maps take precedence. Namespace network and database operations
should use the shared downstream boundary so timeouts, breaker state, and readiness agree:

```python
result = await context.downstream(
    "records_api",
    lambda: records_client.fetch(record_id),
)
```

Register a dependency probe when adding a custom client factory. Probes run concurrently and
their error details are reduced to safe exception types in `/readyz` responses.

In-memory quota and task stores are reference implementations. Multi-instance deployments
must provide shared quota and durable task adapters. Destructive tools require a configured
single-use approval verifier; the default verifier denies them.

When tenant isolation is enabled, namespaces must derive storage identifiers through
`context.tenant_scope()`, execute SQL through `context.tenant_sql()` with an explicit
`:portal_tenant` bind, use `context.tenant_tasks()` instead of the raw task store, and use
`context.mongodb()` instead of creating `langchain_mongodb` directly. Arguments named
`tenant`, `tenant_id`, `organization_id`, or `org_id` are rejected unless the tool is
explicitly tagged `tenant_override` and the caller has the corresponding `tenant.admin`
scope.

## Observability Settings

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `OTEL_SERVICE_NAME` | `mcp-portal` | No | Service name exported to OpenTelemetry launchers. MCP Portal sets this process environment variable only when it is not already present. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | No | OTLP collector endpoint. When set, MCP Portal also exposes it to OpenTelemetry launchers if the process environment does not already contain a value. |
| `MCP_PORTAL_METRICS_ENABLED` | `true` | No | Emit tool, admission, downstream, usage, and estimated-cost measurements through the active OpenTelemetry meter provider. |
| `MCP_PORTAL_COST_ACCOUNTING_ENABLED` | `true` | No | Append detailed tenant- and request-bound usage records to the configured cost sink. The default sink emits canonical JSON on the `mcp_portal.cost` logger. |
| `MCP_PORTAL_METRICS_INCLUDE_TENANT` | `false` | No | Add tenant ID to metric dimensions. Leave disabled for large tenant populations to control cardinality; detailed cost records always retain the trusted tenant. |
| `MCP_PORTAL_COST_CURRENCY` | `USD` | No | Default currency attached to estimated costs reported by namespaces. |
| `MCP_PORTAL_PRICING_VERSION` | unset | Recommended | Pricing table or enterprise contract version used to calculate estimates. Store the version with every estimate so later rate changes do not alter historical meaning. |

FastMCP emits spans and MCP Portal emits runtime metrics when launched with an OpenTelemetry
SDK or `opentelemetry-instrument`. The API layer is safe when no SDK is attached: metric
instruments become no-ops while detailed cost records continue through their configured sink.

Namespaces report metered consumption after receiving authoritative provider usage. Pricing is
not hard-coded into the portal because provider and contract rates change independently of code:

```python
await context.record_usage(
    provider="azure.ai.openai",
    service="language-model",
    operation="chat",
    sku="gpt-enterprise",
    quantity=response.usage.input_tokens,
    unit="input_token",
    estimated_cost="0.0125",
)
```

Call `record_usage` separately for input, output, cached, image, request, document, or compute
units when their rates differ. Detailed records contain request, tool, subject, tenant, client,
SKU, currency, and pricing-version fields but never prompts, responses, credentials, or raw tool
arguments.

## Secret Handling

Secret-bearing values such as `OPENAI_API_KEY`, `AZURE_CLIENT_SECRET`, static bearer
tokens, JWT keys, LDAP bind passwords, database URLs, Oracle passwords, and MongoDB connection strings are
omitted from public settings snapshots. Status tools expose only whether those values
are configured.
