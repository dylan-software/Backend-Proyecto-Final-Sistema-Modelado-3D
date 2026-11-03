from uuid import uuid4

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

DATABASE_URL = get_settings().database_url

# asyncpg requires the "postgresql+asyncpg" scheme; accept plain postgres URLs too.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


def unique_statement_name():
    """Nombre único para cada sentencia preparada de asyncpg.

    Con un pooler en modo transacción (pgbouncer, Supavisor de Supabase) cada transacción puede ir a
    una conexión distinta del servidor: los nombres fijos de asyncpg chocan ("ya existe", 42P05) o
    no se encuentran ("no existe", 26000). Receta de SQLAlchemy para pgbouncer.
    """
    return f"__asyncpg_{uuid4()}__"


def pooler_connect_args():
    """connect_args para funcionar detrás de un pooler en modo transacción.

    statement_cache_size=0 apaga la caché de asyncpg, prepared_statement_cache_size=0 la del
    dialecto de SQLAlchemy, y prepared_statement_name_func da nombres únicos a las sentencias que
    asyncpg sigue preparando. También funciona con una conexión directa o con el pooler de sesión.
    """
    return {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": unique_statement_name,
    }


def build_engine(url):
    """Engine asíncrono de la aplicación. No conecta: la primera conexión se abre al usarlo.

    Los connect_args del pooler solo se aplican con asyncpg (PostgreSQL); otros drivers, como el
    aiosqlite de las pruebas, no los aceptan.
    """
    connect_args = pooler_connect_args() if make_url(url).get_driver_name() == "asyncpg" else {}
    return create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)


engine = build_engine(DATABASE_URL)

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


async def add_missing_room_columns(conn):
    """Add habitaciones.vertices (floor polygon) to databases created before it existed.

    Same approach as add_missing_user_columns: a no-op when the column is already there.
    PostgreSQL only: tests use a fresh SQLite, where create_all already includes it.
    """
    if conn.dialect.name != "postgresql":
        return
    await conn.execute(text("ALTER TABLE habitaciones ADD COLUMN IF NOT EXISTS vertices JSONB"))
