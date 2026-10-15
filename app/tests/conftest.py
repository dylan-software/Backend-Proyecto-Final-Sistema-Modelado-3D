import os

# Must run before anything imports app.config: settings are read once and cached. Real environment
# variables take precedence over .env, so a local .env cannot leak into the tests.
os.environ["JWT_SECRET"] = "test-secret-not-used-anywhere-else"
os.environ["ENVIRONMENT"] = "test"
os.environ["ALLOWED_ORIGINS"] = "https://proyecto-3d.vercel.app,http://localhost:3000"
os.environ["ENABLE_DEBUG_ROUTES"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database.connection import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.space import Base  # noqa: E402
from app.tests.auth_helpers import auth_headers  # noqa: E402


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
