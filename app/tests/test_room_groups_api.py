"""Proyectos (salas) dentro de grupos: POST /rooms con groupId y description, PATCH /rooms/{id} y los
campos nuevos de GET /rooms/{id} y GET /spaces (CONTRATO-GRUPOS.md)."""

import pytest

from app.tests.test_groups_api import GROUPS, group_id
from app.tests.test_room import L_SHAPE
from app.tests.test_rooms_api import ROOMS, as_json, created_id

pytestmark = pytest.mark.anyio

SECRET_TEXT = "TEXTO-PRIVADO-DEL-USUARIO"


async def create_in(client, **extra):
    body = {"name": "Prueba 1", "vertices": as_json(L_SHAPE), "heightM": 2.5, **extra}
    return await client.post(ROOMS, json=body)


async def group_counts(client):
    return {g["id"]: g["projectCount"] for g in (await client.get(GROUPS)).json()["groups"]}


# --- Crear dentro de un grupo -------------------------------------------------------------------


async def test_create_a_project_inside_a_group_with_a_description(user_a, db_sessions):
    gid = await group_id(user_a)

    response = await create_in(user_a, groupId=gid, description="  Sofá contra la pared  ")

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["groupId"], body["description"]) == (gid, "Sofá contra la pared")
    assert await group_counts(user_a) == {gid: 1}


async def test_snake_case_group_id_is_accepted(user_a, db_sessions):
    gid = await group_id(user_a)

    response = await create_in(user_a, group_id=gid)

    assert response.json()["groupId"] == gid


async def test_a_project_without_group_keeps_the_old_answer(user_a, db_sessions):
    response = await create_in(user_a)

    assert set(response.json()) == {"id", "name", "vertices", "heightM", "areaM2"}


async def test_creating_in_a_foreign_or_missing_group_is_404_and_stores_nothing(user_a, user_b, db_sessions):
    foreign = await group_id(user_b)

    assert (await create_in(user_a, groupId=foreign)).status_code == 404
    assert (await create_in(user_a, groupId=99999)).status_code == 404
    spaces = (await user_a.get("/api/v1/spaces")).json()
    assert spaces["habitaciones"] == []


# --- PATCH /rooms/{id} --------------------------------------------------------------------------


async def test_rename_describe_and_move_a_project(user_a, db_sessions):
    first = await group_id(user_a, name="Casa")
    second = await group_id(user_a, name="Apartamento")
    room_id = await created_id(user_a)

    moved = await user_a.patch(f"{ROOMS}/{room_id}", json={"groupId": first})
    renamed = await user_a.patch(f"{ROOMS}/{room_id}", json={"name": "  Prueba 3 ", "description": "Cama"})
    moved_again = await user_a.patch(f"{ROOMS}/{room_id}", json={"groupId": second})

    assert moved.json()["groupId"] == first
    assert (renamed.json()["name"], renamed.json()["description"]) == ("Prueba 3", "Cama")
    assert moved_again.json()["groupId"] == second
    assert await group_counts(user_a) == {first: 0, second: 1}


async def test_group_id_null_takes_a_project_out_of_its_group(user_a, db_sessions):
    gid = await group_id(user_a)
    room_id = (await create_in(user_a, groupId=gid, description="Algo")).json()["id"]

    response = await user_a.patch(f"{ROOMS}/{room_id}", json={"groupId": None, "description": ""})

    assert response.status_code == 200
    assert "groupId" not in response.json() and "description" not in response.json()
    assert await group_counts(user_a) == {gid: 0}


async def test_patch_does_not_touch_shape_height_or_furniture(user_a, db_sessions):
    room_id = await created_id(user_a)
    before = (await user_a.get(f"{ROOMS}/{room_id}")).json()

    after = (await user_a.patch(f"{ROOMS}/{room_id}", json={"name": "Otra"})).json()

    assert {k: after[k] for k in ("vertices", "heightM", "areaM2")} == {
        k: before[k] for k in ("vertices", "heightM", "areaM2")
    }
    assert (await user_a.get(f"{ROOMS}/{room_id}/items")).json() == {"items": []}


async def test_patch_rejects_an_empty_body_and_invalid_fields_without_echo(user_a, db_sessions):
    room_id = await created_id(user_a)

    responses = [
        await user_a.patch(f"{ROOMS}/{room_id}", json={}),
        await user_a.patch(f"{ROOMS}/{room_id}", json={"name": None}),
        await user_a.patch(f"{ROOMS}/{room_id}", json={"name": "x" * 121}),
        await user_a.patch(f"{ROOMS}/{room_id}", json={"name": f"<i>{SECRET_TEXT}</i>"}),
        await user_a.patch(f"{ROOMS}/{room_id}", json={"description": SECRET_TEXT + "x" * 1000}),
        await user_a.patch(f"{ROOMS}/{room_id}", json={"groupId": 0}),
    ]

    assert [r.status_code for r in responses] == [422] * 6
    assert all(SECRET_TEXT not in r.text for r in responses)
    assert (await user_a.patch(f"{ROOMS}/{room_id}", json={"name": "x" * 120})).status_code == 200


async def test_patching_a_foreign_room_or_into_a_foreign_group_is_404(user_a, user_b, db_sessions):
    room_a = await created_id(user_a)
    room_b = await created_id(user_b)
    group_b = await group_id(user_b)

    assert (await user_a.patch(f"{ROOMS}/{room_b}", json={"name": "Mía"})).status_code == 404
    assert (await user_a.patch(f"{ROOMS}/{room_a}", json={"groupId": group_b})).status_code == 404
    assert (await user_a.patch(f"{ROOMS}/99999", json={"name": "Mía"})).status_code == 404
    assert "groupId" not in (await user_a.get(f"{ROOMS}/{room_a}")).json()


async def test_patch_needs_a_session(client, db_sessions):
    assert (await client.patch(f"{ROOMS}/1", json={"name": "x"})).status_code == 401


# --- Lecturas -----------------------------------------------------------------------------------


async def test_get_room_returns_the_group_and_description(user_a, db_sessions):
    gid = await group_id(user_a)
    room_id = (await create_in(user_a, groupId=gid, description="Algo")).json()["id"]

    body = (await user_a.get(f"{ROOMS}/{room_id}")).json()

    assert (body["groupId"], body["description"]) == (gid, "Algo")


async def test_get_spaces_adds_group_id_and_description_to_each_room(user_a, db_sessions):
    gid = await group_id(user_a)
    await create_in(user_a, groupId=gid, description="Algo")
    await create_in(user_a, name="Sin grupo")

    rooms = (await user_a.get("/api/v1/spaces")).json()["habitaciones"]

    assert [(r["nombre"], r["group_id"], r["description"]) for r in rooms] == [
        ("Prueba 1", gid, "Algo"),
        ("Sin grupo", None, None),
    ]
    expected_keys = {"id", "nombre", "ancho", "alto", "profundidad", "muebles", "group_id", "description"}
    assert set(rooms[0]) == expected_keys


async def test_a_project_without_group_still_works_with_items_and_vertices(user_a, db_sessions):
    room_id = await created_id(user_a)

    moved = await user_a.patch(f"{ROOMS}/{room_id}/vertices/0", json={"x": 0.5, "y": 0})
    items = await user_a.get(f"{ROOMS}/{room_id}/items")

    assert (moved.status_code, items.status_code) == (200, 200)
