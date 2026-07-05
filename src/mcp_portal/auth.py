from __future__ import annotations

from fastmcp.server.auth import (
    AuthCheck,
    AuthProvider,
    JWTVerifier,
    StaticTokenVerifier,
    restrict_tag,
)

from mcp_portal.config import Settings
from mcp_portal.errors import ConfigurationPortalError


def create_auth_provider(settings: Settings) -> AuthProvider | None:
    """Create the configured FastMCP authentication provider.

    Args:
        settings: Runtime settings containing authentication configuration.

    Returns:
        A FastMCP authentication provider, or None when auth is disabled.

    Raises:
        ConfigurationPortalError: If the selected provider lacks required settings.
    """
    if settings.auth.provider == "none":
        return None

    if settings.auth.provider == "static":
        return _create_static_token_verifier(settings)

    if settings.auth.provider == "jwt":
        return _create_jwt_verifier(settings)

    raise ConfigurationPortalError(
        "Unsupported authentication provider.",
        details={"provider": settings.auth.provider},
    )


def create_authorization_checks(settings: Settings) -> list[AuthCheck]:
    """Create tag-based authorization checks from runtime settings.

    Args:
        settings: Runtime settings containing authorization policy.

    Returns:
        A list of FastMCP authorization checks.
    """
    return [
        restrict_tag(tag, scopes=list(scopes))
        for tag, scopes in sorted(settings.authorization.tag_scopes.items())
    ]


def _create_static_token_verifier(settings: Settings) -> AuthProvider:
    """Create a development-only static token verifier.

    Args:
        settings: Runtime settings containing static-token configuration.

    Returns:
        A FastMCP static token verifier.

    Raises:
        ConfigurationPortalError: If the static token is missing.
    """
    if settings.auth.static_token is None:
        raise ConfigurationPortalError(
            "Static authentication provider requires MCP_PORTAL_AUTH_STATIC_TOKEN.",
            details={"provider": "static"},
        )

    scopes = settings.auth.static_scopes or settings.auth.required_scopes
    return StaticTokenVerifier(
        tokens={
            settings.auth.static_token: {
                "client_id": settings.auth.static_client_id,
                "scopes": list(scopes),
            }
        },
        required_scopes=list(settings.auth.required_scopes),
    )


def _create_jwt_verifier(settings: Settings) -> AuthProvider:
    """Create a JWT or JWKS token verifier.

    Args:
        settings: Runtime settings containing JWT verifier configuration.

    Returns:
        A FastMCP JWT token verifier.

    Raises:
        ConfigurationPortalError: If neither a public key nor JWKS URI is configured.
    """
    if settings.auth.jwt_public_key is None and settings.auth.jwt_jwks_uri is None:
        raise ConfigurationPortalError(
            "JWT authentication requires MCP_PORTAL_AUTH_JWT_PUBLIC_KEY or "
            "MCP_PORTAL_AUTH_JWT_JWKS_URI.",
            details={"provider": "jwt"},
        )

    return JWTVerifier(
        public_key=settings.auth.jwt_public_key,
        jwks_uri=settings.auth.jwt_jwks_uri,
        issuer=settings.auth.jwt_issuer,
        audience=settings.auth.jwt_audience,
        algorithm=settings.auth.jwt_algorithm,
        required_scopes=list(settings.auth.required_scopes),
    )
