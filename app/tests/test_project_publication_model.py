"""Modelo ProjectPublication y migración de arranque de la 006 (publicaciones de la red social)."""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.database.connection import PROJECT_PUBLICATION_STATEMENTS, add_project_publication_statements
from app.models.space import MAX_PUBLICATION_DOCUMENT_CHARS, Habitacion, ProjectPublication
from app.tests.test_room import RecordingConnection

pytestmark = pytest.mark.anyio

DOCUMENT = {"format": "habita-project", "version": 1}


def publication(room_id, code="A" * 16, **changes):
    values = {"code": code, "user_id": "user-a", "room_id": room_id, "title": "Sala", "document": DOCUMENT}
    values.update(changes)
    return ProjectPublication(**values)


async def new_room(session, name="Sala"):
    room = Habitacion(user_id="user-a", nombre=name, ancho=4, alto=2.5, profundidad=3)
    session.add(room)
    await session.flush()
    return room.id


async def test_the_startup_migration_on_postgresql_adds_indexes_and_rls():
    conn = RecordingConnection("postgresql")

    await add_project_publication_statements(conn)

    assert conn.statements == list(PROJECT_PUBLICATION_STATEMENTS)
    assert conn.statements[-1] == "ALTER TABLE publicaciones_proyecto ENABLE ROW LEVEL SECURITY"
    joined = " ".join(conn.statements)
    assert "ix_publicaciones_proyecto_user_id" in joined
    assert "(id DESC) WHERE publica" in joined
    # Sin políticas ni FORCE: igual que grupos_proyecto, el backend (dueño) sigue leyendo y escribiendo.
    assert "POLICY" not in joined.upper()
    assert "FORCE" not in joined.upper()


async def test_the_startup_migration_does_nothing_on_sqlite():
    conn = RecordingConnection("sqlite")

    await add_project_publication_statements(conn)

    assert conn.statements == []


async def test_startup_runs_the_publication_migration(monkeypatch):
    from app import main

    calls = []

    async def fake_migration(conn):
        calls.append(conn.dialect.name)

    monkeypatch.setattr(main, "add_project_publication_statements", fake_migration)
    async with main.lifespan(main.app):
        pass

    # conftest.py apunta DATABASE_URL a un SQLite en memoria: nunca toca una base real.
    assert calls == ["sqlite"]


async def test_a_new_publication_is_private_with_no_copies(db_sessions):
    async with db_sessions() as session:
        session.add(publication(await new_room(session)))
        await session.commit()

    async with db_sessions() as session:
        stored = await session.scalar(select(ProjectPublication))
    assert stored.public is False
    assert stored.copies == 0
    assert stored.created_at is not None and stored.updated_at is not None
    assert stored.document == DOCUMENT


async def test_one_publication_per_project_and_unique_codes(db_sessions):
    async with db_sessions() as session:
        room_id = await new_room(session)
        other_id = await new_room(session, "Cocina")
        session.add(publication(room_id))
        await session.commit()

    async with db_sessions() as session:
        session.add(publication(room_id, code="B" * 16))
        with pytest.raises(IntegrityError, match="habitacion_id"):
            await session.commit()

    async with db_sessions() as session:
        session.add(publication(other_id))
        with pytest.raises(IntegrityError, match="codigo"):
            await session.commit()


async def test_create_all_builds_the_named_checks(db_sessions):
    async with db_sessions() as session:
        session.add(publication(await new_room(session), description="x" * 1000))
        await session.commit()

    cases = (
        ({"description": "x" * 1001}, "ck_publicaciones_proyecto_descripcion_len"),
        ({"copies": -1}, "ck_publicaciones_proyecto_copias"),
        (
            {"document": {"padding": "x" * MAX_PUBLICATION_DOCUMENT_CHARS}},
            "ck_publicaciones_proyecto_documento_len",
        ),
    )
    for number, (changes, constraint) in enumerate(cases):
        async with db_sessions() as session:
            room_id = await new_room(session, f"Sala {number}")
            session.add(publication(room_id, code=f"{number}" * 16, **changes))
            with pytest.raises(IntegrityError, match=constraint):
                await session.commit()


async def test_the_table_has_the_migration_names(db_sessions):
    def describe(sync_conn):
        inspector = inspect(sync_conn)
        table = "publicaciones_proyecto"
        return (
            {column["name"] for column in inspector.get_columns(table)},
            {index["name"] for index in inspector.get_indexes(table)},
            {key["referred_table"]: key["options"] for key in inspector.get_foreign_keys(table)},
        )

    async with db_sessions() as session:
        connection = await session.connection()
        columns, indexes, foreign_keys = await connection.run_sync(describe)

    assert columns == {
        "id",
        "codigo",
        "user_id",
        "habitacion_id",
        "publica",
        "titulo",
        "descripcion",
        "documento",
        "copias",
        "creada_en",
        "actualizada_en",
    }
    assert {"ix_publicaciones_proyecto_user_id", "ix_publicaciones_proyecto_catalogo"} <= indexes
    assert foreign_keys["habitaciones"].get("ondelete") == "CASCADE"
