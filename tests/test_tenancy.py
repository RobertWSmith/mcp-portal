"""Test tenant isolation across storage, clients, policy, and configuration."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_portal.clients import ClientFactories
from mcp_portal.config import AuthSettings, EnterpriseSettings
from mcp_portal.egress import EgressPolicy
from mcp_portal.errors import PermissionPortalError, ValidationPortalError
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.policy import ScopePolicyEngine
from mcp_portal.namespaces import NamespaceDependencies
from mcp_portal.security import InvocationContext, InvocationIdentity, invocation_scope
from mcp_portal.tasks import MemoryTaskStore
from mcp_portal.tenancy import (
    TENANT_FIELD,
    TenantCacheProxy,
    TenantMongoDBConnectors,
    TenantScope,
    TenantSQLExecutor,
    TenantTaskService,
    TenantVectorStoreProxy,
)
from mcp_portal.testing import create_namespace_test_context, create_test_settings


def invocation(
    tenant: str | None = "tenant-a",
    subject: str | None = "user",
    client_id: str | None = "client",
):
    return InvocationContext(
        "request",
        "tool",
        InvocationIdentity(subject=subject, client_id=client_id, tenant_id=tenant),
        30,
    )


def test_tenant_scope_derives_stable_non_reversible_partitions() -> None:
    first = TenantScope.from_invocation(invocation("tenant-a"))
    same = TenantScope.from_invocation(invocation("tenant-a"))
    other = TenantScope.from_invocation(invocation("tenant-b"))

    assert first.partition == same.partition
    assert first.partition != other.partition
    assert "tenant-a" not in first.partition
    assert first.key("record") != other.key("record")
    assert first.key("record", subject_scoped=True).startswith(first.subject_partition)


def test_tenant_scope_rejects_missing_identity_and_invalid_keys() -> None:
    with pytest.raises(PermissionPortalError):
        TenantScope.from_invocation(invocation(None), require_tenant=True)
    anonymous = TenantScope.from_invocation(invocation(None, None, None))
    with pytest.raises(PermissionPortalError):
        _ = anonymous.owner
    with pytest.raises(ValidationPortalError):
        anonymous.key(" ")
    with pytest.raises(ValidationPortalError):
        anonymous.key("x" * 513)


def test_tenant_scope_builds_reserved_storage_constraints() -> None:
    scope = TenantScope.from_invocation(invocation())
    assert scope.mongo_filter() == {TENANT_FIELD: scope.partition}
    assert scope.mongo_filter({"status": "active"}) == {
        "$and": [{TENANT_FIELD: scope.partition}, {"status": "active"}]
    }
    assert scope.document_metadata({"kind": "memo"}) == {
        "kind": "memo",
        TENANT_FIELD: scope.partition,
    }
    assert scope.sql_parameters({"record_id": 1}) == {
        "record_id": 1,
        "portal_tenant": scope.partition,
    }
    with pytest.raises(ValidationPortalError):
        scope.mongo_filter({TENANT_FIELD: "spoof"})
    with pytest.raises(ValidationPortalError):
        scope.document_metadata({TENANT_FIELD: "spoof"})
    with pytest.raises(ValidationPortalError):
        scope.sql_parameters({"portal_tenant": "spoof"})


def test_tenant_task_service_prevents_cross_tenant_access() -> None:
    store = MemoryTaskStore()
    first = TenantTaskService(store, TenantScope.from_invocation(invocation("tenant-a")))
    second = TenantTaskService(store, TenantScope.from_invocation(invocation("tenant-b")))
    task = first.create(30)

    assert first.get(task.task_id) == task
    assert first.list() == (task,)
    assert first.update(task.task_id, status="completed", result="ok").result == "ok"
    with pytest.raises(PermissionPortalError):
        second.get(task.task_id)


def test_tenant_sql_executor_requires_trusted_bind_parameter() -> None:
    class Connection:
        def execute(self, statement, parameters):
            return statement, parameters

    executor = TenantSQLExecutor(TenantScope.from_invocation(invocation()))
    with pytest.raises(PermissionPortalError, match="portal_tenant"):
        executor.execute(Connection(), "select * from records")
    statement, parameters = executor.execute(
        Connection(),
        "select * from records where tenant_partition = :portal_tenant and id = :id",
        {"id": 7},
    )
    assert ":portal_tenant" in statement
    assert parameters["portal_tenant"] == executor.scope.partition


class FakeCache:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def lookup(self, prompt, llm_string):
        self.calls.append(("lookup", prompt, llm_string))
        return "hit"

    def update(self, prompt, llm_string, value):
        self.calls.append(("update", prompt, llm_string, value))
        return "updated"

    def clear_partition(self, partition, **kwargs):
        self.calls.append(("clear", partition, kwargs))
        return "cleared"

    async def alookup(self, prompt, llm_string):
        self.calls.append(("alookup", prompt, llm_string))
        return "async-hit"

    async def aupdate(self, prompt, llm_string, value):
        self.calls.append(("aupdate", prompt, llm_string, value))
        return "async-updated"

    async def aclear_partition(self, partition, **kwargs):
        self.calls.append(("aclear", partition, kwargs))
        return "async-cleared"


@pytest.mark.asyncio
async def test_cache_proxy_partitions_sync_and_async_keys() -> None:
    cache = FakeCache()
    scope = TenantScope.from_invocation(invocation())
    proxy = TenantCacheProxy(cache, scope)

    assert proxy.lookup("prompt", "model") == "hit"
    assert proxy.update("prompt", "model", ["value"]) == "updated"
    assert proxy.clear(collection="cache") == "cleared"
    assert await proxy.alookup("prompt", "model") == "async-hit"
    assert await proxy.aupdate("prompt", "model", ["value"]) == "async-updated"
    assert await proxy.aclear(collection="cache") == "async-cleared"
    assert all("prompt" not in call[1:2] for call in cache.calls if "lookup" in call[0])
    assert scope.partition in cache.calls[0][1]


@pytest.mark.asyncio
async def test_cache_proxy_refuses_unsafe_global_clear() -> None:
    proxy = TenantCacheProxy(object(), TenantScope.from_invocation(invocation()))
    with pytest.raises(PermissionPortalError, match="tenant-safe"):
        proxy.clear()
    with pytest.raises(PermissionPortalError, match="tenant-safe"):
        await proxy.aclear()


class FakeVectorStore:
    def __init__(self) -> None:
        self.documents = None
        self.search = None

    def add_documents(self, documents, **kwargs):
        self.documents = (documents, kwargs)
        return ["id"]

    async def aadd_documents(self, documents, **kwargs):
        self.documents = (documents, kwargs)
        return ["async-id"]

    def similarity_search(self, query, **kwargs):
        self.search = (query, kwargs)
        return ["result"]

    async def asimilarity_search(self, query, **kwargs):
        self.search = (query, kwargs)
        return ["async-result"]


@pytest.mark.asyncio
async def test_vector_proxy_partitions_documents_and_searches() -> None:
    vector = FakeVectorStore()
    scope = TenantScope.from_invocation(invocation())
    proxy = TenantVectorStoreProxy(vector, scope)
    document = SimpleNamespace(page_content="text", metadata={"source": "test"})

    assert proxy.add_documents([document]) == ["id"]
    assert document.metadata == {"source": "test"}
    assert vector.documents[0][0].metadata[TENANT_FIELD] == scope.partition
    assert await proxy.aadd_documents([document]) == ["async-id"]
    assert proxy.similarity_search("query", pre_filter={"kind": "memo"}) == ["result"]
    assert vector.search[1]["pre_filter"]["$and"][0] == {TENANT_FIELD: scope.partition}
    assert await proxy.asimilarity_search("query") == ["async-result"]


class FakeConnectors:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.cache_value = FakeCache()
        self.vector_value = FakeVectorStore()

    def chat_message_history(self, session_id, **kwargs):
        self.calls.append(("history", (session_id, kwargs)))
        return session_id

    def cache(self, **kwargs):
        self.calls.append(("cache", kwargs))
        return self.cache_value

    def semantic_cache(self, **kwargs):
        self.calls.append(("semantic", kwargs))
        return self.cache_value

    def vector_search(self, **kwargs):
        self.calls.append(("vector", kwargs))
        return self.vector_value

    def loader(self, **kwargs):
        self.calls.append(("loader", kwargs))
        return kwargs


def test_mongodb_facade_scopes_connector_identifiers_and_filters() -> None:
    connectors = FakeConnectors()
    scope = TenantScope.from_invocation(invocation())
    facade = TenantMongoDBConnectors(connectors, scope)

    assert scope.subject_partition in facade.chat_message_history("session")
    assert isinstance(facade.cache(collection="cache"), TenantCacheProxy)
    assert isinstance(facade.semantic_cache("embedding"), TenantCacheProxy)
    assert isinstance(facade.vector_search("embedding"), TenantVectorStoreProxy)
    loader = facade.loader(filter_criteria={"kind": "memo"})
    assert loader["filter_criteria"]["$and"][0] == {TENANT_FIELD: scope.partition}


class FakeBroker:
    async def credential_for(self, identity, audience):
        return f"credential:{identity.tenant_id}:{audience}"


@pytest.mark.asyncio
async def test_namespace_context_exposes_only_invocation_bound_facades() -> None:
    connectors = FakeConnectors()
    context = create_namespace_test_context(
        dependencies=NamespaceDependencies(
            clients=ClientFactories({"langchain_mongodb": lambda: connectors})
        )
    )
    context = replace(
        context,
        credentials=FakeBroker(),
        egress=EgressPolicy(frozenset({"api.example.com"})),
    )

    with invocation_scope(invocation()):
        assert context.tenant_scope().tenant_id == "tenant-a"
        assert isinstance(context.tenant_tasks(), TenantTaskService)
        assert isinstance(context.tenant_sql(), TenantSQLExecutor)
        assert isinstance(context.mongodb(), TenantMongoDBConnectors)
        assert context.outbound_url("https://api.example.com") == "https://api.example.com"
        credential = await context.downstream_credential("https://api.example.com")
    assert credential == "credential:tenant-a:https://api.example.com"


@pytest.mark.asyncio
async def test_policy_rejects_spoofed_tenant_arguments_and_missing_claims() -> None:
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="static", static_token="token"),
        enterprise=EnterpriseSettings(require_tenant=True),
    )
    child = FastMCP("tenant")

    @child.tool(meta={"tags": ["readonly"]})
    def read(tenant_id: str) -> str:
        """Read tenant data."""
        return tenant_id

    tool = child._tool_manager.get_tool("read")
    assert tool is not None
    engine = ScopePolicyEngine(settings)
    spoofed = await engine.authorize(invocation(), tool, {"tenant_id": "tenant-b"})
    missing = await engine.authorize(invocation(None), tool, {})
    assert spoofed.allowed is False
    assert "verified invocation context" in spoofed.reason
    assert missing.allowed is False
    assert missing.reason == "verified tenant claim is required"


@pytest.mark.asyncio
async def test_cross_tenant_override_requires_explicit_admin_scope() -> None:
    settings = replace(
        create_test_settings(),
        auth=AuthSettings(provider="static", static_token="token"),
        enterprise=EnterpriseSettings(require_tenant=True),
    )
    child = FastMCP("tenant-admin")

    @child.tool(meta={"tags": ["tenant_override"]})
    def inspect_tenant(tenant_id: str) -> str:
        """Inspect another tenant as an administrator."""
        return tenant_id

    tool = child._tool_manager.get_tool("inspect_tenant")
    assert tool is not None
    engine = ScopePolicyEngine(settings)
    denied = await engine.authorize(invocation(), tool, {"tenant_id": "tenant-b"})
    admin_invocation = replace(
        invocation(),
        identity=replace(invocation().identity, scopes=frozenset({"tenant.admin"})),
    )
    allowed = await engine.authorize(admin_invocation, tool, {"tenant_id": "tenant-b"})
    assert denied.allowed is False
    assert denied.required_scopes == frozenset({"tenant.admin"})
    assert allowed.allowed is True


def test_production_validation_requires_auth_for_tenant_isolation() -> None:
    settings = replace(create_test_settings(), enterprise=EnterpriseSettings(require_tenant=True))
    with pytest.raises(PermissionPortalError), invocation_scope(invocation(None)):
        create_namespace_test_context(settings=settings).tenant_scope()
    with pytest.raises(ConfigurationPortalError, match="unsafe"):
        settings.validate_production()
