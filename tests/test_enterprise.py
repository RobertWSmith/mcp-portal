from __future__ import annotations

from dataclasses import replace

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_portal.audit import AuditDetails, MemoryAuditSink, audit_event, digest_arguments
from mcp_portal.approvals import RejectingApprovalVerifier
from mcp_portal.config import (
    AuthSettings,
    AuthorizationSettings,
    EnterpriseSettings,
    HttpSettings,
    MiddlewareSettings,
)
from mcp_portal.contracts import compare_tool_contract_manifests
from mcp_portal.credentials import RejectingCredentialBroker
from mcp_portal.egress import EgressPolicy
from mcp_portal.errors import (
    ConfigurationPortalError,
    PermissionPortalError,
    TimeoutPortalError,
    UpstreamPortalError,
    ValidationPortalError,
)
from mcp_portal.namespaces import Namespace, NamespaceProvider
from mcp_portal.policy import PolicyDecision, ScopePolicyEngine
from mcp_portal.clients import ClientFactories
from mcp_portal.resilience import (
    AdmissionController,
    CircuitBreaker,
    CircuitState,
    MemoryQuotaBackend,
)
from mcp_portal.security import (
    InvocationContext,
    InvocationIdentity,
    current_invocation,
    identity_from_token,
    reset_invocation,
    set_invocation,
)
from mcp_portal.server import (
    PortalDependencies,
    add_operational_routes,
    create_mcp,
    create_production_mcp,
)
from mcp_portal.tasks import MemoryTaskStore
from mcp_portal.testing import create_test_settings


class DenyPolicy:
    """Deny every test invocation."""

    async def authorize(self, invocation, tool, arguments):
        """Return a deterministic denial."""
        _ = invocation, tool, arguments
        return PolicyDecision(False, "test denial", frozenset({"blocked"}))


def test_identity_and_context_are_tenant_bound() -> None:
    token = AccessToken(
        token="secret",
        client_id="client",
        scopes=["read"],
        subject="subject",
        claims={"tenant_id": "tenant", "amr": "mfa"},
    )
    identity = identity_from_token(token, "tenant_id")
    invocation = InvocationContext("request", "tool", identity, 5)

    context_token = set_invocation(invocation)
    assert current_invocation() == invocation
    reset_invocation(context_token)
    assert identity.tenant_id == "tenant"
    assert identity.scopes == frozenset({"read"})


@pytest.mark.asyncio
async def test_scope_policy_requires_verified_scopes() -> None:
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="static", static_token="token"),
    )
    child = FastMCP("test")

    @child.tool(meta={"tags": ["write", "destructive"]})
    def mutate() -> str:
        """Mutate test state."""
        return "ok"

    tool = child._tool_manager.get_tool("mutate")
    assert tool is not None
    engine = ScopePolicyEngine(settings)
    denied = await engine.authorize(
        InvocationContext("r", "mutate", InvocationIdentity(subject="s"), 1), tool, {}
    )
    allowed = await engine.authorize(
        InvocationContext(
            "r",
            "mutate",
            InvocationIdentity(subject="s", scopes=frozenset({"write", "admin"})),
            1,
        ),
        tool,
        {},
    )
    assert denied.allowed is False
    assert allowed.allowed is True
    assert "approval_required" in allowed.obligations


@pytest.mark.asyncio
async def test_active_server_path_emits_sanitized_audit_events() -> None:
    audit = MemoryAuditSink()
    server = create_mcp(
        create_test_settings(),
        dependencies=PortalDependencies(audit_sink=audit),
    )

    result = await server.call_tool("health_ping", {})

    assert result
    assert [event.event for event in audit.events] == ["authorization", "completion"]
    assert audit.events[1].outcome == "succeeded"
    assert audit.events[0].argument_digest == digest_arguments({})


@pytest.mark.asyncio
async def test_active_server_path_denies_before_tool_execution() -> None:
    audit = MemoryAuditSink()
    server = create_mcp(
        create_test_settings(),
        dependencies=PortalDependencies(policy_engine=DenyPolicy(), audit_sink=audit),
    )

    with pytest.raises(PermissionPortalError):
        await server.call_tool("health_ping", {})

    assert len(audit.events) == 1
    assert audit.events[0].allowed is False


@pytest.mark.asyncio
async def test_catalog_only_discloses_authorized_namespaces_and_components() -> None:
    """Verify namespace policy filters discovery and direct component access."""

    def namespace_server(label: str):
        def create(context) -> NamespaceProvider:
            _ = context
            provider = NamespaceProvider(label)

            @provider.tool(meta={"tags": ["readonly"]})
            def inspect_record() -> str:
                """Inspect a governed record."""
                return label

            @provider.resource(f"portal://{label}/record")
            def record() -> str:
                """Return a governed resource."""
                return label

            @provider.resource(f"portal://{label}/records/{{record_id}}")
            def record_by_id(record_id: str) -> str:
                """Return a governed resource-template result."""
                return f"{label}:{record_id}"

            @provider.prompt(name="review")
            def review() -> str:
                """Return a governed review prompt."""
                return f"Review {label}"

            return provider

        return create

    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="static", static_token="configured-token"),
        authorization=AuthorizationSettings(
            tag_scopes={},
            namespace_scopes={"finance": ("finance.read",)},
        ),
    )
    server = create_mcp(
        settings,
        namespaces=[
            Namespace("finance", namespace_server("finance")),
            Namespace(
                "hr",
                namespace_server("hr"),
                required_scopes=frozenset({"hr.read"}),
            ),
        ],
    )
    access_token = AccessToken(
        token="verified-token",
        client_id="catalog-client",
        scopes=["finance.read"],
        subject="alice",
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        tools = await server.list_tools()
        resources = await server.list_resources()
        templates = await server.list_resource_templates()
        prompts = await server.list_prompts()

        assert [tool.name for tool in tools] == ["finance_inspect_record"]
        assert tools[0].meta["required_scopes"] == ["finance.read"]
        assert [str(resource.uri) for resource in resources] == ["portal://finance/record"]
        assert [template.uriTemplate for template in templates] == [
            "portal://finance/records/{record_id}"
        ]
        assert [prompt.name for prompt in prompts] == ["finance_review"]
        assert await server.call_tool("finance_inspect_record", {})

        with pytest.raises(PermissionPortalError):
            await server.call_tool("hr_inspect_record", {})
        with pytest.raises(ValueError, match="Unknown resource"):
            await server.read_resource("portal://hr/record")
        with pytest.raises(ValueError, match="Unknown resource"):
            await server.read_resource("portal://hr/records/employee-1")
        with pytest.raises(ValueError, match="Unknown prompt"):
            await server.get_prompt("hr_review")
    finally:
        auth_context_var.reset(context_token)


@pytest.mark.asyncio
async def test_catalog_fails_closed_without_identity_or_with_custom_denial() -> None:
    """Verify authenticated discovery requires identity and honors custom policy."""
    authenticated_settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="static", static_token="configured-token"),
    )
    authenticated = create_mcp(authenticated_settings)
    assert await authenticated.list_tools() == []

    denied = create_mcp(
        create_test_settings(),
        dependencies=PortalDependencies(policy_engine=DenyPolicy()),
    )
    assert await denied.list_tools() == []


@pytest.mark.asyncio
async def test_destructive_operations_fail_closed_without_approval() -> None:
    def destructive_provider(context) -> NamespaceProvider:
        _ = context
        provider = NamespaceProvider("destructive")

        @provider.tool(meta={"tags": ["destructive"]})
        def erase() -> str:
            """Erase test state."""
            return "erased"

        return provider

    server = create_mcp(
        create_test_settings(),
        namespaces=[Namespace("danger", destructive_provider)],
        dependencies=PortalDependencies(approval_verifier=RejectingApprovalVerifier()),
    )
    with pytest.raises(PermissionPortalError, match="approved"):
        await server.call_tool("danger_erase", {})


@pytest.mark.asyncio
async def test_deadline_and_response_size_are_enforced() -> None:
    def slow_provider(context) -> NamespaceProvider:
        _ = context
        provider = NamespaceProvider("slow")

        @provider.tool(meta={"tags": ["readonly"]})
        async def wait() -> str:
            """Wait beyond the test deadline."""
            await anyio.sleep(0.05)
            return "done"

        return provider

    settings = replace(
        create_test_settings(),
        enterprise=EnterpriseSettings(tool_timeout_seconds=0.01),
        middleware=MiddlewareSettings(enabled=True, response_max_bytes=1),
    )
    namespace = Namespace("slow", slow_provider)
    server = create_mcp(
        settings,
        namespaces=[namespace],
        include_production_middleware=True,
    )
    with pytest.raises(TimeoutPortalError):
        await server.call_tool("slow_wait", {})

    normal = create_mcp(
        replace(settings, enterprise=EnterpriseSettings(tool_timeout_seconds=1)),
        include_production_middleware=True,
    )
    with pytest.raises(UpstreamPortalError):
        await normal.call_tool("health_ping", {})


@pytest.mark.asyncio
async def test_per_tool_deadline_and_concurrency_overrides_are_enforced() -> None:
    """Verify fully-qualified deployment overrides govern each tool independently."""
    active = 0
    maximum_active = 0

    def controlled_provider(context) -> NamespaceProvider:
        """Create tools with deliberately looser metadata than deployment policy."""
        _ = context
        provider = NamespaceProvider("controlled")

        @provider.tool(meta={"timeout_seconds": 1, "max_concurrency": 5})
        async def work(delay: float = 0.02) -> str:
            """Track concurrent work while sleeping briefly."""
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                await anyio.sleep(delay)
            finally:
                active -= 1
            return "done"

        return provider

    enterprise = EnterpriseSettings(
        tool_timeout_seconds=1,
        tool_timeout_overrides={"controlled_work": 0.2},
        max_concurrent_requests=10,
        tool_concurrency_limits={"controlled_work": 1},
    )
    server = create_mcp(
        replace(create_test_settings(), enterprise=enterprise),
        namespaces=[Namespace("controlled", controlled_provider)],
    )
    results: list[object] = []

    async def invoke() -> None:
        """Invoke the controlled tool and retain its converted result."""
        results.append(await server.call_tool("controlled_work", {}))

    async with anyio.create_task_group() as group:
        group.start_soon(invoke)
        group.start_soon(invoke)

    assert len(results) == 2
    assert maximum_active == 1
    tool = server._tool_manager.get_tool("controlled_work")
    assert tool is not None
    assert enterprise.tool_timeout("controlled_work", tool.meta) == 0.2
    assert enterprise.tool_concurrency("controlled_work", tool.meta) == 1


@pytest.mark.asyncio
async def test_circuit_breaker_opens_rejects_and_recovers() -> None:
    """Verify repeated failures open a circuit and one half-open success closes it."""
    breaker = CircuitBreaker("records", failure_threshold=2, recovery_seconds=0.01)

    def fail() -> None:
        """Raise a deterministic downstream failure."""
        raise RuntimeError("offline")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="offline"):
            await breaker.execute(fail, timeout_seconds=1)

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(UpstreamPortalError, match="circuit is open"):
        await breaker.execute(lambda: "blocked", timeout_seconds=1)

    await anyio.sleep(0.02)
    assert breaker.state is CircuitState.HALF_OPEN
    assert await breaker.execute(lambda: "restored", timeout_seconds=1) == "restored"
    assert breaker.snapshot()["state"] == "closed"


@pytest.mark.asyncio
async def test_downstream_timeout_and_readiness_expose_open_circuit() -> None:
    """Verify dependency timeouts feed circuit state into readiness safely."""
    clients = ClientFactories().with_factory(
        "records",
        lambda: object(),
        readiness_check=lambda: (_ for _ in ()).throw(RuntimeError("secret endpoint")),
    )
    clients.circuit_breakers.failure_threshold = 1

    async def slow() -> None:
        """Exceed the downstream deadline."""
        await anyio.sleep(0.05)

    with pytest.raises(TimeoutPortalError):
        await clients.execute("records", slow, timeout_seconds=0.01)

    status = await clients.check_readiness()
    assert status == {
        "records": {
            "status": "error",
            "error_type": "UpstreamPortalError",
            "circuit": "open",
        }
    }


@pytest.mark.asyncio
async def test_readiness_fails_when_a_registered_dependency_is_unavailable() -> None:
    """Verify readiness rejects traffic while liveness remains healthy."""
    clients = ClientFactories().with_factory(
        "records",
        lambda: object(),
        readiness_check=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    settings = replace(
        create_test_settings(),
        enterprise=EnterpriseSettings(circuit_breaker_failure_threshold=1),
        http=HttpSettings(),
    )
    server = create_mcp(
        settings,
        dependencies=PortalDependencies(clients=clients),
    )
    add_operational_routes(server, settings)

    async with AsyncClient(
        transport=ASGITransport(app=server.streamable_http_app()),
        base_url="http://test",
    ) as client:
        live = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert live.status_code == 200
    assert live.json()["status"] == "alive"
    assert ready.status_code == 503
    assert ready.json()["dependencies"]["records"]["status"] == "error"


def test_egress_policy_blocks_unapproved_and_private_destinations() -> None:
    policy = EgressPolicy(frozenset({"api.example.com"}))
    assert policy.validate_url("https://api.example.com/v1") == "https://api.example.com/v1"
    with pytest.raises(PermissionPortalError):
        policy.validate_url("https://other.example.com")
    with pytest.raises(PermissionPortalError):
        EgressPolicy().validate_url("https://127.0.0.1")
    with pytest.raises(ValidationPortalError):
        policy.validate_url("http://api.example.com")


@pytest.mark.asyncio
async def test_downstream_credentials_fail_closed() -> None:
    with pytest.raises(PermissionPortalError):
        await RejectingCredentialBroker().credential_for(InvocationIdentity(), "https://api")


@pytest.mark.asyncio
async def test_memory_quota_and_admission_controller() -> None:
    backend = MemoryQuotaBackend()
    assert await backend.consume("key", 1, 1) is True
    assert await backend.consume("key", 1, 1) is False
    controller = AdmissionController(1, backend)
    with pytest.raises(PermissionPortalError):
        await controller.check_quota("key", 1, 1)


def test_task_store_enforces_owner_tenant_ttl_and_concurrency() -> None:
    store = MemoryTaskStore(max_ttl_seconds=60, max_per_owner=1)
    task = store.create("owner", "tenant", 30)
    assert store.get(task.task_id, "owner", "tenant") == task
    assert store.list("owner", "tenant") == (task,)
    with pytest.raises(PermissionPortalError):
        store.get(task.task_id, "other", "tenant")
    with pytest.raises(PermissionPortalError):
        store.create("owner", "tenant", 30)
    completed = store.update(
        task.task_id, "owner", "tenant", status="completed", result={"ok": True}
    )
    assert completed.result == {"ok": True}
    with pytest.raises(ValidationPortalError):
        store.create("owner", "tenant", 61)
    with pytest.raises(PermissionPortalError):
        store.create("", "tenant", 30)


def test_production_validation_rejects_unsafe_oauth_posture() -> None:
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="jwt", jwt_public_key="key"),
        enterprise=EnterpriseSettings(require_auth=True),
    )
    with pytest.raises(ConfigurationPortalError) as error:
        settings.validate_production()
    assert "JWT issuer is required" in str(error.value.details)

    safe = replace(
        settings,
        auth=replace(
            settings.auth,
            jwt_issuer="https://issuer.example",
            jwt_audience="https://portal.example/mcp",
            resource_server_url="https://portal.example/mcp",
        ),
    )
    safe.validate_production()


def test_governed_provider_mounts_tools_resources_and_prompts() -> None:
    server = create_mcp(create_test_settings())
    tool = next(tool for tool in server._tool_manager.list_tools() if tool.name == "health_ping")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.meta["owner"] == "platform-engineering"
    assert "portal://health/runtime/config" in server._resource_manager._resources
    assert "portal://health/runtime/{section}" in server._resource_manager._templates
    assert "health_diagnose" in server._prompt_manager._prompts


def test_governed_tool_merges_explicit_and_inferred_semantics() -> None:
    """Verify explicit annotations win while missing standard hints are inferred."""
    provider = NamespaceProvider("Example")

    @provider.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        meta={"tags": ["idempotent", "external"]},
    )
    def update_record() -> dict[str, bool]:
        """Return a placeholder update result."""
        return {"updated": True}

    server = create_mcp(create_test_settings(), namespaces=())
    server.mount(provider, namespace="example")
    tool = server._tool_manager.get_tool("example_update_record")

    assert tool is not None
    assert tool.title == "Example Update Record"
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is True
    assert tool.annotations.destructiveHint is None


def test_namespace_allowlist_controls_provider_admission() -> None:
    settings = replace(
        create_test_settings(),
        enterprise=EnterpriseSettings(namespace_allowlist=("missing",)),
    )
    server = create_mcp(settings)
    assert server._tool_manager.list_tools() == []


@pytest.mark.asyncio
async def test_readiness_route_reports_namespace_state() -> None:
    settings = replace(create_test_settings(), http=HttpSettings())
    app = create_production_mcp(settings).streamable_http_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["namespaces"]["health"] == "ok"


def test_contract_manifest_comparison_classifies_changes() -> None:
    changes = compare_tool_contract_manifests(
        {"removed": "1", "changed": "1", "same": "1"},
        {"added": "1", "changed": "2", "same": "1"},
    )
    assert changes == {
        "added": ("added",),
        "removed": ("removed",),
        "changed": ("changed",),
    }


def test_audit_event_omits_raw_arguments() -> None:
    invocation = InvocationContext("r", "tool", InvocationIdentity(subject="s"), 1)
    event = audit_event(
        "completion",
        invocation,
        {"password": "secret"},
        AuditDetails(outcome="failed"),
    )
    assert "secret" not in str(event)
