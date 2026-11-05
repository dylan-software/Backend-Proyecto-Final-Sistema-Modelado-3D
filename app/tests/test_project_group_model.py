"""Modelo de grupos de proyectos y su migración de arranque (migración 005), sin base real."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.connection import PROJECT_GROUP_STATEMENTS, add_project_group_columns
from app.models.space import Habitacion, ProjectGroup
from app.tests.test_room import RecordingConnection

pytestmark = pytest.mark.anyio


async def test_the_startup_migration_on_postgresql_adds_columns_indexes_and_rls():
    conn = RecordingConnection("postgresql")

    await add_project_group_columns(conn)

    assert conn.statements == list(PROJECT_GROUP_STATEMENTS)
    assert conn.statements[-1] == "ALTER TABLE grupos_proyecto ENABLE ROW LEVEL SECURITY"
    joined = " ".join(conn.statements)
    for fragment in (
        "ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES grupos_proyecto (id) ON DELETE SET NULL",
        "ADD COLUMN IF NOT EXISTS descripcion TEXT CONSTRAINT ck_habitaciones_descripcion_len",
        "ADD COLUMN IF NOT EXISTS derivado_de INTEGER REFERENCES habitaciones (id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS ix_habitaciones_grupo_id",
        "CREATE INDEX IF NOT EXISTS ix_grupos_proyecto_user_id",
    ):
        assert fragment in joined


async def test_the_startup_migration_does_nothing_on_sqlite():
    conn = RecordingConnection("sqlite")

    await add_project_group_columns(conn)

    assert conn.statements == []


async def test_startup_runs_the_project_group_migration(monkeypatch):
    from app import main

    calls = []

    async def fake_migration(conn):
        calls.append(conn.dialect.name)

    monkeypatch.setattr(main, "add_project_group_columns", fake_migration)
    async with main.lifespan(main.app):
        pass

    # conftest.py apunta DATABASE_URL a un SQLite en memoria: nunca toca una base real.
    assert calls == ["sqlite"]


async def test_create_all_builds_the_named_description_checks(db_sessions):
    async with db_sessions() as session:
        session.add(ProjectGroup(user_id="user-a", name="Casa", description="x" * 1000))
        await session.commit()

    async with db_sessions() as session:
        session.add(ProjectGroup(user_id="user-a", name="Casa", description="x" * 1001))
        with pytest.raises(IntegrityError, match="ck_grupos_proyecto_descripcion_len"):
            await session.commit()

    async with db_sessions() as session:
        room = Habitacion(user_id="user-a", nombre="Sala", ancho=4, alto=2.5, profundidad=3)
        room.description = "y" * 1001
        session.add(room)
        with pytest.raises(IntegrityError, match="ck_habitaciones_descripcion_len"):
            await session.commit()


async def test_a_new_group_has_its_creation_time_and_rooms_start_without_group(db_sessions):
    async with db_sessions() as session:
        group = ProjectGroup(user_id="user-a", name="Casa")
        room = Habitacion(user_id="user-a", nombre="Sala", ancho=4, alto=2.5, profundidad=3)
        session.add_all([group, room])
        await session.commit()
        assert group.created_at is not None

    async with db_sessions() as session:
        stored = await session.scalar(select(Habitacion))
    assert (stored.group_id, stored.description, stored.derived_from) == (None, None, None)
