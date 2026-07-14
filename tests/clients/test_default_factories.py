"""Test default SQLAlchemy and Oracle client factory construction."""

from dataclasses import replace

import pytest

import mcp_portal.clients.factory as client_factory_module
from mcp_portal.clients import default_client_factories
from mcp_portal.config import DatabaseSettings
from mcp_portal.errors import ConfigurationPortalError
from mcp_portal.testing import create_test_settings


def test_oracle_backend_factory_is_registered_when_configured(monkeypatch) -> None:
    """Verify Oracle config creates a SQLAlchemy database engine factory."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(
            provider="oracle",
            oracle_dsn="db.example/orclpdb1",
            oracle_user="portal",
            oracle_password="secret",
        ),
    )
    captured = {}

    def fake_create_engine(url, **kwargs):
        """Capture SQLAlchemy engine construction options."""
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"engine": "sqlalchemy"}

    monkeypatch.setattr(
        client_factory_module,
        "_import_sqlalchemy_create_engine",
        lambda: fake_create_engine,
    )
    registry = default_client_factories(settings)
    engine = registry.create("database")

    assert registry.get("database") is not None
    assert registry.get("oracle") is None
    assert engine == {"engine": "sqlalchemy"}
    assert captured == {
        "url": "oracle+oracledb://",
        "kwargs": {
            "pool_pre_ping": True,
            "pool_size": 1,
            "max_overflow": 3,
            "connect_args": {
                "user": "portal",
                "password": "secret",
                "dsn": "db.example/orclpdb1",
            },
        },
    }


def test_sqlalchemy_url_database_factory_is_portable(monkeypatch) -> None:
    """Verify a generic SQLAlchemy URL can be used for non-Oracle engines."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(
            provider="sqlalchemy",
            sqlalchemy_url="sqlite:///portable.db",
            oracle_pool_min=2,
            oracle_pool_max=5,
        ),
    )
    captured = {}

    def fake_create_engine(url, **kwargs):
        """Capture SQLAlchemy URL engine construction options."""
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"engine": "portable"}

    monkeypatch.setattr(
        client_factory_module,
        "_import_sqlalchemy_create_engine",
        lambda: fake_create_engine,
    )
    registry = default_client_factories(settings)

    assert registry.create("database") == {"engine": "portable"}
    assert captured == {
        "url": "sqlite:///portable.db",
        "kwargs": {
            "pool_pre_ping": True,
            "pool_size": 2,
            "max_overflow": 3,
        },
    }


def test_database_factory_requires_sqlalchemy_dependency(monkeypatch) -> None:
    """Verify configured database access fails through the SQLAlchemy boundary."""
    settings = replace(
        create_test_settings(),
        database=DatabaseSettings(sqlalchemy_url="sqlite:///portable.db"),
    )
    monkeypatch.setattr(
        client_factory_module,
        "_import_sqlalchemy_create_engine",
        lambda: (_ for _ in ()).throw(ImportError("missing sqlalchemy")),
    )
    registry = default_client_factories(settings)

    with pytest.raises(ConfigurationPortalError, match="SQLAlchemy dependency"):
        registry.create("database")
