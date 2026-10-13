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
