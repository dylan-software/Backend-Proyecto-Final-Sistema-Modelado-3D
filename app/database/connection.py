from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

DATABASE_URL = get_settings().database_url

# asyncpg requires the "postgresql+asyncpg" scheme; accept plain postgres URLs too.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session


# Tables that gained a user_id column after they were first created in Supabase.
USER_SCOPED_TABLES = ("habitaciones", "muebles_catalogo")


async def add_missing_user_columns(conn):
    """Add the user_id column and its index to tables created before it existed.

    create_all only creates missing tables, it never alters existing ones. Both statements are
    no-ops when the column and index are already there. PostgreSQL only: tests use a fresh SQLite.
    """
    if conn.dialect.name != "postgresql":
        return
    for table in USER_SCOPED_TABLES:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id VARCHAR(64)"))
        await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)"))
