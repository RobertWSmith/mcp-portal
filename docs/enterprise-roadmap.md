# Enterprise MCP Roadmap

This roadmap evolves MCP Portal from an integration scaffold into a governed enterprise
control plane. Protocol-specific behavior stays behind adapters so specification changes
do not leak into namespace business logic.

## Phase 1: Enforceable foundations

1. **Authorization and OAuth posture** — enforce policy in the active tool-call path,
   validate issuer, audience, and canonical resource URI, and optionally fail startup when
   production authentication is absent.
2. **Tenant context and audit** — derive subject, tenant, client, and scopes only from a
   verified token; partition downstream state using that context; emit sanitized lifecycle
   records through an append-only audit sink.

   Implemented tenant façades provide stable non-reversible partition tokens, reserved
   MongoDB metadata and filters, SQLAlchemy bind parameters, subject-scoped chat sessions,
   cache/vector wrappers, and task methods that never accept caller-supplied ownership.
   Semantic-cache entries additionally carry opaque tenant and authorization partitions. Every
   similarity lookup is constrained with backend metadata filters covering the verified subject,
   client, entitlements, authentication method, tool, and namespace-owned policy version.
   Cross-tenant administrative tools must opt in with `tenant_override` and require
   `tenant.admin`.
3. **Outbound trust boundary** — require an audience-bound credential broker and validate
   outbound HTTPS destinations against an explicit hostname policy. Inbound MCP tokens are
   never exposed through the namespace context.

   Implemented data-aware egress binds every downstream request to verified actor/client/tool
   context, the namespace classification floor, a stable purpose, HTTP method, destination
   ceiling, structured payload inspection, and a same-origin credential audience. Sensitive
   fields block by default or are removed before the approved callback receives the payload;
   sanitized decisions are audited before credential exchange.
4. **Bounded execution** — apply per-tenant/subject/tool quota keys, concurrency admission,
   deadlines, and response-size limits. Replace the memory quota backend with Redis or an
   API-gateway adapter for horizontally scaled deployments.

   Implemented execution cells create a single-use request/tool/identity/namespace lease after
   admission, reject nesting and expired inherited contexts, bind namespace capabilities to the
   active cell, and correlate cell start/completion audit evidence. Restricted namespaces require
   the existing remote provider process or network boundary by default; local cells are explicitly
   treated as logical isolation for trusted code rather than an OS sandbox.

## Phase 2: Governed capabilities

5. **Catalog and lifecycle** — require namespace owner, version, maturity, classification,
   scopes, dependencies, timeout, and deprecation metadata. Compare committed tool contract
   fingerprints in CI and require approval for removals, changes, or permission expansion.
6. **Asynchronous tasks** — keep task storage behind a protocol-neutral interface. A
   production backend must persist state, bind every operation to subject and tenant, enforce
   TTL/concurrency limits, and record lifecycle audit events before an MCP Tasks adapter is
   enabled.
7. **Consent and safe mutations** — mark tools using standard MCP safety annotations.
   Destructive tools fail closed unless an external verifier validates a single-use approval
   receipt bound to actor, tool, arguments, and expiration. Mutating namespaces should also
   expose dry-run and idempotency contracts.
8. **Complete MCP providers** — namespaces may contribute tools, resources, templates, and
   prompts. Resources are preferred for governed read-only context; prompts remain
   user-controlled; model-controlled tools remain policy-enforced.

## Phase 3: Operations and provenance

9. **Operational evidence** — use separate liveness and readiness routes, OpenTelemetry,
   audit events, latency/outcome metrics, and per-tenant cost records. Readiness should gain
   bounded live dependency checks as production namespaces declare required dependencies.

   Implemented instrumentation emits tool count/duration/admission wait, downstream
   count/duration/circuit state, generic provider usage, and estimated-cost metrics. Detailed
   append-only accounting records retain trusted tenant/request ownership, exact decimal values,
   currency, and pricing version without using high-cardinality identities in metrics by default.
   Production deployments still need an OpenTelemetry SDK/exporter and a durable cost sink.
10. **Supply-chain governance** — enforce namespace allowlists, dependency review, CodeQL,
    SBOM generation, Dependabot, signed release artifacts, and build provenance. Independently
    owned or untrusted namespaces can use the implemented `RemoteNamespaceProvider` process or
    network boundary instead of being imported into the portal process. Deployments still own
    remote service identity, transport security, and availability policy.

## Production adapters still required

The repository includes safe interfaces and in-memory reference implementations. Before a
multi-instance production rollout, provide organization-specific adapters for:

- OPA, Cedar, or another centrally managed ABAC policy decision point
- WORM/SIEM audit export
- OAuth token exchange or workload-identity credential brokerage
- Redis or gateway-backed distributed quotas
- Durable encrypted task persistence and workers
- Single-use approval receipt verification
- Secret-manager resolution and rotation
- Signed namespace provenance and admission

These adapters intentionally remain deployment choices rather than dependencies embedded in
namespace code.
