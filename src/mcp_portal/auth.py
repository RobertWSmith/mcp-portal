"""Configure token and enterprise authentication and authorization."""

from __future__ import annotations

import base64
import binascii
from contextvars import ContextVar
from functools import partial
import importlib.util
import os
import ssl
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import anyio
from fastmcp.server.auth import (
    AccessToken,
    AuthProvider,
    JWTVerifier,
    RemoteAuthProvider,
    StaticTokenVerifier,
)

from mcp_portal.config import Settings
from mcp_portal.errors import ConfigurationPortalError

_kerberos_response_token: ContextVar[bytes | None] = ContextVar(
    "kerberos_response_token", default=None
)


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
        if settings.auth.static_token is None:
            raise ConfigurationPortalError(
                "Static authentication provider requires MCP_PORTAL_AUTH_STATIC_TOKEN.",
                details={"provider": "static"},
            )
        return StaticTokenVerifier(
            tokens={
                settings.auth.static_token: {
                    "client_id": settings.auth.static_client_id,
                    "scopes": list(settings.auth.static_scopes or settings.auth.required_scopes),
                }
            },
            required_scopes=list(settings.auth.required_scopes),
        )

    if settings.auth.provider in {"oauth", "jwt"}:
        verifier = _create_jwt_verifier(settings)
        if settings.auth.provider == "jwt":
            return verifier
        return _create_oauth_resource_server(settings, verifier)

    if settings.auth.provider in {"ldap", "kerberos", "ldap_kerberos"}:
        return _create_enterprise_verifier(settings)

    raise ConfigurationPortalError(
        "Unsupported authentication provider.",
        details={"provider": settings.auth.provider},
    )


class PortalJWTVerifier(JWTVerifier):
    """Verify JWTs and normalize trusted OAuth identity and permission claims."""

    def __init__(self, settings: Settings) -> None:
        """Initialize strict JWT verification from portal settings.

        Args:
            settings: Portal settings containing JWT and claim mappings.
        """
        super().__init__(
            public_key=settings.auth.jwt_public_key,
            jwks_uri=settings.auth.jwt_jwks_uri,
            issuer=settings.auth.jwt_issuer,
            audience=settings.auth.jwt_audience,
            algorithm=settings.auth.jwt_algorithm,
        )
        self.portal_auth = settings.auth

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a bearer token and reject tokens without a real principal.

        Args:
            token: Encoded JWT bearer token.

        Returns:
            A normalized access token, or None when validation fails.
        """
        verified = await super().verify_token(token)
        if verified is None:
            return None
        claims = dict(verified.claims or {})
        if self.portal_auth.provider == "oauth" and claims.get("exp") is None:
            return None
        if not _valid_temporal_claims(claims, self.portal_auth.jwt_clock_skew_seconds):
            return None

        subject = _string_claim(claims, self.portal_auth.jwt_subject_claim)
        client_id = next(
            (
                value
                for claim in self.portal_auth.jwt_client_id_claims
                if (value := _string_claim(claims, claim)) is not None
            ),
            None,
        )
        if subject is None and client_id is None:
            return None

        roles = _claim_values(claims.get(self.portal_auth.jwt_roles_claim))
        scopes = sorted(set(verified.scopes) | roles)
        if not set(self.portal_auth.required_scopes) <= set(scopes):
            return None

        claims["_portal_client_id"] = client_id
        claims["_portal_roles"] = sorted(roles)
        claims["auth_method"] = "oauth" if self.portal_auth.provider == "oauth" else "bearer"
        return AccessToken(
            token=verified.token,
            client_id=client_id or subject or "",
            subject=subject,
            scopes=scopes,
            expires_at=verified.expires_at,
            resource=verified.resource,
            claims=claims,
        )


def _create_jwt_verifier(settings: Settings) -> PortalJWTVerifier:
    """Validate JWT key configuration and construct the portal verifier.

    Args:
        settings: Portal settings containing JWT verification configuration.

    Returns:
        Configured strict portal JWT verifier.
    """
    key_sources = sum(
        value is not None for value in (settings.auth.jwt_public_key, settings.auth.jwt_jwks_uri)
    )
    if key_sources != 1:
        raise ConfigurationPortalError(
            "JWT authentication requires exactly one of MCP_PORTAL_AUTH_JWT_PUBLIC_KEY or "
            "MCP_PORTAL_AUTH_JWT_JWKS_URI.",
            details={"provider": settings.auth.provider},
        )
    if settings.auth.jwt_clock_skew_seconds < 0:
        raise ConfigurationPortalError(
            "JWT clock skew must be non-negative.",
            details={"provider": settings.auth.provider},
        )
    try:
        return PortalJWTVerifier(settings)
    except ValueError as error:
        raise ConfigurationPortalError(
            "JWT authentication configuration is invalid.",
            details={"provider": settings.auth.provider, "reason": str(error)},
        ) from error


def _create_oauth_resource_server(
    settings: Settings, verifier: PortalJWTVerifier
) -> RemoteAuthProvider:
    """Create a discoverable OAuth protected resource around the JWT verifier.

    Args:
        settings: Portal settings containing OAuth discovery configuration.
        verifier: Strict verifier used to authenticate access tokens.

    Returns:
        Discoverable remote OAuth resource-server provider.
    """
    auth = settings.auth
    missing = [
        name
        for name, value in (
            ("MCP_PORTAL_AUTH_AUTHORIZATION_SERVER_URL", auth.authorization_server_url),
            ("MCP_PORTAL_AUTH_JWT_ISSUER", auth.jwt_issuer),
            ("MCP_PORTAL_AUTH_JWT_AUDIENCE", auth.jwt_audience),
            ("MCP_PORTAL_AUTH_RESOURCE_SERVER_URL", auth.resource_server_url),
        )
        if not value
    ]
    if missing:
        raise ConfigurationPortalError(
            "OAuth authentication requires authorization-server, issuer, audience, and "
            "resource-server URLs.",
            details={"provider": "oauth", "missing": missing},
        )
    if auth.jwt_jwks_uri is None:
        raise ConfigurationPortalError(
            "OAuth authentication requires MCP_PORTAL_AUTH_JWT_JWKS_URI for key rotation.",
            details={"provider": "oauth"},
        )
    base_url = _oauth_base_url(auth.resource_server_url, settings.http.path)
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[auth.authorization_server_url],
        base_url=base_url,
        scopes_supported=_supported_scopes(settings),
        resource_name="MCP Portal",
    )


def _oauth_base_url(resource_url: str, mcp_path: str) -> str:
    """Derive the public ASGI base URL from the exact MCP resource URL.

    Args:
        resource_url: Exact externally visible MCP resource URL.
        mcp_path: Configured MCP HTTP endpoint path.

    Returns:
        External application base URL used to construct discovery routes.
    """
    parsed = urlsplit(resource_url)
    normalized_mcp_path = f"/{mcp_path.strip('/')}"
    resource_path = parsed.path.rstrip("/") or "/"
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or not resource_path.endswith(normalized_mcp_path)
    ):
        raise ConfigurationPortalError(
            "OAuth resource server URL must be an absolute URL ending in the configured "
            "MCP HTTP path and must not contain a query or fragment.",
            details={"resource_server_url": resource_url, "http_path": mcp_path},
        )
    base_path = resource_path[: -len(normalized_mcp_path)].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, base_path or "/", "", ""))


def _supported_scopes(settings: Settings) -> list[str]:
    """Return configured OAuth scopes advertised in protected-resource metadata.

    Args:
        settings: Portal authentication and authorization settings.

    Returns:
        Sorted configured OAuth scopes.
    """
    scopes = set(settings.auth.required_scopes)
    for configured in settings.authorization.tag_scopes.values():
        scopes.update(configured)
    for configured in settings.authorization.namespace_scopes.values():
        scopes.update(configured)
    return sorted(scopes)


def _string_claim(claims: dict[str, Any], name: str) -> str | None:
    """Return a nonempty scalar string claim.

    Args:
        claims: Verified access-token claims.
        name: Claim name to read.

    Returns:
        Normalized claim value, or None when absent or invalid.
    """
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip() or value == "unknown":
        return None
    return value


def _claim_values(value: Any) -> set[str]:
    """Normalize a space-delimited or array claim into nonempty strings.

    Args:
        value: Untrusted verified-claim value.

    Returns:
        Normalized nonempty string values.
    """
    if isinstance(value, str):
        return {item for item in value.split() if item}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str) and item}
    return set()


def _valid_temporal_claims(claims: dict[str, Any], clock_skew_seconds: float) -> bool:
    """Validate not-before and issued-at claims with bounded clock skew.

    Args:
        claims: Verified access-token claims.
        clock_skew_seconds: Maximum accepted future clock skew.

    Returns:
        True when temporal claims are numeric and currently valid.
    """
    now = time.time()
    for claim in ("nbf", "iat"):
        value = claims.get(claim)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if value > now + clock_skew_seconds:
            return False
    return True


class EnterpriseAuthProvider(AuthProvider):
    """Verify LDAP Basic credentials and/or Kerberos service tickets."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the enabled enterprise authentication mechanisms.

        Args:
            settings: Portal settings containing LDAP and Kerberos configuration.
        """
        super().__init__(required_scopes=list(settings.auth.required_scopes))
        self.settings = settings
        self.ldap_enabled = settings.auth.provider in {"ldap", "ldap_kerberos"}
        self.kerberos_enabled = settings.auth.provider in {"kerberos", "ldap_kerberos"}

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a scheme-tagged token produced by the HTTP auth adapter.

        Args:
            token: Scheme-tagged LDAP credentials or Kerberos service ticket.

        Returns:
            Authenticated principal metadata, or None when verification fails.
        """
        scheme, separator, payload = token.partition(":")
        if not separator:
            return None

        if scheme == "ldap" and self.ldap_enabled:
            try:
                decoded_credentials = base64.b64decode(payload, validate=True).decode("utf-8")
            except (UnicodeDecodeError, binascii.Error):
                return None
            username, separator, password = decoded_credentials.partition(":")
            if not separator or not username or not password:
                return None
            try:
                if not await anyio.to_thread.run_sync(
                    _verify_ldap_credentials, self.settings, username, password
                ):
                    return None
            except Exception:
                return None
            return AccessToken(
                token="ldap-authenticated",
                client_id=username,
                subject=username,
                scopes=list(self.settings.auth.ldap_scopes or self.settings.auth.required_scopes),
                claims={"auth_method": "ldap"},
            )

        if scheme == "kerberos" and self.kerberos_enabled:
            try:
                service_ticket = base64.b64decode(payload, validate=True)
                principal, response_token = await anyio.to_thread.run_sync(
                    _verify_kerberos_ticket,
                    self.settings,
                    service_ticket,
                )
            except Exception:
                return None
            if not principal:
                return None
            _kerberos_response_token.set(response_token)
            return AccessToken(
                token="kerberos-authenticated",
                client_id=principal,
                subject=principal,
                scopes=list(
                    self.settings.auth.kerberos_scopes or self.settings.auth.required_scopes
                ),
                claims={"auth_method": "kerberos"},
            )

        return None


class EnterpriseAuthSchemeMiddleware:
    """Translate Basic/Negotiate headers for the SDK's bearer-only auth backend."""

    def __init__(self, app: Any, provider: EnterpriseAuthProvider) -> None:
        """Initialize the HTTP authentication scheme adapter.

        Args:
            app: Wrapped SDK ASGI application.
            provider: Enterprise verifier defining the enabled schemes.
        """
        self.app = app
        self.provider = provider

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Adapt an ASGI request and attach authentication response challenges.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        selected_scope = self._adapt_scope(scope)
        context_token = _kerberos_response_token.set(None)
        try:
            await self.app(
                selected_scope,
                receive,
                partial(self._send_with_challenge, send=send),
            )
        finally:
            _kerberos_response_token.reset(context_token)

    def _adapt_scope(self, scope: dict[str, Any]) -> dict[str, Any]:
        """Translate an enterprise authorization header into a bearer header.

        Args:
            scope: Incoming HTTP ASGI scope.

        Returns:
            The original or translated ASGI scope.
        """
        headers = list(scope.get("headers", ()))
        for index, (name, value) in enumerate(headers):
            if name.lower() != b"authorization":
                continue
            scheme, separator, payload = value.partition(b" ")
            normalized_scheme = scheme.lower()
            if separator and normalized_scheme == b"basic" and self.provider.ldap_enabled:
                headers[index] = (name, b"Bearer ldap:" + payload)
                return dict(scope, headers=headers)
            if separator and normalized_scheme == b"negotiate" and self.provider.kerberos_enabled:
                headers[index] = (name, b"Bearer kerberos:" + payload)
                return dict(scope, headers=headers)
            return scope
        return scope

    async def _send_with_challenge(self, message: dict[str, Any], *, send: Any) -> None:
        """Attach Basic or Negotiate challenges to an ASGI response.

        Args:
            message: Outbound ASGI response message.
            send: Wrapped ASGI send callable.
        """
        if message["type"] == "http.response.start":
            response_headers = list(message.get("headers", ()))
            status = message.get("status")
            kerberos_token = _kerberos_response_token.get()
            if kerberos_token:
                challenge = b"Negotiate " + base64.b64encode(kerberos_token)
                response_headers.append((b"www-authenticate", challenge))
            elif status == 401 and self.provider.kerberos_enabled:
                response_headers.append((b"www-authenticate", b"Negotiate"))
            if status == 401 and self.provider.ldap_enabled:
                response_headers.append((b"www-authenticate", b'Basic realm="mcp-portal"'))
            message = dict(message, headers=response_headers)
        await send(message)


def _create_enterprise_verifier(settings: Settings) -> EnterpriseAuthProvider:
    """Validate enterprise auth configuration and create its verifier.

    Args:
        settings: Portal settings containing enterprise authentication configuration.

    Returns:
        Configured LDAP and/or Kerberos verifier.
    """
    if settings.auth.provider in {"ldap", "ldap_kerberos"}:
        _validate_ldap_settings(settings)

    if settings.auth.provider in {"kerberos", "ldap_kerberos"}:
        _validate_kerberos_settings(settings)

    return EnterpriseAuthProvider(settings)


def _validate_ldap_settings(settings: Settings) -> None:
    """Validate LDAP settings and require the optional directory dependency.

    Args:
        settings: Portal settings containing LDAP configuration.

    Raises:
        ConfigurationPortalError: If LDAP configuration is unsafe or incomplete.
    """
    if settings.auth.ldap_uri is None:
        raise ConfigurationPortalError(
            "LDAP authentication requires MCP_PORTAL_AUTH_LDAP_URI.",
            details={"provider": settings.auth.provider},
        )
    if not settings.auth.ldap_user_dn_template and not settings.auth.ldap_base_dn:
        raise ConfigurationPortalError(
            "LDAP authentication requires MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE or "
            "MCP_PORTAL_AUTH_LDAP_BASE_DN.",
            details={"provider": settings.auth.provider},
        )
    if (
        settings.auth.ldap_user_dn_template
        and "{username}" not in settings.auth.ldap_user_dn_template
    ):
        raise ConfigurationPortalError(
            "MCP_PORTAL_AUTH_LDAP_USER_DN_TEMPLATE must contain {username}.",
            details={"provider": settings.auth.provider},
        )
    if (
        not settings.auth.ldap_user_dn_template
        and "{username}" not in settings.auth.ldap_search_filter
    ):
        raise ConfigurationPortalError(
            "MCP_PORTAL_AUTH_LDAP_SEARCH_FILTER must contain {username}.",
            details={"provider": settings.auth.provider},
        )
    if bool(settings.auth.ldap_bind_dn) != bool(settings.auth.ldap_bind_password):
        raise ConfigurationPortalError(
            "MCP_PORTAL_AUTH_LDAP_BIND_DN and MCP_PORTAL_AUTH_LDAP_BIND_PASSWORD "
            "must be configured together.",
            details={"provider": settings.auth.provider},
        )
    if not settings.auth.ldap_uri.lower().startswith("ldaps://") and not (
        settings.auth.ldap_uri.lower().startswith("ldap://") and settings.auth.ldap_start_tls
    ):
        raise ConfigurationPortalError(
            "LDAP authentication requires LDAPS or MCP_PORTAL_AUTH_LDAP_START_TLS=true.",
            details={"provider": settings.auth.provider},
        )
    _require_optional_dependency("ldap3", "ldap")


def _validate_kerberos_settings(settings: Settings) -> None:
    """Validate Kerberos settings and require the optional SPNEGO dependency.

    Args:
        settings: Portal settings containing Kerberos configuration.

    Raises:
        ConfigurationPortalError: If Kerberos configuration is incomplete.
    """
    if settings.auth.kerberos_hostname is None:
        raise ConfigurationPortalError(
            "Kerberos authentication requires MCP_PORTAL_AUTH_KERBEROS_HOSTNAME.",
            details={"provider": settings.auth.provider},
        )
    _require_optional_dependency("spnego", "kerberos")
    if settings.auth.kerberos_keytab:
        os.environ.setdefault("KRB5_KTNAME", settings.auth.kerberos_keytab)


def _require_optional_dependency(module: str, extra: str) -> None:
    """Require an optional authentication dependency at server startup.

    Args:
        module: Importable module name supplied by the dependency.
        extra: Project extra that installs the dependency.
    """
    if importlib.util.find_spec(module) is None:
        raise ConfigurationPortalError(
            f"{module} is required for this authentication provider; install mcp-portal[{extra}].",
            details={"dependency": module, "extra": extra},
        )


def _verify_ldap_credentials(  # pragma: no cover - live directory integration
    settings: Settings, username: str, password: str
) -> bool:
    """Bind to LDAP to verify a user's password.

    Args:
        settings: Portal settings containing directory connection configuration.
        username: Untrusted username supplied through HTTP Basic authentication.
        password: Password supplied through HTTP Basic authentication.

    Returns:
        True when the user's directory bind succeeds.
    """
    from ldap3 import Connection, Server, SUBTREE, Tls
    from ldap3.utils.conv import escape_filter_chars
    from ldap3.utils.dn import escape_rdn

    auth = settings.auth
    parsed_uri = urlsplit(auth.ldap_uri)
    server = Server(
        parsed_uri.hostname,
        port=parsed_uri.port,
        use_ssl=parsed_uri.scheme.lower() == "ldaps",
        tls=Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=auth.ldap_ca_cert_file),
        connect_timeout=auth.ldap_connect_timeout,
    )

    if auth.ldap_user_dn_template:
        user_dn = auth.ldap_user_dn_template.format(username=escape_rdn(username))
    else:
        search_connection = Connection(
            server,
            user=auth.ldap_bind_dn,
            password=auth.ldap_bind_password,
            receive_timeout=auth.ldap_connect_timeout,
            raise_exceptions=True,
        )
        try:
            search_connection.open()
            if auth.ldap_start_tls:
                search_connection.start_tls()
            if not search_connection.bind():
                return False
            if (
                not search_connection.search(
                    auth.ldap_base_dn,
                    auth.ldap_search_filter.format(username=escape_filter_chars(username)),
                    search_scope=SUBTREE,
                    attributes=[],
                    size_limit=2,
                    time_limit=max(1, int(auth.ldap_connect_timeout)),
                )
                or len(search_connection.entries) != 1
            ):
                return False
            user_dn = search_connection.entries[0].entry_dn
        finally:
            search_connection.unbind()

    user_connection = Connection(
        server,
        user=user_dn,
        password=password,
        receive_timeout=auth.ldap_connect_timeout,
        raise_exceptions=True,
    )
    try:
        user_connection.open()
        if auth.ldap_start_tls:
            user_connection.start_tls()
        return bool(user_connection.bind())
    finally:
        user_connection.unbind()


def _verify_kerberos_ticket(  # pragma: no cover - live KDC integration
    settings: Settings, service_ticket: bytes
) -> tuple[str, bytes | None]:
    """Accept a Kerberos service ticket using the platform credential store.

    Args:
        settings: Portal settings containing the HTTP service principal configuration.
        service_ticket: Decoded SPNEGO/Kerberos token from the client.

    Returns:
        Authenticated client principal and optional mutual-authentication response token.
    """
    import spnego

    context = spnego.server(
        hostname=settings.auth.kerberos_hostname,
        service=settings.auth.kerberos_service,
        protocol="kerberos",
    )
    response_token = context.step(service_ticket)
    if not context.complete or not context.client_principal:
        raise ValueError("Kerberos negotiation did not produce an authenticated principal")
    return str(context.client_principal), response_token
