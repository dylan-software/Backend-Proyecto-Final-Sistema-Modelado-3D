"""Refuses to run the tests against any database other than a local one.

Imports nothing from app, so conftest.py can use it before the application is loaded.
"""

from sqlalchemy import make_url

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
MEMORY_DATABASES = {None, "", ":memory:"}


def unsafe_database_reason(url):
    """Why `url` must not be used by the tests, or None if it is local.

    Allowed: in-memory SQLite and servers on localhost. Anything else (a Supabase pooler, a remote
    host, a SQLite file) is refused. The returned text never includes the password.
    """
    try:
        parsed = make_url(str(url))
    except Exception:  # noqa: BLE001 - any unparsable URL is unsafe by definition
        return "DATABASE_URL could not be parsed"
    if parsed.get_backend_name() == "sqlite":
        if parsed.database in MEMORY_DATABASES:
            return None
        return f"SQLite file {parsed.database!r} instead of an in-memory database"
    if parsed.host in LOCAL_HOSTS:
        return None
    return f"host {parsed.host!r} is not localhost"


def guard_message(reason):
    return (
        f"Refusing to run the tests: DATABASE_URL points at a non-local database ({reason}). "
        "The tests must only use in-memory SQLite or a database on localhost, never Supabase."
    )
