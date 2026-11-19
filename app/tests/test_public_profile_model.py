"""Modelo PublicProfile, migración de arranque de la 007 y consultas de app/services/public_profile.py."""

import re

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.database.connection import PUBLIC_PROFILE_STATEMENTS, add_public_profile_statements
from app.models.space import Habitacion, ProjectPublication, PublicProfile
from app.services import public_profile
from app.services.project_publication import PUBLIC_CODE_PATTERN
from app.tests.test_room import RecordingConnection

pytestmark = pytest.mark.anyio

DOCUMENT = {"format": "habita-project", "version": 1}


async def add_publication(session, user_id, number, public=True):
    room = Habitacion(user_id=user_id, nombre=f"Sala {number}", ancho=4, alto=2.5, profundidad=3)
    session.add(room)
    await session.flush()
    row = ProjectPublication(
        code=f"{number:016d}",
        user_id=user_id,
        room_id=room.id,
        public=public,
        title=f"Sala {number}",
        document=DOCUMENT,
    )
    session.add(row)
    await session.flush()
    return row


async def test_the_startup_migration_on_postgresql_adds_index_rls_and_anonymous_profiles():
    conn = RecordingConnection("postgresql")

    await add_public_profile_statements(conn)

    assert conn.statements == list(PUBLIC_PROFILE_STATEMENTS)
    joined = " ".join(conn.statements)
    assert "ix_publicaciones_proyecto_autor" in joined
    assert "(user_id, id DESC) WHERE publica" in joined
    assert "ALTER TABLE perfiles_publicos ENABLE ROW LEVEL SECURITY" in conn.statements
    # El relleno solo pone user_id y código: el nombre visible queda NULL (anónimo), nunca usuarios.nombre.
    fill = conn.statements[-1]
    assert fill.startswith("INSERT INTO perfiles_publicos (user_id, codigo) ")
    assert "usuarios" not in fill and "nombre" not in fill
    assert "ON CONFLICT DO NOTHING" in fill
    assert "POLICY" not in joined.upper() and "FORCE" not in joined.upper()


async def test_the_startup_migration_does_nothing_on_sqlite():
    conn = RecordingConnection("sqlite")

    await add_public_profile_statements(conn)

    assert conn.statements == []


async def test_startup_runs_the_profile_migration(monkeypatch):
    from app import main

    calls = []

    async def fake_migration(conn):
        calls.append(conn.dialect.name)

    monkeypatch.setattr(main, "add_public_profile_statements", fake_migration)
    async with main.lifespan(main.app):
        pass

    # conftest.py apunta DATABASE_URL a un SQLite en memoria: nunca toca una base real.
    assert calls == ["sqlite"]


async def test_the_table_has_the_migration_names(db_sessions):
    def describe(sync_conn):
        inspector = inspect(sync_conn)
        return (
            {column["name"] for column in inspector.get_columns("perfiles_publicos")},
            {index["name"] for index in inspector.get_indexes("publicaciones_proyecto")},
        )

    async with db_sessions() as session:
        connection = await session.connection()
        columns, publication_indexes = await connection.run_sync(describe)

    assert columns == {"id", "user_id", "codigo", "nombre_visible", "creado_en", "actualizado_en"}
    assert "ix_publicaciones_proyecto_autor" in publication_indexes


async def test_one_profile_per_account_unique_codes_and_the_name_check(db_sessions):
    async with db_sessions() as session:
        session.add(PublicProfile(user_id="user-a", code="A" * 16))
        await session.commit()

    cases = (
        ({"user_id": "user-a", "code": "B" * 16}, "user_id"),
        ({"user_id": "user-b", "code": "A" * 16}, "codigo"),
        (
            {"user_id": "user-c", "code": "C" * 16, "display_name": ""},
            "ck_perfiles_publicos_nombre_visible_len",
        ),
    )
    for values, constraint in cases:
        async with db_sessions() as session:
            session.add(PublicProfile(**values))
            with pytest.raises(IntegrityError, match=constraint):
                await session.commit()

    async with db_sessions() as session:
        session.add(PublicProfile(user_id="user-d", code="D" * 16, display_name="x" * 40))
        await session.commit()
        stored = await session.scalar(select(PublicProfile).where(PublicProfile.user_id == "user-a"))
    assert stored.display_name is None


async def test_ensure_profile_creates_one_anonymous_profile_and_then_reuses_it(db_sessions):
    async with db_sessions() as session:
        first = await public_profile.ensure_profile(session, "user-a")
        await session.commit()
        code = first.code

    async with db_sessions() as session:
        again = await public_profile.ensure_profile(session, "user-a")
        await session.commit()
        count = len(list(await session.scalars(select(PublicProfile))))

    assert re.fullmatch(PUBLIC_CODE_PATTERN, code)
    assert first.display_name is None
    assert again.code == code
    assert count == 1


async def test_set_display_name_renames_without_changing_the_code(db_sessions):
    async with db_sessions() as session:
        profile = await public_profile.ensure_profile(session, "user-a")
        await session.commit()
        code = profile.code
        public_profile.set_display_name(profile, "Ana Planos")
        await session.commit()

    async with db_sessions() as session:
        stored = await public_profile.profile_by_code(session, code)
    assert stored.display_name == "Ana Planos"
    assert stored.user_id == "user-a"


async def test_featured_publications_take_the_newest_public_one_per_author(db_sessions):
    async with db_sessions() as session:
        a1 = await add_publication(session, "user-a", 1)
        b1 = await add_publication(session, "user-b", 2)
        a2 = await add_publication(session, "user-a", 3)
        await add_publication(session, "user-a", 4, public=False)
        await add_publication(session, "user-c", 5, public=False)
        await session.commit()

        featured = await public_profile.featured_publications(session, 1, 24)
        counts = await public_profile.public_counts(session, ["user-a", "user-b", "user-c"])

    assert [row.id for row in featured] == [a2.id, b1.id]
    assert a1.id not in [row.id for row in featured]
    assert counts == {"user-a": 2, "user-b": 1}


async def test_featured_publications_page_by_author(db_sessions):
    async with db_sessions() as session:
        for number in range(25):
            await add_publication(session, f"user-{number}", number + 1)
        await session.commit()

        first = await public_profile.featured_publications(session, 1, 24)
        second = await public_profile.featured_publications(session, 2, 24)

    assert len(first) == 25  # 24 + la que indica que hay otra página
    assert [row.user_id for row in second] == ["user-0"]


async def test_profile_publications_list_only_the_public_ones_of_that_account(db_sessions):
    async with db_sessions() as session:
        a1 = await add_publication(session, "user-a", 1)
        await add_publication(session, "user-b", 2)
        await add_publication(session, "user-a", 3, public=False)
        a4 = await add_publication(session, "user-a", 4)
        await session.commit()

        rows = await public_profile.profile_publications(session, "user-a", 1, 24)
        profiles = await public_profile.profiles_of(session, [])

    assert [row.id for row in rows] == [a4.id, a1.id]
    assert profiles == {}
