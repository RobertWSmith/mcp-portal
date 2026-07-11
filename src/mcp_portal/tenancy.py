from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any

from mcp_portal.errors import PermissionPortalError, ValidationPortalError
from mcp_portal.security import InvocationContext
from mcp_portal.tasks import MemoryTaskStore, PortalTask, TaskStatus

TENANT_FIELD = "_portal_tenant"


@dataclass(frozen=True)
class TenantScope:
    """Trusted partition derived only from verified invocation identity.

    Attributes:
        tenant_id: Verified external tenant identifier.
        subject: Verified human or workload subject.
        client_id: Verified calling client identifier.
        partition: Non-reversible stable storage partition token.
        subject_partition: Non-reversible tenant-and-subject partition token.
    """

    tenant_id: str | None
    subject: str | None
    client_id: str | None
    partition: str
    subject_partition: str

    @classmethod
    def from_invocation(
        cls, invocation: InvocationContext, *, require_tenant: bool = False
    ) -> "TenantScope":
        """Create a scope from trusted invocation identity.

        Args:
            invocation: Current trusted invocation context.
            require_tenant: Whether a missing verified tenant must be rejected.

        Returns:
            Stable tenant and subject partition helpers.
        """
        identity = invocation.identity
        if require_tenant and not identity.tenant_id:
            raise PermissionPortalError("A verified tenant claim is required.")
        tenant_seed = identity.tenant_id or "single-tenant"
        actor_seed = identity.subject or identity.client_id or "anonymous"
        return cls(
            tenant_id=identity.tenant_id,
            subject=identity.subject,
            client_id=identity.client_id,
            partition=_partition_token("tenant", tenant_seed),
            subject_partition=_partition_token("subject", f"{tenant_seed}\0{actor_seed}"),
        )

    @property
    def owner(self) -> str:
        """Return the verified task owner.

        Returns:
            Subject or client identifier suitable for ownership checks.
        """
        owner = self.subject or self.client_id
        if not owner:
            raise PermissionPortalError("An authenticated subject or client is required.")
        return owner

    def key(self, value: str, *, subject_scoped: bool = False) -> str:
        """Prefix an application key with a trusted storage partition.

        Args:
            value: Namespace-controlled logical key.
            subject_scoped: Whether the key is private to the current actor.

        Returns:
            Partitioned opaque key.
        """
        normalized = value.strip()
        if not normalized or len(normalized) > 512:
            raise ValidationPortalError("Tenant-scoped keys must contain 1 to 512 characters.")
        prefix = self.subject_partition if subject_scoped else self.partition
        return f"{prefix}:{normalized}"

    def mongo_filter(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        """Combine a MongoDB query with the trusted tenant partition.

        Args:
            query: Optional namespace-defined query.

        Returns:
            Query constrained to the current tenant.
        """
        selected = dict(query or {})
        if TENANT_FIELD in selected:
            raise ValidationPortalError(f"{TENANT_FIELD} is reserved for the portal.")
        if not selected:
            return {TENANT_FIELD: self.partition}
        return {"$and": [{TENANT_FIELD: self.partition}, selected]}

    def document_metadata(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Attach trusted tenant metadata to a stored document.

        Args:
            metadata: Optional application document metadata.

        Returns:
            Copied metadata containing the trusted tenant partition.
        """
        selected = dict(metadata or {})
        if TENANT_FIELD in selected:
            raise ValidationPortalError(f"{TENANT_FIELD} is reserved for the portal.")
        selected[TENANT_FIELD] = self.partition
        return selected

    def sql_parameters(self, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Attach a trusted tenant bind parameter for SQLAlchemy statements.

        Args:
            parameters: Optional application bind parameters.

        Returns:
            Copied parameters containing ``portal_tenant``.
        """
        selected = dict(parameters or {})
        if "portal_tenant" in selected:
            raise ValidationPortalError("portal_tenant is reserved for the portal.")
        selected["portal_tenant"] = self.partition
        return selected


class TenantTaskService:
    """Task façade that never accepts caller-supplied owner or tenant identifiers."""

    def __init__(self, store: MemoryTaskStore, scope: TenantScope) -> None:
        """Bind a task store to one trusted tenant scope.

        Args:
            store: Shared authorization-aware task store.
            scope: Trusted invocation tenant scope.
        """
        self.store = store
        self.scope = scope

    def create(self, ttl_seconds: int) -> PortalTask:
        """Create a task for the current verified owner and tenant.

        Args:
            ttl_seconds: Requested retention duration.

        Returns:
            Newly created task.
        """
        return self.store.create(self.scope.owner, self.scope.tenant_id, ttl_seconds)

    def get(self, task_id: str) -> PortalTask:
        """Retrieve a task visible to the current verified owner and tenant.

        Args:
            task_id: Opaque task identifier.

        Returns:
            Matching task.
        """
        return self.store.get(task_id, self.scope.owner, self.scope.tenant_id)

    def update(self, task_id: str, *, status: TaskStatus, result: Any = None) -> PortalTask:
        """Update a task visible to the current verified owner and tenant.

        Args:
            task_id: Opaque task identifier.
            status: New lifecycle state.
            result: Optional task result.

        Returns:
            Updated task.
        """
        return self.store.update(
            task_id,
            self.scope.owner,
            self.scope.tenant_id,
            status=status,
            result=result,
        )

    def list(self) -> tuple[PortalTask, ...]:
        """List tasks visible to the current verified owner and tenant.

        Returns:
            Authorization-filtered tasks.
        """
        return self.store.list(self.scope.owner, self.scope.tenant_id)


class TenantSQLExecutor:
    """Require SQLAlchemy statements to bind the trusted tenant partition."""

    def __init__(self, scope: TenantScope) -> None:
        """Bind SQL execution checks to a trusted tenant scope.

        Args:
            scope: Trusted tenant scope.
        """
        self.scope = scope

    def execute(
        self,
        connection: Any,
        statement: Any,
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a statement only when it declares a tenant bind parameter.

        Args:
            connection: SQLAlchemy connection or session.
            statement: SQLAlchemy statement containing ``:portal_tenant``.
            parameters: Optional application bind parameters.

        Returns:
            Result returned by the SQLAlchemy connection or session.
        """
        if ":portal_tenant" not in str(statement):
            raise PermissionPortalError(
                "Tenant SQL statements must bind :portal_tenant explicitly."
            )
        return connection.execute(statement, self.scope.sql_parameters(parameters))


class TenantCacheProxy:
    """Partition LangChain-compatible cache keys by trusted tenant scope."""

    def __init__(self, cache: Any, scope: TenantScope) -> None:
        """Bind a cache to one trusted tenant partition.

        Args:
            cache: LangChain-compatible cache implementation.
            scope: Trusted tenant scope.
        """
        self.cache = cache
        self.scope = scope

    def lookup(self, prompt: str, llm_string: str) -> Any:
        """Look up a tenant-partitioned cache entry.

        Args:
            prompt: Model prompt.
            llm_string: Model configuration key.

        Returns:
            Cached generations or None.
        """
        return self.cache.lookup(self.scope.key(prompt), llm_string)

    def update(self, prompt: str, llm_string: str, return_val: Any) -> Any:
        """Update a tenant-partitioned cache entry.

        Args:
            prompt: Model prompt.
            llm_string: Model configuration key.
            return_val: Generations to cache.

        Returns:
            Result returned by the underlying cache.
        """
        return self.cache.update(self.scope.key(prompt), llm_string, return_val)

    def clear(self, **kwargs: Any) -> Any:
        """Clear only entries in the current tenant partition.

        Args:
            kwargs: Cache-specific clear options.

        Returns:
            Result returned by a partition-aware cache implementation.
        """
        clear_partition = getattr(self.cache, "clear_partition", None)
        if clear_partition is None:
            raise PermissionPortalError("Cache backend does not support tenant-safe clearing.")
        return clear_partition(self.scope.partition, **kwargs)

    async def alookup(self, prompt: str, llm_string: str) -> Any:
        """Asynchronously look up a tenant-partitioned cache entry.

        Args:
            prompt: Model prompt.
            llm_string: Model configuration key.

        Returns:
            Cached generations or None.
        """
        return await self.cache.alookup(self.scope.key(prompt), llm_string)

    async def aupdate(self, prompt: str, llm_string: str, return_val: Any) -> Any:
        """Asynchronously update a tenant-partitioned cache entry.

        Args:
            prompt: Model prompt.
            llm_string: Model configuration key.
            return_val: Generations to cache.

        Returns:
            Result returned by the underlying cache.
        """
        return await self.cache.aupdate(self.scope.key(prompt), llm_string, return_val)

    async def aclear(self, **kwargs: Any) -> Any:
        """Asynchronously clear only entries in the tenant partition.

        Args:
            kwargs: Cache-specific clear options.

        Returns:
            Result returned by a partition-aware cache implementation.
        """
        clear_partition = getattr(self.cache, "aclear_partition", None)
        if clear_partition is None:
            raise PermissionPortalError("Cache backend does not support tenant-safe clearing.")
        return await clear_partition(self.scope.partition, **kwargs)


class TenantVectorStoreProxy:
    """Constrain common vector-store writes and searches to one tenant partition."""

    def __init__(self, vector_store: Any, scope: TenantScope) -> None:
        """Bind a vector store to one trusted tenant partition.

        Args:
            vector_store: LangChain-compatible vector store.
            scope: Trusted tenant scope.
        """
        self.vector_store = vector_store
        self.scope = scope

    def add_documents(self, documents: list[Any], **kwargs: Any) -> Any:
        """Add copied documents with trusted tenant metadata.

        Args:
            documents: LangChain-compatible documents.
            kwargs: Vector-store-specific write options.

        Returns:
            Result returned by the underlying vector store.
        """
        return self.vector_store.add_documents(self._documents(documents), **kwargs)

    async def aadd_documents(self, documents: list[Any], **kwargs: Any) -> Any:
        """Asynchronously add copied documents with trusted tenant metadata.

        Args:
            documents: LangChain-compatible documents.
            kwargs: Vector-store-specific write options.

        Returns:
            Result returned by the underlying vector store.
        """
        return await self.vector_store.aadd_documents(self._documents(documents), **kwargs)

    def similarity_search(self, query: str, **kwargs: Any) -> Any:
        """Search only vectors in the current tenant partition.

        Args:
            query: Similarity search text.
            kwargs: Vector-store-specific search options.

        Returns:
            Tenant-filtered search results.
        """
        return self.vector_store.similarity_search(query, **self._search_kwargs(kwargs))

    async def asimilarity_search(self, query: str, **kwargs: Any) -> Any:
        """Asynchronously search only vectors in the tenant partition.

        Args:
            query: Similarity search text.
            kwargs: Vector-store-specific search options.

        Returns:
            Tenant-filtered search results.
        """
        return await self.vector_store.asimilarity_search(query, **self._search_kwargs(kwargs))

    def _documents(self, documents: list[Any]) -> list[Any]:
        """Copy documents and attach trusted tenant metadata.

        Args:
            documents: Source documents.

        Returns:
            Copied and partitioned documents.
        """
        selected: list[Any] = []
        for document in documents:
            item = copy.copy(document)
            item.metadata = self.scope.document_metadata(getattr(document, "metadata", None))
            selected.append(item)
        return selected

    def _search_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Attach a trusted pre-filter to search options.

        Args:
            kwargs: Caller-provided search options.

        Returns:
            Copied options constrained to the tenant partition.
        """
        selected = dict(kwargs)
        existing = selected.pop("pre_filter", None)
        selected["pre_filter"] = self.scope.mongo_filter(existing)
        return selected


class TenantMongoDBConnectors:
    """Tenant-safe façade over configured LangChain MongoDB connectors."""

    def __init__(self, connectors: Any, scope: TenantScope) -> None:
        """Bind connector factories to one trusted tenant partition.

        Args:
            connectors: Configured ``MongoDBConnectors`` instance.
            scope: Trusted tenant scope.
        """
        self.connectors = connectors
        self.scope = scope

    def chat_message_history(self, session_id: str, **kwargs: Any) -> Any:
        """Create actor-private chat history with a trusted scoped session ID.

        Args:
            session_id: Namespace-controlled logical session identifier.
            kwargs: Connector-specific options.

        Returns:
            MongoDB chat history instance.
        """
        return self.connectors.chat_message_history(
            self.scope.key(session_id, subject_scoped=True), **kwargs
        )

    def cache(self, **kwargs: Any) -> TenantCacheProxy:
        """Create a tenant-partitioned exact cache.

        Args:
            kwargs: Connector-specific options.

        Returns:
            Cache proxy that prefixes every prompt key.
        """
        return TenantCacheProxy(self.connectors.cache(**kwargs), self.scope)

    def semantic_cache(self, embedding: Any, **kwargs: Any) -> TenantCacheProxy:
        """Create a tenant-partitioned semantic cache.

        Args:
            embedding: LangChain embeddings implementation.
            kwargs: Connector-specific options.

        Returns:
            Cache proxy that prefixes every prompt key.
        """
        return TenantCacheProxy(
            self.connectors.semantic_cache(embedding=embedding, **kwargs), self.scope
        )

    def vector_search(self, embedding: Any, **kwargs: Any) -> TenantVectorStoreProxy:
        """Create a tenant-constrained vector store façade.

        Args:
            embedding: LangChain embeddings implementation.
            kwargs: Connector-specific options.

        Returns:
            Vector-store proxy that partitions writes and searches.
        """
        return TenantVectorStoreProxy(
            self.connectors.vector_search(embedding=embedding, **kwargs), self.scope
        )

    def loader(self, **kwargs: Any) -> Any:
        """Create a document loader constrained by a trusted tenant query.

        Args:
            kwargs: Connector-specific loader options.

        Returns:
            Configured MongoDB loader.
        """
        existing = kwargs.pop("filter_criteria", None)
        return self.connectors.loader(filter_criteria=self.scope.mongo_filter(existing), **kwargs)


def _partition_token(kind: str, value: str) -> str:
    """Create a stable non-reversible storage partition token.

    Args:
        kind: Partition domain separator.
        value: Verified identity value.

    Returns:
        Short SHA-256 partition token.
    """
    digest = hashlib.sha256(f"mcp-portal:{kind}\0{value}".encode()).hexdigest()
    return f"p_{digest[:24]}"
