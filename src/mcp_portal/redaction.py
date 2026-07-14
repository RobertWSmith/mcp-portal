"""Remove configured secrets and sensitive fields from portal data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Redactor:
    """Remove sensitive values from diagnostic payloads.

    Attributes:
        secrets: Literal secret strings to replace when they appear in text values.
        replacement: Placeholder used for removed secret values.
        secret_key_markers: Lowercase key fragments that identify sensitive fields.
    """

    secrets: tuple[str, ...] = field(
        default=(),
        metadata={
            "description": "Literal secret strings to replace when they appear in text values."
        },
    )
    replacement: str = field(
        default="[REDACTED]",
        metadata={"description": "Placeholder used for removed secret values."},
    )
    secret_key_markers: tuple[str, ...] = field(
        default=(
            "authorization",
            "bearer",
            "credential",
            "password",
            "secret",
            "token",
        ),
        metadata={"description": "Lowercase key fragments that identify sensitive fields."},
    )

    @classmethod
    def from_secrets(cls, secrets: tuple[str | None, ...]) -> "Redactor":
        """Create a redactor from optional literal secrets.

        Args:
            secrets: Secret strings that may be present in diagnostic payloads.

        Returns:
            A redactor with empty and placeholder values removed.
        """
        return cls(
            secrets=tuple(
                secret
                for secret in secrets
                if secret is not None and secret.strip() and secret != "your-api-key"
            )
        )

    def redact(self, value: Any) -> Any:
        """Return a copy of `value` with sensitive data removed.

        Args:
            value: Arbitrary diagnostic data.

        Returns:
            Redacted data with the same broad shape as the input.
        """
        if isinstance(value, Mapping):
            return {
                key: (
                    self.replacement
                    if self._is_secret_key(str(key)) and not isinstance(child, Mapping)
                    else self.redact(child)
                )
                for key, child in value.items()
            }

        if isinstance(value, list):
            return [self.redact(child) for child in value]

        if isinstance(value, tuple):
            return tuple(self.redact(child) for child in value)

        if isinstance(value, set):
            return sorted(self.redact(child) for child in value)

        if isinstance(value, str):
            return self._redact_text(value)

        return value

    def _is_secret_key(self, key: str) -> bool:
        """Report whether a mapping key should have its value removed.

        Args:
            key: Mapping key to inspect.

        Returns:
            True when the key conventionally stores a secret value.
        """
        normalized = key.lower().replace("-", "_")
        if normalized.startswith(("has_", "is_", "uses_")) or normalized.endswith(
            ("_configured", "_scope", "_scopes")
        ):
            return False

        return (
            normalized in {"api_key", "apikey", "openai_api_key"}
            or normalized.endswith("_api_key")
            or any(marker in normalized for marker in self.secret_key_markers)
        )

    def _redact_text(self, value: str) -> str:
        """Replace known literal secrets inside a string.

        Args:
            value: Text that may contain a known secret.

        Returns:
            Text with every known secret replaced.
        """
        redacted = value
        for secret in self.secrets:
            redacted = redacted.replace(secret, self.replacement)
        return redacted
