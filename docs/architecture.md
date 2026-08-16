# MCP Portal Architecture

## Decision: FastMCP 3 application runtime

MCP Portal uses FastMCP 3 as its application runtime and the official `mcp` package for
protocol types. Portal request governance is implemented with FastMCP middleware and public
provider APIs. Runtime code must not depend on private component managers from either package.

This decision replaces the former hybrid in which the official SDK `FastMCP` server was
subclassed while FastMCP 3 supplied clients, authentication, and an unused middleware stack.

## Deployment model

The default deployment is a governed modular monolith:

1. The protocol layer owns MCP transports and catalog operations.
2. Middleware owns identity, authorization, approval, admission, audit, and telemetry.
3. Namespace providers own domain tools, resources, templates, and prompts.
4. `PortalServices` is the composition boundary for deployment-specific adapters.

Trusted namespaces run in process. A remote provider boundary is available for namespaces
that require independent scaling, release ownership, or security isolation. Moving a namespace
out of process is a deployment decision and does not change its public MCP contract.

## Execution-cell boundary

After authorization, approval, quota, and concurrency admission, middleware opens one
single-use execution cell around the tool handler. The cell binds its opaque identifier to the
request, exact tool, verified authorization partition, namespace, namespace-owned data
classification, isolation mode, and deadline. Namespace invocation capabilities verify this
binding on every access. Nested cells, cross-namespace access, and context inherited by work that
outlives the cell are rejected.

Local cells are logical boundaries for trusted in-process providers. They do not attempt to make
Python code an operating-system sandbox. Classifications configured as remote-only fail namespace
mounting unless the provider is a `RemoteNamespaceProvider`; `restricted` is remote-only by
default. The remote service remains responsible for its own workload identity, filesystem,
network, syscall, secret, and resource controls.

## Dependency rules

- Namespace code depends on `NamespaceContext`, not concrete infrastructure adapters.
- Production infrastructure enters through `PortalServices`.
- Built-in namespaces are registered explicitly. Trusted third-party namespace packages use
  the `mcp_portal.namespaces` Python entry-point group.
- Multi-instance production deployments provide shared quota and durable task adapters.
- Production deployments configure durable audit, cost, credential, and approval adapters as
  required by their enabled capabilities.
