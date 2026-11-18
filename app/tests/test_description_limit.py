"""Límite de 500 caracteres al escribir la descripción de un proyecto y la de su publicación.

Solo se valida lo que llega (POST/PATCH/PUT): las descripciones antiguas más largas se siguen guardando y
mostrando, sin migración, y solo hay que recortarlas al editarlas. Los grupos no cambian (1000).
"""

import json

import pytest

from app.models.space import MAX_DESCRIPTION_LENGTH, Habitacion, ProjectPublication
from app.schemas.text import MAX_PROJECT_DESCRIPTION_LENGTH, optional_description
from app.tests.test_groups_api import create_group
from app.tests.test_project_import import VALID
from app.tests.test_room_groups_api import create_in
from app.tests.test_room_import_routes import send
from app.tests.test_rooms_api import ROOMS, created_id
from app.tests.test_social import CATALOG, publication_url, publish, stored_publications, undo_store  # noqa: F401

pytestmark = pytest.mark.anyio

SECRET_TEXT = "TEXTO-PRIVADO-DEL-USUARIO"
LIMIT = MAX_PROJECT_DESCRIPTION_LENGTH
# Una descripción antigua, guardada antes del límite: más de 500 y dentro del CHECK de la base (1000).
OLD_LONG = "Descripción antigua. " + "x" * 779


def too_long():
    """501 caracteres que empiezan por un texto reconocible, para comprobar que no hay eco."""
    return SECRET_TEXT + "x" * (LIMIT + 1 - len(SECRET_TEXT))


async def set_room_description(db_sessions, room_id, text):
    async with db_sessions() as session:
        row = await session.get(Habitacion, room_id)
        row.description = text
        await session.commit()


async def stored_room_description(db_sessions, room_id):
    async with db_sessions() as session:
        return (await session.get(Habitacion, room_id)).description


def assert_422_without_echo(response):
    assert response.status_code == 422, response.text
    assert SECRET_TEXT not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
        assert error["loc"][-1] == "description"
        assert f"como mucho {LIMIT} caracteres" in error["msg"]


# --- La regla -----------------------------------------------------------------------------------------


def test_the_project_limit_is_500_and_the_database_limit_stays_1000():
    assert LIMIT == 500
    assert MAX_DESCRIPTION_LENGTH == 1000
    assert len(OLD_LONG) == 800
    assert len(too_long()) == LIMIT + 1


def test_the_limit_counts_after_trimming_and_keeps_line_breaks():
    assert optional_description("  " + "x" * LIMIT + "\n ", LIMIT) == "x" * LIMIT
    assert optional_description("a\n" + "x" * (LIMIT - 2), LIMIT) == "a\n" + "x" * (LIMIT - 2)
    with pytest.raises(ValueError, match="como mucho 500"):
        optional_description("x" * (LIMIT + 1), LIMIT)


# --- Proyecto: crear y editar -------------------------------------------------------------------------


async def test_creating_a_project_accepts_500_and_rejects_501_without_echo(user_a, db_sessions):
    accepted = await create_in(user_a, description="x" * LIMIT)
    rejected = await create_in(user_a, description=too_long())

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["description"] == "x" * LIMIT
    assert_422_without_echo(rejected)
    assert len((await user_a.get("/api/v1/spaces")).json()["habitaciones"]) == 1


async def test_editing_accepts_500_and_rejects_501_leaving_it_unchanged(user_a, db_sessions):
    room_id = await created_id(user_a)
    await user_a.patch(f"{ROOMS}/{room_id}", json={"description": "Cama junto a la ventana"})

    rejected = await user_a.patch(f"{ROOMS}/{room_id}", json={"description": too_long()})
    unchanged = await stored_room_description(db_sessions, room_id)
    accepted = await user_a.patch(f"{ROOMS}/{room_id}", json={"description": "y" * LIMIT})

    assert_422_without_echo(rejected)
    assert unchanged == "Cama junto a la ventana"
    assert accepted.status_code == 200
    assert accepted.json()["description"] == "y" * LIMIT


# --- Proyecto: descripciones antiguas más largas ------------------------------------------------------


async def test_an_old_long_description_is_still_shown(user_a, db_sessions):
    room_id = await created_id(user_a)
    await set_room_description(db_sessions, room_id, OLD_LONG)

    detail = (await user_a.get(f"{ROOMS}/{room_id}")).json()
    (listed,) = (await user_a.get("/api/v1/spaces")).json()["habitaciones"]

    assert detail["description"] == OLD_LONG
    assert listed["description"] == OLD_LONG


async def test_editing_other_fields_keeps_an_old_long_description(user_a, db_sessions):
    room_id = await created_id(user_a)
    await set_room_description(db_sessions, room_id, OLD_LONG)

    renamed = await user_a.patch(f"{ROOMS}/{room_id}", json={"name": "Prueba 2"})
    moved = await user_a.patch(f"{ROOMS}/{room_id}", json={"groupId": None})

    assert renamed.status_code == moved.status_code == 200
    assert renamed.json()["description"] == moved.json()["description"] == OLD_LONG
    assert await stored_room_description(db_sessions, room_id) == OLD_LONG


async def test_an_old_long_description_must_be_trimmed_to_be_saved_again(user_a, db_sessions):
    room_id = await created_id(user_a)
    await set_room_description(db_sessions, room_id, OLD_LONG)

    same = await user_a.patch(f"{ROOMS}/{room_id}", json={"description": OLD_LONG})
    trimmed = await user_a.patch(f"{ROOMS}/{room_id}", json={"description": OLD_LONG[:LIMIT]})

    assert same.status_code == 422
    assert trimmed.status_code == 200
    assert await stored_room_description(db_sessions, room_id) == OLD_LONG[:LIMIT]


# --- Publicación: mismo límite ------------------------------------------------------------------------


async def test_publishing_accepts_500_and_rejects_501_without_echo(user_a, db_sessions, undo_store):  # noqa: F811
    room_id = await created_id(user_a)

    rejected = await publish(user_a, room_id, description=too_long())
    nothing_stored = await stored_publications(db_sessions)
    accepted = await publish(user_a, room_id, description="z" * LIMIT)

    assert_422_without_echo(rejected)
    assert nothing_stored == []
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["description"] == "z" * LIMIT


async def test_an_old_long_publication_description_is_shown_and_must_be_trimmed_to_republish(
    user_a,
    user_b,
    db_sessions,
    undo_store,  # noqa: F811
):
    room_id = await created_id(user_a)
    assert (await publish(user_a, room_id, description="Corta")).status_code == 201
    (stored,) = await stored_publications(db_sessions)
    async with db_sessions() as session:
        row = await session.get(ProjectPublication, stored.id)
        row.description = OLD_LONG
        await session.commit()

    status = (await user_a.get(publication_url(room_id))).json()
    card = (await user_b.get(CATALOG, params={"page": 1})).json()["items"][0]
    republished_as_is = await publish(user_a, room_id, description=OLD_LONG)
    republished_trimmed = await publish(user_a, room_id, description=OLD_LONG[:LIMIT])

    assert status["publication"]["description"] == OLD_LONG
    assert card["description"] == OLD_LONG
    assert republished_as_is.status_code == 422
    assert republished_trimmed.status_code == 200, republished_trimmed.text
    assert republished_trimmed.json()["description"] == OLD_LONG[:LIMIT]


# --- Importación: el archivo no lleva descripción ----------------------------------------------------


async def test_an_imported_project_never_gets_a_description(user_a, db_sessions):
    with_description = {**VALID, "description": "x" * 10}

    rejected = await send(user_a, with_description)
    imported = await send(user_a, VALID)

    assert rejected.status_code == 422
    assert imported.status_code == 201, imported.text
    assert "description" not in imported.json()["room"]
    assert "x" * 10 not in json.dumps(rejected.json())


# --- Grupos: sin cambios ------------------------------------------------------------------------------


async def test_group_descriptions_keep_the_1000_limit(user_a, db_sessions):
    over_project_limit = await create_group(user_a, name="Casa", description="x" * (LIMIT + 1))
    at_group_limit = await create_group(user_a, name="Piso", description="x" * MAX_DESCRIPTION_LENGTH)
    over_group_limit = await create_group(user_a, name="Finca", description="x" * 1001)

    assert (over_project_limit.status_code, at_group_limit.status_code) == (201, 201)
    assert over_group_limit.status_code == 422
