from __future__ import annotations

import base64
import binascii
from contextvars import ContextVar
from functools import partial
import importlib.util
import os
import ssl
from typing import Any
from urllib.parse import urlsplit

import anyio
from fastmcp.server.auth import (
    AccessToken,
    AuthCheck,
    AuthProvider,
    JWTVerifier,
    StaticTokenVerifier,
    restrict_tag,
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

    if settings.auth.provider == "jwt":
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

    if settings.auth.provider in {"ldap", "kerberos", "ldap_kerberos"}:
        return _create_enterprise_verifier(settings)

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
