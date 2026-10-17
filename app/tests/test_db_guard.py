from types import SimpleNamespace

import pytest

from app.database import connection
from app.tests import conftest
from app.tests.db_guard import unsafe_database_reason


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite://",
        "sqlite+aiosqlite:///:memory:",
        "postgresql://app:app@localhost:5432/app",
        "postgresql+asyncpg://app:app@127.0.0.1/app",
    ],
)
def test_local_databases_are_allowed(url):
    assert unsafe_database_reason(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres.ref:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
        "postgres://user:pw@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql+asyncpg://app:app@10.0.0.5/app",
        "sqlite+aiosqlite:///real-data.db",
        "not a url",
    ],
)
def test_remote_or_file_databases_are_refused(url):
    reason = unsafe_database_reason(url)

    assert reason
    assert "pw" not in reason  # the password never appears in the message


def test_the_configured_database_is_local():
    assert conftest._check_database_is_local() is None


def test_guard_detects_an_engine_pointing_at_supabase(monkeypatch):
    remote = "postgresql+asyncpg://postgres.ref:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    monkeypatch.setattr(connection, "engine", SimpleNamespace(url=remote))

    message = conftest._check_database_is_local()

    assert "Refusing to run the tests" in message
    assert "pooler.supabase.com" in message
    assert ":pw@" not in message
