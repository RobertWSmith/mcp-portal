from __future__ import annotations

from dataclasses import replace

import anyio
import pytest
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.testclient import TestClient

from mcp_portal.audit import MemoryAuditSink, audit_event, digest_arguments
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
from mcp_portal.namespaces import Namespace
from mcp_portal.policy import PolicyDecision, ScopePolicyEngine
from mcp_portal.resilience import AdmissionController, MemoryQuotaBackend
from mcp_portal.security import (
    InvocationContext,
    InvocationIdentity,
    current_invocation,
    identity_from_token,
    reset_invocation,
    set_invocation,
)
from mcp_portal.server import create_mcp, create_production_mcp
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
    server = create_mcp(create_test_settings(), include_debug_ui=False, audit_sink=audit)

    result = await server.call_tool("health_ping", {})

    assert result
    assert [event.event for event in audit.events] == ["authorization", "completion"]
    assert audit.events[1].outcome == "succeeded"
    assert audit.events[0].argument_digest == digest_arguments({})


@pytest.mark.asyncio
async def test_active_server_path_denies_before_tool_execution() -> None:
    audit = MemoryAuditSink()
    server = create_mcp(
        create_test_settings(), include_debug_ui=False, policy_engine=DenyPolicy(), audit_sink=audit
    )

    with pytest.raises(PermissionPortalError):
        await server.call_tool("health_ping", {})

    assert len(audit.events) == 1
    assert audit.events[0].allowed is False


@pytest.mark.asyncio
async def test_catalog_only_discloses_authorized_namespaces_and_components() -> None:
    """Verify namespace policy filters discovery and direct component access."""

    def namespace_server(label: str):
        def create(context) -> FastMCP:
            _ = context
            child = FastMCP(label)

            @child.tool(meta={"tags": ["readonly"]})
            def inspect_record() -> str:
                """Inspect a governed record."""
                return label

            @child.resource(f"portal://{label}/record")
            def record() -> str:
                """Return a governed resource."""
                return label

            @child.resource(f"portal://{label}/records/{{record_id}}")
            def record_by_id(record_id: str) -> str:
                """Return a governed resource-template result."""
                return f"{label}:{record_id}"

            @child.prompt(name="review")
            def review() -> str:
                """Return a governed review prompt."""
                return f"Review {label}"

            return child

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
        include_debug_ui=False,
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
    authenticated = create_mcp(authenticated_settings, include_debug_ui=False)
    assert await authenticated.list_tools() == []

    denied = create_mcp(
        create_test_settings(),
        include_debug_ui=False,
        policy_engine=DenyPolicy(),
    )
    assert await denied.list_tools() == []


@pytest.mark.asyncio
async def test_destructive_operations_fail_closed_without_approval() -> None:
    def destructive_server(context) -> FastMCP:
        _ = context
        child = FastMCP("destructive")

        @child.tool(meta={"tags": ["destructive"]})
        def erase() -> str:
            """Erase test state."""
            return "erased"

        return child

    server = create_mcp(
        create_test_settings(),
        namespaces=[Namespace("danger", destructive_server)],
        include_debug_ui=False,
        approval_verifier=RejectingApprovalVerifier(),
    )
    with pytest.raises(PermissionPortalError, match="approved"):
        await server.call_tool("danger_erase", {})


@pytest.mark.asyncio
async def test_deadline_and_response_size_are_enforced() -> None:
    def slow_server(context) -> FastMCP:
        _ = context
        child = FastMCP("slow")

        @child.tool(meta={"tags": ["readonly"]})
        async def wait() -> str:
            """Wait beyond the test deadline."""
            await anyio.sleep(0.05)
            return "done"

        return child

    settings = replace(
        create_test_settings(),
        enterprise=EnterpriseSettings(tool_timeout_seconds=0.01),
        middleware=MiddlewareSettings(enabled=True, response_max_bytes=1),
    )
    namespace = Namespace("slow", slow_server)
    server = create_mcp(
        settings,
        namespaces=[namespace],
        include_debug_ui=False,
        include_production_middleware=True,
    )
    with pytest.raises(TimeoutPortalError):
        await server.call_tool("slow_wait", {})

    normal = create_mcp(
        replace(settings, enterprise=EnterpriseSettings(tool_timeout_seconds=1)),
        include_debug_ui=False,
        include_production_middleware=True,
    )
    with pytest.raises(UpstreamPortalError):
        await normal.call_tool("health_ping", {})


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
    server = create_mcp(create_test_settings(), include_debug_ui=False)
    tool = next(tool for tool in server._tool_manager.list_tools() if tool.name == "health_ping")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.meta["owner"] == "platform-engineering"
    assert "portal://runtime/config" in server._resource_manager._resources
    assert "health_diagnose" in server._prompt_manager._prompts


def test_governed_tool_merges_explicit_and_inferred_semantics() -> None:
    """Verify explicit annotations win while missing standard hints are inferred."""
    child = FastMCP("Example")

    @child.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        meta={"tags": ["idempotent", "external"]},
    )
    def update_record() -> dict[str, bool]:
        """Return a placeholder update result."""
        return {"updated": True}

    server = create_mcp(create_test_settings(), namespaces=(), include_debug_ui=False)
    server.mount(child, namespace="example")
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
    server = create_mcp(settings, include_debug_ui=False)
    assert server._tool_manager.list_tools() == []


def test_readiness_route_reports_namespace_state() -> None:
    settings = replace(create_test_settings(), http=HttpSettings())
    app = create_production_mcp(settings).streamable_http_app()
    with TestClient(app) as client:
        response = client.get("/readyz")
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
    event = audit_event("completion", invocation, {"password": "secret"}, outcome="failed")
    assert "secret" not in str(event)
