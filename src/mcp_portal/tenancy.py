"""Partition tasks, storage, and downstream clients by tenant identity."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import warnings
from dataclasses import dataclass, field
from typing import Any

from langchain_core.load.dump import dumps
from langchain_core.load.load import loads
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import (
    ChatGeneration,
    ChatGenerationChunk,
    Generation,
    GenerationChunk,
)

from mcp_portal.errors import PermissionPortalError, ValidationPortalError
from mcp_portal.security import InvocationContext
from mcp_portal.tasks import PortalTask, TaskStatus, TaskStore

TENANT_FIELD = "_portal_tenant"
AUTHORIZATION_FIELD = "_portal_authorization"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TenantScope:
    """Trusted partition derived only from verified invocation identity.

    Attributes:
        tenant_id: Verified external tenant identifier.
        subject: Verified human or workload subject.
        client_id: Verified calling client identifier.
        partition: Non-reversible stable storage partition token.
        subject_partition: Non-reversible tenant-and-subject partition token.
        authorization_partition: Non-reversible partition for the complete verified
            authorization context and current tool.
    """

    tenant_id: str | None = field(metadata={"description": "Verified external tenant identifier."})
    subject: str | None = field(metadata={"description": "Verified human or workload subject."})
    client_id: str | None = field(metadata={"description": "Verified calling client identifier."})
    partition: str = field(
        metadata={"description": "Non-reversible stable storage partition token."}
    )
    subject_partition: str = field(
        metadata={"description": "Non-reversible tenant-and-subject partition token."}
    )
    authorization_partition: str = field(
        metadata={"description": "Non-reversible authorization-context partition token."}
    )

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
        authorization_seed = json.dumps(
            {
                "auth_method": identity.auth_method,
                "client_id": identity.client_id,
                "linux_groups": sorted(identity.linux_groups),
                "scopes": sorted(identity.scopes),
                "subject": identity.subject,
                "tenant_id": tenant_seed,
                "tool_name": invocation.tool_name,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return cls(
            tenant_id=identity.tenant_id,
            subject=identity.subject,
            client_id=identity.client_id,
            partition=_partition_token("tenant", tenant_seed),
            subject_partition=_partition_token(
                "subject", f"{tenant_seed}\0{identity.subject or identity.client_id or 'anonymous'}"
            ),
            authorization_partition=_partition_token("authorization", authorization_seed),
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
        return f"{self.subject_partition if subject_scoped else self.partition}:{normalized}"

    def semantic_cache_partition(self, policy_version: str) -> str:
        """Return a cache partition bound to authorization and policy versions.

        Args:
            policy_version: Namespace-owned version changed whenever cache-relevant
                authorization or source-data semantics change.

        Returns:
            Stable opaque semantic-cache partition.
        """
        normalized = policy_version.strip()
        if not normalized or len(normalized) > 128:
            raise ValidationPortalError(
                "Semantic-cache policy versions must contain 1 to 128 characters."
            )
        return _partition_token("semantic-cache", f"{self.authorization_partition}\0{normalized}")

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

    def __init__(self, store: TaskStore, scope: TenantScope) -> None:
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


class IsolatedSemanticCacheProxy:
    """Enforce backend semantic-cache isolation for one authorization context.

    Prompt prefixes are not a security boundary for similarity search. This proxy
    writes opaque tenant and authorization partitions as metadata and injects both
    fields into every backend vector-search and deletion predicate.
    """

    def __init__(self, cache: Any, scope: TenantScope, *, policy_version: str) -> None:
        """Bind a semantic cache to an authenticated authorization partition.

        Args:
            cache: MongoDB Atlas semantic-cache/vector-search implementation.
            scope: Trusted invocation scope.
            policy_version: Namespace-owned cache authorization policy version.
        """
        _ = scope.owner
        self.cache = cache
        self.scope = scope
        self.partition = scope.semantic_cache_partition(policy_version)
        self.llm_field = str(getattr(cache, "LLM", "llm_string"))
        self.return_value_field = str(getattr(cache, "RETURN_VAL", "return_val"))

    def lookup(self, prompt: str, llm_string: str) -> Any:
        """Look up a generation inside the current authorization partition.

        Args:
            prompt: Model prompt used for similarity search.
            llm_string: Serialized model configuration key.

        Returns:
            Cached generations, or `None` for a cache miss.
        """
        search = self._backend_method("similarity_search_with_score")
        response = search(prompt, 1, **self._search_kwargs(llm_string))
        return self._response_value(response)

    def update(self, prompt: str, llm_string: str, return_val: Any) -> None:
        """Write a generation with trusted isolation metadata.

        Args:
            prompt: Model prompt to embed and store.
            llm_string: Serialized model configuration key.
            return_val: LangChain generations to cache.
        """
        add_texts = self._backend_method("add_texts")
        add_texts([prompt], [self._metadata(llm_string, return_val)])

    def clear(self, **kwargs: Any) -> Any:
        """Delete only entries in the current authorization partition.

        Args:
            kwargs: Optional backend deletion criteria.

        Returns:
            Backend deletion result.
        """
        self._reject_reserved_criteria(kwargs)
        collection = getattr(self.cache, "collection", None)
        delete_many = getattr(collection, "delete_many", None)
        if not callable(delete_many):
            raise PermissionPortalError(
                "Semantic cache backend does not support authorization-safe clearing."
            )
        return delete_many(self._isolation_filter(kwargs))

    async def alookup(self, prompt: str, llm_string: str) -> Any:
        """Asynchronously look up a generation in the authorization partition.

        Args:
            prompt: Model prompt used for similarity search.
            llm_string: Serialized model configuration key.

        Returns:
            Cached generations, or `None` for a cache miss.
        """
        search = self._backend_method("asimilarity_search_with_score")
        response = await search(prompt, 1, **self._search_kwargs(llm_string))
        return self._response_value(response)

    async def aupdate(self, prompt: str, llm_string: str, return_val: Any) -> None:
        """Asynchronously write a generation with trusted isolation metadata.

        Args:
            prompt: Model prompt to embed and store.
            llm_string: Serialized model configuration key.
            return_val: LangChain generations to cache.
        """
        add_texts = self._backend_method("aadd_texts")
        await add_texts([prompt], metadatas=[self._metadata(llm_string, return_val)])

    async def aclear(self, **kwargs: Any) -> Any:
        """Asynchronously delete entries in the authorization partition.

        Args:
            kwargs: Optional backend deletion criteria.

        Returns:
            Backend deletion result.
        """
        return await asyncio.to_thread(self.clear, **kwargs)

    def _metadata(self, llm_string: str, return_val: Any) -> dict[str, Any]:
        """Build cache metadata from trusted portal state.

        Args:
            llm_string: Serialized model configuration key.
            return_val: LangChain generations to serialize.

        Returns:
            Metadata containing result and isolation fields.
        """
        return {
            self.llm_field: llm_string,
            self.return_value_field: _dumps_generations(return_val),
            TENANT_FIELD: self.scope.partition,
            AUTHORIZATION_FIELD: self.partition,
        }

    def _search_kwargs(self, llm_string: str) -> dict[str, Any]:
        """Build a mandatory Atlas pre-filter and preserve score thresholds.

        Args:
            llm_string: Serialized model configuration key.

        Returns:
            Keyword arguments for the backend vector search.
        """
        selected: dict[str, Any] = {
            "pre_filter": self._isolation_filter({self.llm_field: {"$eq": llm_string}})
        }
        threshold = getattr(self.cache, "score_threshold", None)
        if threshold is not None:
            selected["post_filter_pipeline"] = [{"$match": {"score": {"$gte": threshold}}}]
        return selected

    def _isolation_filter(self, criteria: dict[str, Any] | None = None) -> dict[str, Any]:
        """Combine caller criteria with immutable isolation filters.

        Args:
            criteria: Optional additional backend criteria.

        Returns:
            Backend predicate containing immutable portal constraints.
        """
        constraints: list[dict[str, Any]] = [
            {TENANT_FIELD: {"$eq": self.scope.partition}},
            {AUTHORIZATION_FIELD: {"$eq": self.partition}},
        ]
        if criteria:
            constraints.append(dict(criteria))
        return {"$and": constraints}

    def _response_value(self, response: Any) -> Any:
        """Deserialize a cache hit, failing closed on malformed backend data.

        Args:
            response: Backend similarity-search response.

        Returns:
            Deserialized generations, or `None` for invalid data or a miss.
        """
        if not response:
            return None
        document = response[0][0]
        metadata = getattr(document, "metadata", {})
        serialized = metadata.get(self.return_value_field)
        if not isinstance(serialized, str):
            return None
        return _loads_generations(serialized)

    def _backend_method(self, name: str) -> Any:
        """Return a required backend capability or fail closed.

        Args:
            name: Backend method name.

        Returns:
            Callable backend method.
        """
        method = getattr(self.cache, name, None)
        if not callable(method):
            raise PermissionPortalError(
                "Semantic cache backend cannot enforce authorization isolation.",
                details={"missing_capability": name},
            )
        return method

    @staticmethod
    def _reject_reserved_criteria(criteria: dict[str, Any]) -> None:
        """Prevent namespaces from supplying portal-owned isolation fields.

        Args:
            criteria: Namespace-provided deletion criteria.
        """
        reserved = {TENANT_FIELD, AUTHORIZATION_FIELD}
        if reserved & criteria.keys():
            raise ValidationPortalError(
                "Semantic-cache isolation fields are reserved for the portal."
            )


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

    def semantic_cache(
        self, embedding: Any, *, policy_version: str, **kwargs: Any
    ) -> IsolatedSemanticCacheProxy:
        """Create an authorization-partitioned semantic cache.

        Args:
            embedding: LangChain embeddings implementation.
            policy_version: Namespace-owned version changed whenever authorization
                or source-data semantics change.
            kwargs: Connector-specific options.

        Returns:
            Cache proxy that enforces backend metadata filters.
        """
        return self.connectors.semantic_cache(
            embedding=embedding,
            scope=self.scope,
            policy_version=policy_version,
            **kwargs,
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
        return self.connectors.loader(
            filter_criteria=self.scope.mongo_filter(kwargs.pop("filter_criteria", None)), **kwargs
        )


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


def _dumps_generations(generations: Any) -> str:
    """Serialize LangChain generations without backend-private helpers.

    Args:
        generations: LangChain generations to serialize.

    Returns:
        JSON-encoded LangChain serialization payload.
    """
    return json.dumps([dumps(item) for item in generations])


def _loads_generations(generations: str) -> Any:
    """Deserialize cache data and treat invalid entries as misses.

    Args:
        generations: JSON-encoded LangChain generation payload.

    Returns:
        Deserialized generations, or `None` for malformed or disallowed data.
    """
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="The function `loads` is in beta.*")
            return [
                loads(
                    item,
                    allowed_objects=[
                        Generation,
                        GenerationChunk,
                        ChatGeneration,
                        ChatGenerationChunk,
                        AIMessage,
                        AIMessageChunk,
                    ],
                )
                for item in json.loads(generations)
            ]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    try:
        return [Generation(**item) for item in json.loads(generations)]
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Ignoring malformed semantic-cache generation data")
        return None
