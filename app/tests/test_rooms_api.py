import pytest
from sqlalchemy import select

from app.models.space import Habitacion
from app.tests.test_room import L_SHAPE, NOTCHED_ROOM

pytestmark = pytest.mark.anyio

ROOMS = "/api/v1/spaces/rooms"


def as_json(vertices):
    return [{"x": x, "y": y} for x, y in vertices]


async def create(client, vertices=L_SHAPE, height_m=2.6, name="Sala en L"):
    return await client.post(ROOMS, json={"name": name, "vertices": as_json(vertices), "heightM": height_m})


async def created_id(client, **kwargs):
    response = await create(client, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- Create and read -----------------------------------------------------------------------------


async def test_create_an_l_shaped_room(user_a, db_sessions):
    response = await create(user_a)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "name", "vertices", "heightM", "areaM2"}
    assert body["name"] == "Sala en L"
    assert body["vertices"] == as_json(L_SHAPE)
    assert body["heightM"] == 2.6
    assert body["areaM2"] == 16


async def test_create_stores_the_polygon_and_its_bounding_box(user_a, db_sessions):
    room_id = await created_id(user_a)

    async with db_sessions() as session:
        row = await session.get(Habitacion, room_id)
    assert row.user_id == "user-a"
    assert row.vertices == as_json(L_SHAPE)
    assert (row.ancho, row.profundidad, row.alto) == (4, 5, 2.6)


async def test_get_returns_the_stored_room(user_a, db_sessions):
    room_id = await created_id(user_a)

    response = await user_a.get(f"{ROOMS}/{room_id}")

    assert response.status_code == 200
    assert response.json()["vertices"] == as_json(L_SHAPE)


async def test_get_a_room_from_analyze_returns_its_rectangle(user_a, db_sessions):
    async with db_sessions() as session:
        row = Habitacion(user_id="user-a", nombre="Escaneada", ancho=4, alto=3, profundidad=5)
        session.add(row)
        await session.commit()

    response = await user_a.get(f"{ROOMS}/{row.id}")

    assert response.status_code == 200
    assert response.json()["vertices"] == as_json([(0, 0), (4, 0), (4, 5), (0, 5)])
    assert response.json()["areaM2"] == 20


@pytest.mark.parametrize(
    "vertices",
    [
        [(0, 0), (1, 0)],  # fewer than 3
        [(0, 0), (1, 0), (2, 0)],  # collinear, no area
        [(0, 0), (2, 2), (2, 0), (0, 2)],  # bow tie, crosses itself
    ],
)
async def test_create_rejects_an_invalid_polygon(user_a, db_sessions, vertices):
    response = await create(user_a, vertices=vertices)

    assert response.status_code == 422, response.text
    async with db_sessions() as session:
        assert (await session.scalars(select(Habitacion))).all() == []


@pytest.mark.parametrize("height_m", [0, -1, 21])
async def test_create_rejects_an_invalid_height(user_a, db_sessions, height_m):
    assert (await create(user_a, height_m=height_m)).status_code == 422


async def test_create_rejects_a_blank_name(user_a, db_sessions):
    assert (await create(user_a, name="   ")).status_code == 422


async def test_snake_case_keys_are_accepted_too(user_a, db_sessions):
    response = await user_a.post(ROOMS, json={"name": "Sala", "vertices": as_json(L_SHAPE), "height_m": 2.4})

    assert response.status_code == 201
    assert response.json()["heightM"] == 2.4


# --- Acceptance: real 21-vertex room ----------------------------------------------------------------


async def test_real_21_vertex_room(user_a, db_sessions):
    response = await create(user_a, vertices=NOTCHED_ROOM, height_m=2.5, name="Sala real")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["areaM2"] == 28.75
    # (3.5, 5) is collinear and redundant, but it is accepted and kept.
    assert len(body["vertices"]) == 21
    assert {"x": 3.5, "y": 5} in body["vertices"]


async def test_real_room_rejects_a_vertex_move_that_crosses_another_edge(user_a, db_sessions):
    room_id = await created_id(user_a, vertices=NOTCHED_ROOM, height_m=2.5)

    # Vertex 12 is (4.5, 3); moving it to (1, 4) makes its edges cross the walls around (2.5, 3)-(2.5, 4).
    response = await user_a.patch(f"{ROOMS}/{room_id}/vertices/12", json={"x": 1, "y": 4})

    assert response.status_code == 422, response.text
    assert "intersects itself" in response.json()["detail"]
    assert len((await user_a.get(f"{ROOMS}/{room_id}")).json()["vertices"]) == 21


# --- Editing vertices ----------------------------------------------------------------------------


async def test_move_a_vertex(user_a, db_sessions):
    room_id = await created_id(user_a)

    response = await user_a.patch(f"{ROOMS}/{room_id}/vertices/1", json={"x": 5, "y": 0})

    assert response.status_code == 200, response.text
    assert response.json()["vertices"][1] == {"x": 5, "y": 0}
    assert response.json()["vertices"][0] == {"x": 0, "y": 0}
    assert response.json()["areaM2"] == 17.5


async def test_vertex_edits_are_read_back_by_a_new_session(user_a, db_sessions):
    room_id = await created_id(user_a)

    await user_a.patch(f"{ROOMS}/{room_id}/vertices/1", json={"x": 5, "y": 0})

    # A fresh session cannot return a cached object: this proves the JSON change reached the database.
    async with db_sessions() as session:
        row = await session.get(Habitacion, room_id)
    assert row.vertices[1] == {"x": 5, "y": 0}
    assert row.ancho == 5


async def test_move_that_breaks_the_polygon_is_rejected_and_nothing_changes(user_a, db_sessions):
    room_id = await created_id(user_a)

    # (0, 0) -> (3, 6) makes the first edge cross the edge (4, 3)-(2, 3).
    response = await user_a.patch(f"{ROOMS}/{room_id}/vertices/0", json={"x": 3, "y": 6})

    assert response.status_code == 422, response.text
    assert "intersects itself" in response.json()["detail"]
    async with db_sessions() as session:
        row = await session.get(Habitacion, room_id)
    assert row.vertices == as_json(L_SHAPE)


async def test_move_with_an_out_of_range_index_is_422(user_a, db_sessions):
    room_id = await created_id(user_a)

    assert (await user_a.patch(f"{ROOMS}/{room_id}/vertices/6", json={"x": 1, "y": 1})).status_code == 422
    assert (await user_a.patch(f"{ROOMS}/{room_id}/vertices/-1", json={"x": 1, "y": 1})).status_code == 422


async def test_move_a_vertex_of_a_room_from_analyze_stores_its_polygon(user_a, db_sessions):
    async with db_sessions() as session:
        row = Habitacion(user_id="user-a", nombre="Escaneada", ancho=4, alto=3, profundidad=5)
        session.add(row)
        await session.commit()

    response = await user_a.patch(f"{ROOMS}/{row.id}/vertices/2", json={"x": 3, "y": 5})

    assert response.status_code == 200
    async with db_sessions() as session:
        stored = await session.get(Habitacion, row.id)
    assert stored.vertices == as_json([(0, 0), (4, 0), (3, 5), (0, 5)])


async def test_insert_a_vertex(user_a, db_sessions):
    room_id = await created_id(user_a)

    response = await user_a.post(f"{ROOMS}/{room_id}/vertices", json={"afterIndex": 0, "x": 2, "y": -1})

    assert response.status_code == 200, response.text
    vertices = response.json()["vertices"]
    assert len(vertices) == 7
    assert vertices[1] == {"x": 2, "y": -1}


async def test_insert_that_breaks_the_polygon_is_422(user_a, db_sessions):
    room_id = await created_id(user_a)

    response = await user_a.post(f"{ROOMS}/{room_id}/vertices", json={"afterIndex": 0, "x": 2, "y": 4})

    assert response.status_code == 422


async def test_remove_a_vertex(user_a, db_sessions):
    room_id = await created_id(user_a, vertices=[(0, 0), (4, 0), (4, 4), (2, 5), (0, 4)])

    response = await user_a.delete(f"{ROOMS}/{room_id}/vertices/3")

    assert response.status_code == 200, response.text
    assert response.json()["vertices"] == as_json([(0, 0), (4, 0), (4, 4), (0, 4)])


async def test_a_room_keeps_at_least_3_vertices(user_a, db_sessions):
    room_id = await created_id(user_a, vertices=[(0, 0), (4, 0), (0, 3)])

    response = await user_a.delete(f"{ROOMS}/{room_id}/vertices/0")

    assert response.status_code == 422
    assert "at least 3" in response.json()["detail"]


# --- Authentication and ownership ------------------------------------------------------------------


async def test_every_room_endpoint_needs_a_token(client, db_sessions):
    calls = [
        client.post(ROOMS, json={"name": "x", "vertices": as_json(L_SHAPE), "heightM": 2.6}),
        client.get(f"{ROOMS}/1"),
        client.patch(f"{ROOMS}/1/vertices/0", json={"x": 0, "y": 0}),
        client.post(f"{ROOMS}/1/vertices", json={"afterIndex": 0, "x": 0, "y": 0}),
        client.delete(f"{ROOMS}/1/vertices/0"),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


async def test_another_account_gets_404_and_cannot_edit(user_a, user_b, db_sessions):
    room_id = await created_id(user_a)

    assert (await user_b.get(f"{ROOMS}/{room_id}")).status_code == 404
    assert (await user_b.patch(f"{ROOMS}/{room_id}/vertices/1", json={"x": 5, "y": 0})).status_code == 404
    assert (
        await user_b.post(f"{ROOMS}/{room_id}/vertices", json={"afterIndex": 0, "x": 2, "y": -1})
    ).status_code == 404
    assert (await user_b.delete(f"{ROOMS}/{room_id}/vertices/0")).status_code == 404

    assert (await user_a.get(f"{ROOMS}/{room_id}")).json()["vertices"] == as_json(L_SHAPE)


async def test_unknown_room_is_404(user_a, db_sessions):
    assert (await user_a.get(f"{ROOMS}/999")).status_code == 404
