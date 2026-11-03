"""Configuración del engine para el pooler de Supabase en modo transacción. Nunca conecta a nada."""

import re

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.util.concurrency import greenlet_spawn

from app.database import connection
from app.database.connection import build_engine, pooler_connect_args, unique_statement_name

# Host reservado (.invalid, RFC 2606): aunque algo intentara conectar, no resolvería.
POOLER_URL = "postgresql+asyncpg://usuario@pooler.invalid:6543/postgres"


def test_both_statement_caches_are_off():
    args = pooler_connect_args()

    assert args["statement_cache_size"] == 0  # caché de asyncpg
    assert args["prepared_statement_cache_size"] == 0  # caché del dialecto de SQLAlchemy
    assert args["prepared_statement_name_func"] is unique_statement_name


def test_statement_names_are_unique():
    names = {unique_statement_name() for _ in range(1000)}

    assert len(names) == 1000
    assert all(re.fullmatch(r"__asyncpg_[0-9a-f-]{36}__", name) for name in names)


def test_the_engine_is_built_with_the_pooler_arguments(monkeypatch):
    calls = []

    def fake_create_async_engine(url, **kwargs):
        calls.append((url, kwargs))
        return "engine"

    monkeypatch.setattr(connection, "create_async_engine", fake_create_async_engine)

    assert build_engine(POOLER_URL) == "engine"
    ((url, kwargs),) = calls
    assert url == POOLER_URL
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["connect_args"] == pooler_connect_args()


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///datos.db"])
def test_other_drivers_get_no_pooler_arguments(monkeypatch, url):
    calls = []
    monkeypatch.setattr(connection, "create_async_engine", lambda url, **kwargs: calls.append(kwargs))

    build_engine(url)

    assert calls == [{"pool_pre_ping": True, "connect_args": {}}]


def test_sqlalchemy_accepts_the_arguments_without_connecting():
    engine = build_engine(POOLER_URL)
    try:
        assert isinstance(engine, AsyncEngine)
        assert engine.dialect.driver == "asyncpg"
        # create_async_engine no abre ninguna conexión: el pool sigue vacío.
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        engine.sync_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["prepared_statement_cache_size", "prepared_statement_name_func"])
async def test_the_asyncpg_adapter_consumes_the_sqlalchemy_only_keys(monkeypatch, key):
    """SQLAlchemy retira sus dos claves antes de llamar a asyncpg.connect; asyncpg recibe el resto."""
    received = {}

    async def fake_asyncpg_connect(*args, **kwargs):
        received.update(kwargs)
        raise ConnectionRefusedError("sin red en las pruebas")

    engine = build_engine(POOLER_URL)
    monkeypatch.setattr(engine.dialect.dbapi.asyncpg, "connect", fake_asyncpg_connect)
    try:
        # El adaptador espera correr dentro de greenlet_spawn, como cuando lo llama el engine.
        with pytest.raises(ConnectionRefusedError):
            await greenlet_spawn(engine.dialect.dbapi.connect, **pooler_connect_args(), host="pooler.invalid")
    finally:
        engine.sync_engine.dispose()

    assert key not in received
    assert received["statement_cache_size"] == 0
