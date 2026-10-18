import os

# Must run before anything imports app.config: settings are read once and cached. Real environment
# variables take precedence over .env, so a local .env cannot leak into the tests.
os.environ["JWT_SECRET"] = "test-secret-not-used-anywhere-else"
os.environ["ENVIRONMENT"] = "test"
os.environ["ALLOWED_ORIGINS"] = "https://proyecto-3d.vercel.app,http://localhost:3000"
os.environ["ENABLE_DEBUG_ROUTES"] = "false"
# Never let a test reach the database configured in .env (it may be the real Supabase).
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import connection  # noqa: E402
from app.database.connection import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.space import Base  # noqa: E402
from app.tests.auth_helpers import auth_headers  # noqa: E402
from app.tests.db_guard import guard_message, unsafe_database_reason  # noqa: E402
from app.utils import rate_limit  # noqa: E402


def _configured_database_urls():
    # The settings value and the engine actually built from it, in case either is changed later.
    return (get_settings().database_url, connection.engine.url)


def _check_database_is_local():
    for url in _configured_database_urls():
        reason = unsafe_database_reason(url)
        if reason:
            return guard_message(reason)
    return None


# Checked once while loading, so a wrong configuration stops the whole run before any test starts.
_problem = _check_database_is_local()
if _problem:
    pytest.exit(_problem, returncode=2)


@pytest.fixture(autouse=True)
def _database_is_local():
    """Checked again around every test, in case a test or fixture swapped the configuration."""
    problem = _check_database_is_local()
    if problem:
        pytest.fail(problem, pytrace=False)
    yield
    problem = _check_database_is_local()
    if problem:
        pytest.fail(problem, pytrace=False)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Request counters start empty in every test, and re-read settings a test may have changed."""
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client(headers=None):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)


@pytest.fixture
async def client():
    """Client without a session token."""
    async with _client() as http:
        yield http


@pytest.fixture
async def user_a():
    """Client logged in as account "user-a"."""
    async with _client(auth_headers("user-a")) as http:
        yield http


@pytest.fixture
async def user_b():
    """Client logged in as account "user-b"."""
    async with _client(auth_headers("user-b")) as http:
        yield http


@pytest.fixture
async def db_sessions():
    """In-memory SQLite database standing in for PostgreSQL."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield sessions
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()
