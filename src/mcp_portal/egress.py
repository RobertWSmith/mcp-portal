"""Enforce destination- and data-aware policy for downstream requests."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol
from urllib.parse import SplitResult, urlsplit

from mcp_portal.errors import PermissionPortalError, ValidationPortalError
from mcp_portal.redaction import Redactor
from mcp_portal.security import InvocationContext

DataClassification = Literal["public", "internal", "confidential", "restricted"]
SensitiveFieldAction = Literal["block", "redact"]

DATA_CLASSIFICATIONS: tuple[DataClassification, ...] = (
    "public",
    "internal",
    "confidential",
    "restricted",
)
_CLASSIFICATION_LEVEL = {
    classification: level for level, classification in enumerate(DATA_CLASSIFICATIONS)
}
_ALLOWED_METHODS = frozenset({"DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"})
_PURPOSE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CONFIDENTIAL_KEYS = frozenset(
    {
        "account_number",
        "bank_account",
        "card_number",
        "credit_card",
        "date_of_birth",
        "dob",
        "email",
        "email_address",
        "national_id",
        "personal_email",
        "routing_number",
        "social_security_number",
        "ssn",
        "tax_id",
    }
)


@dataclass(frozen=True)
class EgressRequest:
    """Namespace-declared outbound request awaiting trusted policy evaluation.

    Attributes:
        destination: Exact HTTPS URL receiving the request.
        purpose: Stable low-cardinality purpose identifier for policy and audit.
        method: Intended HTTP operation.
        payload: Structured outbound payload inspected before execution.
        data_classification: Optional caller-declared classification that may only
            raise the namespace-owned minimum.
        credential_audience: Optional HTTPS audience for downstream token exchange.
    """

    destination: str
    purpose: str
    method: str = "POST"
    payload: Any = field(default=None, repr=False)
    data_classification: DataClassification | None = None
    credential_audience: str | None = None


@dataclass(frozen=True)
class PayloadInspection:
    """Result of inspecting and sanitizing one structured outbound payload.

    Attributes:
        payload: Copy with detected sensitive values redacted.
        detected_classification: Highest classification found before redaction.
        findings: Stable labels describing detected sensitive data types.
        payload_digest: SHA-256 digest of the original canonicalized payload.
    """

    payload: Any = field(repr=False)
    detected_classification: DataClassification
    findings: tuple[str, ...]
    payload_digest: str


class PayloadInspector(Protocol):
    """Deployment extension point for structured DLP inspection."""

    def inspect(
        self, payload: Any, minimum_classification: DataClassification
    ) -> PayloadInspection:
        """Inspect an outbound payload without retaining sensitive values.

        Args:
            payload: Structured payload proposed for egress.
            minimum_classification: Trusted lower bound from namespace policy.

        Returns:
            Sanitized payload and data findings.
        """
        ...


@dataclass(frozen=True)
class StructuredPayloadInspector:
    """Detect common credentials and personal-data fields in structured payloads.

    This conservative built-in inspector is a safe baseline, not a replacement for
    an organization's DLP service. Opaque payload objects and binary content are
    classified as restricted because their contents cannot be inspected safely.

    Attributes:
        redactor: Redactor containing deployment-known literal secrets.
    """

    redactor: Redactor = field(default_factory=Redactor)

    def inspect(
        self, payload: Any, minimum_classification: DataClassification
    ) -> PayloadInspection:
        """Inspect a payload and redact every detected sensitive value.

        Args:
            payload: Structured payload proposed for egress.
            minimum_classification: Trusted lower classification bound.

        Returns:
            Sanitized payload with stable finding labels and a payload digest.
        """
        classification = _validate_classification(minimum_classification)
        findings: dict[str, DataClassification] = {}
        sanitized = self._sanitize(payload, findings)
        for finding_classification in findings.values():
            classification = _max_classification(classification, finding_classification)
        return PayloadInspection(
            payload=sanitized,
            detected_classification=classification,
            findings=tuple(sorted(findings)),
            payload_digest=_payload_digest(payload),
        )

    def _sanitize(self, value: Any, findings: dict[str, DataClassification]) -> Any:
        """Recursively copy a value while redacting sensitive content.

        Args:
            value: Current payload value.
            findings: Mutable finding accumulator containing labels only.

        Returns:
            Sanitized copy safe to pass to an approved operation.
        """
        if isinstance(value, Mapping):
            selected: dict[Any, Any] = {}
            for key, child in value.items():
                key_classification = self._key_classification(str(key))
                if key_classification is None:
                    selected[key] = self._sanitize(child, findings)
                    continue
                label, classification = key_classification
                findings[label] = classification
                selected[key] = self.redactor.replacement
            return selected
        if isinstance(value, list):
            return [self._sanitize(child, findings) for child in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize(child, findings) for child in value)
        if isinstance(value, set):
            return [self._sanitize(child, findings) for child in sorted(value, key=repr)]
        if isinstance(value, str):
            return self._sanitize_text(value, findings)
        if isinstance(value, bytes):
            findings["binary_payload"] = "restricted"
            return self.redactor.replacement
        if value is None or isinstance(value, bool | int | float):
            return value
        findings["opaque_payload"] = "restricted"
        return self.redactor.replacement

    def _key_classification(self, key: str) -> tuple[str, DataClassification] | None:
        """Classify a structured field name without inspecting its value.

        Args:
            key: Mapping key proposed for egress.

        Returns:
            Finding label and classification, or `None` for an ordinary field.
        """
        normalized = key.lower().replace("-", "_").replace(" ", "_")
        redacted_probe = self.redactor.redact({key: "probe"})
        if redacted_probe.get(key) == self.redactor.replacement:
            return "credential_field", "restricted"
        if normalized in _CONFIDENTIAL_KEYS:
            label = (
                "financial_identifier"
                if any(marker in normalized for marker in ("account", "card", "routing", "tax"))
                else "personal_identifier"
            )
            return label, "confidential"
        return None

    def _sanitize_text(self, value: str, findings: dict[str, DataClassification]) -> str:
        """Redact known secrets and sensitive text patterns.

        Args:
            value: Text payload value.
            findings: Mutable finding accumulator containing labels only.

        Returns:
            Text with recognized sensitive substrings removed.
        """
        selected = self.redactor.redact(value)
        if selected != value:
            findings["known_secret"] = "restricted"
        patterns: tuple[tuple[re.Pattern[str], str, DataClassification], ...] = (
            (_BEARER_PATTERN, "bearer_token", "restricted"),
            (_PRIVATE_KEY_PATTERN, "private_key", "restricted"),
            (_SSN_PATTERN, "personal_identifier", "confidential"),
            (_EMAIL_PATTERN, "email_address", "confidential"),
        )
        for pattern, label, classification in patterns:
            if pattern.search(selected):
                findings[label] = classification
                selected = pattern.sub(self.redactor.replacement, selected)
        return selected


@dataclass(frozen=True)
class EgressDecision:
    """Auditable result of evaluating destination and outbound data policy.

    Attributes:
        allowed: Whether downstream execution may proceed.
        reason: Stable policy decision reason.
        destination: Normalized HTTPS destination.
        host: Normalized destination hostname.
        method: Normalized HTTP operation.
        purpose: Validated low-cardinality purpose identifier.
        data_classification: Classification of the payload actually sent.
        detected_classification: Highest classification detected before redaction.
        destination_max_classification: Maximum classification allowed by the host.
        payload_digest: Digest of the original payload for audit correlation.
        findings: Stable sensitive-data labels containing no raw values.
        payload: Sanitized payload supplied only to an approved operation.
        credential_audience: Normalized same-origin credential audience.
    """

    allowed: bool
    reason: str
    destination: str
    host: str
    method: str
    purpose: str
    data_classification: DataClassification
    detected_classification: DataClassification
    destination_max_classification: DataClassification
    payload_digest: str
    findings: tuple[str, ...]
    payload: Any = field(repr=False)
    credential_audience: str | None = None


@dataclass(frozen=True)
class ApprovedEgress:
    """Sanitized downstream inputs released after an allow decision.

    Attributes:
        destination: Normalized HTTPS destination.
        method: Normalized HTTP operation.
        purpose: Validated outbound purpose.
        data_classification: Classification of the released payload.
        payload_digest: Digest of the original payload.
        payload: Sanitized structured payload.
        credential: Optional broker-issued same-origin credential.
    """

    destination: str
    method: str
    purpose: str
    data_classification: DataClassification
    payload_digest: str
    payload: Any = field(repr=False)
    credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class EgressPolicy:
    """Authorize outbound requests by destination and payload classification.

    Attributes:
        allowed_hosts: Exact DNS hostname allowlist.
        destination_classifications: Maximum data classification accepted per host.
        sensitive_field_action: Whether findings block the call or are redacted.
        allow_private_networks: Whether literal private IP destinations are permitted.
        inspector: Structured DLP adapter.
    """

    allowed_hosts: frozenset[str] = field(
        default=frozenset(), metadata={"description": "Exact DNS hostname allowlist."}
    )
    destination_classifications: Mapping[str, DataClassification] = field(
        default_factory=dict,
        metadata={"description": "Maximum outbound data classification per hostname."},
    )
    sensitive_field_action: SensitiveFieldAction = "block"
    allow_private_networks: bool = field(
        default=False,
        metadata={"description": "Whether literal private IP destinations are permitted."},
    )
    inspector: PayloadInspector = field(
        default_factory=StructuredPayloadInspector,
        metadata={"description": "Structured outbound payload inspector."},
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Normalize immutable destination policy and reject invalid configuration."""
        normalized_hosts = frozenset(_normalize_host(host) for host in self.allowed_hosts)
        classifications = {
            _normalize_host(host): _validate_classification(classification)
            for host, classification in self.destination_classifications.items()
        }
        if self.sensitive_field_action not in {"block", "redact"}:
            raise ValueError("Sensitive field action must be 'block' or 'redact'.")
        object.__setattr__(self, "allowed_hosts", normalized_hosts)
        object.__setattr__(self, "destination_classifications", MappingProxyType(classifications))

    def validate_url(self, url: str) -> str:
        """Validate an HTTPS URL against destination network boundaries.

        This compatibility helper validates only destination policy. Data-bearing
        operations must use :meth:`evaluate` through `NamespaceContext.downstream`.

        Args:
            url: Candidate outbound destination.

        Returns:
            Normalized approved URL.
        """
        destination, host, _ = self._normalize_url(url)
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise PermissionPortalError(
                "Outbound destination is not approved.", details={"host": host}
            )
        return destination

    def evaluate(
        self,
        invocation: InvocationContext,
        request: EgressRequest,
        *,
        minimum_classification: DataClassification,
    ) -> EgressDecision:
        """Evaluate identity, destination, payload, and credential-audience policy.

        Args:
            invocation: Trusted actor, client, tenant, and tool context.
            request: Namespace-declared outbound operation.
            minimum_classification: Namespace-owned classification lower bound.

        Returns:
            Auditable allow or deny decision containing only a sanitized payload.
        """
        method = request.method.strip().upper()
        if method not in _ALLOWED_METHODS:
            raise ValidationPortalError("Outbound HTTP method is not supported.")
        purpose = request.purpose.strip().lower()
        if not _PURPOSE_PATTERN.fullmatch(purpose):
            raise ValidationPortalError(
                "Outbound purpose must be a 1-64 character lowercase identifier."
            )
        declared = _validate_classification(minimum_classification)
        if request.data_classification is not None:
            declared = _max_classification(
                declared, _validate_classification(request.data_classification)
            )
        inspection = self.inspector.inspect(request.payload, declared)
        destination, host, parsed = self._normalize_url(request.destination)
        maximum = self.destination_classifications.get(host, "public")
        credential_audience = self._credential_audience(request, parsed)

        effective = (
            declared
            if self.sensitive_field_action == "redact"
            else inspection.detected_classification
        )
        decision = {
            "destination": destination,
            "host": host,
            "method": method,
            "purpose": purpose,
            "data_classification": effective,
            "detected_classification": inspection.detected_classification,
            "destination_max_classification": maximum,
            "payload_digest": inspection.payload_digest,
            "findings": inspection.findings,
            "payload": inspection.payload,
            "credential_audience": credential_audience,
        }
        identity = invocation.identity
        if identity.subject is None and identity.client_id is None:
            return EgressDecision(
                False, "verified identity is required for outbound data", **decision
            )
        if not self.allowed_hosts or host not in self.allowed_hosts:
            return EgressDecision(False, "outbound destination is not approved", **decision)
        if inspection.findings and self.sensitive_field_action == "block":
            return EgressDecision(
                False, "sensitive payload data is blocked by egress policy", **decision
            )
        if _CLASSIFICATION_LEVEL[effective] > _CLASSIFICATION_LEVEL[maximum]:
            return EgressDecision(
                False, "destination does not permit payload classification", **decision
            )
        return EgressDecision(True, "data-aware egress policy satisfied", **decision)

    def _normalize_url(self, url: str) -> tuple[str, str, SplitResult]:
        """Normalize and validate an absolute HTTPS URL.

        Args:
            url: Candidate outbound URL.

        Returns:
            Normalized URL, hostname, and parsed URL components.
        """
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ValidationPortalError("Outbound destination URL is invalid.") from exc
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValidationPortalError("Outbound destinations must be absolute HTTPS URLs.")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValidationPortalError(
                "Outbound destinations cannot contain credentials or fragments."
            )
        host = _normalize_host(parsed.hostname)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if (
            address
            and not self.allow_private_networks
            and (address.is_private or address.is_loopback or address.is_link_local)
        ):
            raise PermissionPortalError("Private or local outbound destinations are blocked.")
        rendered_host = f"[{host}]" if address and address.version == 6 else host
        netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
        normalized = parsed._replace(scheme="https", netloc=netloc, fragment="").geturl()
        return normalized, host, urlsplit(normalized)

    def _credential_audience(self, request: EgressRequest, destination: SplitResult) -> str | None:
        """Validate that token exchange targets the exact outbound origin.

        Args:
            request: Outbound request containing an optional credential audience.
            destination: Normalized destination URL components.

        Returns:
            Normalized credential audience, or `None` when no credential is requested.
        """
        if request.credential_audience is None:
            return None
        audience, _, parsed = self._normalize_url(request.credential_audience)
        destination_origin = (destination.scheme, destination.hostname, destination.port)
        audience_origin = (parsed.scheme, parsed.hostname, parsed.port)
        if audience_origin != destination_origin:
            raise PermissionPortalError(
                "Credential audience must match the outbound destination origin."
            )
        return audience


def _normalize_host(host: str) -> str:
    """Normalize a configured or parsed hostname.

    Args:
        host: DNS name or literal IP address.

    Returns:
        Lowercase IDNA hostname without a trailing root label.
    """
    selected = host.strip().rstrip(".")
    if not selected:
        raise ValueError("Egress hostnames cannot be empty.")
    try:
        return selected.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(f"Invalid egress hostname {host!r}.") from exc


def _validate_classification(classification: str) -> DataClassification:
    """Validate and narrow one data-classification label.

    Args:
        classification: Candidate classification label.

    Returns:
        Validated classification.
    """
    normalized = classification.strip().lower()
    if normalized not in _CLASSIFICATION_LEVEL:
        raise ValueError(f"Unsupported data classification {classification!r}.")
    return normalized  # type: ignore[return-value]


def _max_classification(
    first: DataClassification, second: DataClassification
) -> DataClassification:
    """Return the more restrictive of two classifications.

    Args:
        first: First classification.
        second: Second classification.

    Returns:
        Classification with the higher policy level.
    """
    return first if _CLASSIFICATION_LEVEL[first] >= _CLASSIFICATION_LEVEL[second] else second


def _payload_digest(payload: Any) -> str:
    """Hash a payload without retaining its potentially sensitive values.

    Args:
        payload: Original structured payload.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    canonical = json.dumps(
        _canonical_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_payload(value: Any) -> Any:
    """Convert a payload into deterministic, JSON-serializable audit material.

    Args:
        value: Payload value to canonicalize.

    Returns:
        Type-preserving structure suitable for deterministic hashing.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, Mapping):
        items = [
            [_canonical_payload(key), _canonical_payload(child)]
            for key, child in value.items()
        ]
        items.sort(
            key=lambda item: json.dumps(item[0], sort_keys=True, separators=(",", ":"))
        )
        return {"type": "mapping", "items": items}
    if isinstance(value, list):
        return {"type": "list", "items": [_canonical_payload(child) for child in value]}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_canonical_payload(child) for child in value]}
    if isinstance(value, set | frozenset):
        items = [_canonical_payload(child) for child in value]
        items.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return {"type": "set", "items": items}
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}
