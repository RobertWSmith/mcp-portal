"""Construct default database and MongoDB client registries."""

from __future__ import annotations

from typing import Any

from mcp_portal.config import Settings
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.clients.mongodb import _create_langchain_mongodb_connectors
from mcp_portal.clients.registry import ClientFactories
from mcp_portal.telemetry import OpenTelemetryRecorder, TelemetryRecorder


def default_client_factories(
    settings: Settings | None = None,
    *,
    telemetry: TelemetryRecorder | None = None,
) -> ClientFactories:
    """Create the default external client registry.

    Args:
        settings: Optional runtime settings used to register configured backends.
        telemetry: Optional shared metrics and accounting recorder.

    Returns:
        A client registry ready for namespace-specific injection.
    """
    factories = ClientFactories(telemetry=telemetry or OpenTelemetryRecorder())
    if settings is not None:
        factories = factories.with_resilience(settings, telemetry=telemetry)
    if settings is not None and settings.database.sqlalchemy_configured:

        def database_ready() -> None:
            """Execute a minimal SQL query through the configured engine."""
            from sqlalchemy import text

            with factories.shared("database").connect() as connection:
                connection.execute(text("SELECT 1"))

        factories = factories.with_factory(
            "database",
            lambda: _create_sqlalchemy_engine(settings),
            shared=True,
            readiness_check=database_ready,
        )
    if settings is not None and settings.mongodb.configured:

        def mongodb_ready() -> None:
            """Ping MongoDB without retaining an additional readiness client."""
            from pymongo import MongoClient

            client = MongoClient(
                settings.mongodb.connection_string,
                serverSelectionTimeoutMS=int(settings.enterprise.downstream_timeout_seconds * 1000),
            )
            try:
                client.admin.command("ping")
            finally:
                client.close()

        factories = factories.with_factory(
            "langchain_mongodb",
            lambda: _create_langchain_mongodb_connectors(settings),
            shared=True,
            readiness_check=mongodb_ready,
        )

    return factories


def _create_sqlalchemy_engine(settings: Settings) -> Any:
    """Create a SQLAlchemy engine from runtime database settings.

    Args:
        settings: Runtime settings containing database metadata.

    Returns:
        A SQLAlchemy Engine.

    Raises:
        ConfigurationPortalError: If SQLAlchemy, the selected dialect, or its settings are
            unavailable.
    """
    try:
        create_engine = _import_sqlalchemy_create_engine()
    except ImportError as error:
        raise ConfigurationPortalError(
            "Database backend is configured but the required SQLAlchemy dependency is missing.",
            details={"dependency": "sqlalchemy", "client": "database"},
            cause=error,
        ) from error

    try:
        url, kwargs = _sqlalchemy_engine_configuration(settings)
        return create_engine(url, **kwargs)
    except ConfigurationPortalError:
        raise
    except Exception as error:
        raise ConfigurationPortalError(
            "SQLAlchemy database engine could not be created.",
            details={
                "client": "database",
                "provider": settings.database.provider,
                "sqlalchemy_url_configured": settings.database.sqlalchemy_url is not None,
                "oracle_configured": settings.database.oracle_configured,
            },
            cause=error,
        ) from error


def _import_sqlalchemy_create_engine() -> Any:
    """Import SQLAlchemy's engine factory lazily.

    Returns:
        The `sqlalchemy.create_engine` callable.
    """
    from sqlalchemy import create_engine

    return create_engine


def _sqlalchemy_engine_configuration(settings: Settings) -> tuple[str, dict[str, Any]]:
    """Build SQLAlchemy engine URL and keyword arguments.

    Args:
        settings: Runtime settings containing database metadata.

    Returns:
        A SQLAlchemy URL string and keyword arguments for `create_engine`.

    Raises:
        ConfigurationPortalError: If the database settings are incomplete.
    """
    base_kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_size": settings.database.oracle_pool_min,
        "max_overflow": max(
            0, settings.database.oracle_pool_max - settings.database.oracle_pool_min
        ),
    }

    if settings.database.sqlalchemy_url is not None:
        return settings.database.sqlalchemy_url, base_kwargs

    if settings.database.provider == "oracle" and settings.database.oracle_configured:
        return (
            "oracle+oracledb://",
            {
                **base_kwargs,
                "connect_args": {
                    "user": settings.database.oracle_user,
                    "password": settings.database.oracle_password,
                    "dsn": settings.database.oracle_dsn,
                },
            },
        )

    raise ConfigurationPortalError(
        "Database provider requires MCP_PORTAL_DATABASE_SQLALCHEMY_URL or complete "
        "Oracle settings.",
        details={"provider": settings.database.provider, "client": "database"},
    )
