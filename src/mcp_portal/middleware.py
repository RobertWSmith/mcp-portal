from __future__ import annotations

from fastmcp.server.middleware import AuthMiddleware, Middleware
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware, StructuredLoggingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from mcp_portal.auth import create_authorization_checks
from mcp_portal.config import Settings


def create_production_middleware(
    settings: Settings,
    *,
    enabled: bool | None = None,
) -> tuple[Middleware, ...]:
    """Create production middleware from runtime settings.

    Args:
        settings: Runtime settings containing middleware and authorization policy.
        enabled: Optional override for whether middleware should be created.

    Returns:
        Middleware instances ordered for parent-server installation.
    """
    should_enable = settings.middleware.enabled if enabled is None else enabled
    if not should_enable:
        return ()

    middleware: list[Middleware] = [ErrorHandlingMiddleware()]

    if settings.middleware.rate_limit_per_second > 0:
        middleware.append(
            RateLimitingMiddleware(
                max_requests_per_second=settings.middleware.rate_limit_per_second,
                burst_capacity=settings.middleware.rate_limit_burst,
                global_limit=True,
            )
        )

    authorization_checks = create_authorization_checks(settings)
    if authorization_checks:
        middleware.append(AuthMiddleware(auth=authorization_checks))

    if settings.middleware.structured_logging:
        middleware.append(
            StructuredLoggingMiddleware(
                include_payload_length=settings.middleware.include_payload_length
            )
        )
    else:
        middleware.append(
            LoggingMiddleware(include_payload_length=settings.middleware.include_payload_length)
        )

    middleware.append(TimingMiddleware())

    if settings.middleware.response_max_bytes > 0:
        middleware.append(
            ResponseLimitingMiddleware(max_size=settings.middleware.response_max_bytes)
        )

    return tuple(middleware)
