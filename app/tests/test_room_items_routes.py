"""Rutas de objetos de la sala (CONTRATO-FASE0.md): POST, PATCH y DELETE /rooms/{id}/items."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.main import app
from app.models.space import MuebleSugerido
from app.services import photo_estimator
from app.services.room_objects import WALL_NEEDS_RECTANGLE
from app.services.room_undo import NOTHING_TO_UNDO, RoomUndoStore, get_room_undo_store
from app.tests.test_analyze_photo import stored, upload
from app.tests.test_photo_estimator import ESTIMATE, FakeModel
from app.tests.test_rooms_api import ROOMS, created_id
from app.utils import rate_limit

pytestmark = pytest.mark.anyio

RECTANGLE = [(0, 0), (4, 0), (4, 3), (0, 3)]
PICTURE = {"name": "Cuadro", "kind": "wall", "wall": "left", "widthCm": 60, "depthCm": 5, "heightCm": 40}
TABLE = {"name": "Mesa", "kind": "floor", "widthCm": 120, "depthCm": 80, "heightCm": 75}


@pytest.fixture
def undo_store():
    """Almacén de deshacer la foto limpio, el mismo para analyze-photo, photo-undo y estas rutas."""
    fresh = RoomUndoStore()
    app.dependency_overrides[get_room_undo_store] = lambda: fresh
    yield fresh
    app.dependency_overrides.pop(get_room_undo_store, None)


@pytest.fixture
def vision_model(monkeypatch):
    fake = FakeModel(json.dumps(ESTIMATE))
    monkeypatch.setattr(photo_estimator, "chat_completion", fake)
    return fake


def items_url(room_id, item_id=None):
    return f"{ROOMS}/{room_id}/items" + ("" if item_id is None else f"/{item_id}")


async def add(client, room_id, body):
    return await client.post(items_url(room_id), json=body)


async def rectangle_room(client):
    return await created_id(client, vertices=RECTANGLE, height_m=2.5)


# --- POST ---------------------------------------------------------------------------------------------


async def test_add_a_picture_to_the_left_wall(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)

    response = await add(user_a, room_id, PICTURE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"item", "wall", "adjusted"}
    assert (body["wall"], body["adjusted"]) == ("left", False)
    item = body["item"]
    assert item["name"] == "Cuadro"
    assert (item["x"], item["y"], item["rotation"]) == pytest.approx((0, 1.2, 0))
    assert (item["widthM"], item["depthM"], item["heightM"], item["elevationM"]) == pytest.approx(
        (0.05, 0.6, 0.4, 1.3)
    )
    listed = (await user_a.get(items_url(room_id))).json()["items"]
    assert listed == [item]


async def test_add_a_floor_object_in_an_l_room_but_not_a_wall_one(user_a, db_sessions, undo_store):
    room_id = await created_id(user_a)  # sala en L

    floor = await add(user_a, room_id, TABLE)
    wall = await add(user_a, room_id, PICTURE)

    assert floor.status_code == 201, floor.text
    assert floor.json()["adjusted"] is False
    assert wall.status_code == 422
    assert wall.json()["detail"] == WALL_NEEDS_RECTANGLE


@pytest.mark.parametrize(
    "changes",
    [{"name": "<img src=x onerror=robar()>"}, {"widthCm": 4.9}, {"heightCm": "75"}, {"depthCm": 400.5},
     {"kind": "floor"}, {"wall": None}, {"extra": "robar"}],
)
async def test_invalid_bodies_are_422_without_echo(user_a, db_sessions, undo_store, changes):
    room_id = await rectangle_room(user_a)

    response = await add(user_a, room_id, {**PICTURE, **changes})

    assert response.status_code == 422
    assert "robar" not in response.text
    assert all(set(error) == {"type", "loc", "msg"} for error in response.json()["detail"])
    assert await stored(db_sessions, room_id) == []


async def test_non_json_numbers_are_422(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    raw = json.dumps(PICTURE).replace('"widthCm": 60', '"widthCm": NaN')

    headers = {"content-type": "application/json"}
    response = await user_a.post(items_url(room_id), content=raw, headers=headers)

    assert response.status_code == 422


async def test_routes_need_a_token(client, db_sessions, undo_store):
    assert (await add(client, 1, PICTURE)).status_code == 401
    assert (await client.patch(items_url(1, 1), json={"name": "Otro"})).status_code == 401
    assert (await client.delete(items_url(1, 1))).status_code == 401


async def test_a_room_of_another_account_or_missing_is_404(user_a, user_b, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    item_id = (await add(user_a, room_id, TABLE)).json()["item"]["id"]
    message = f"No existe una habitación con id {room_id}."

    responses = [
        await add(user_b, room_id, PICTURE),
        await user_b.patch(items_url(room_id, item_id), json={"name": "Robado"}),
        await user_b.delete(items_url(room_id, item_id)),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
    assert {r.json()["detail"] for r in responses} == {message}
    assert (await add(user_a, 999, PICTURE)).status_code == 404
    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Mesa"]


async def test_a_full_room_is_409(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    async with db_sessions() as session:
        session.add_all(
            MuebleSugerido(
                habitacion_id=room_id, nombre=f"Caja {i}", ancho=0.05, alto=0.05, profundidad=0.05,
                posicion_x=0.1 + (i % 20) * 0.2, posicion_y=0, posicion_z=0.1 + (i // 20) * 0.2,
            )
            for i in range(200)
        )
        await session.commit()

    response = await add(user_a, room_id, TABLE)

    assert response.status_code == 409
    assert "200" in response.json()["detail"]


async def test_no_space_is_409(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)

    response = await add(user_a, room_id, {**TABLE, "widthCm": 400, "depthCm": 300, "heightCm": 30})
    again = await add(user_a, room_id, TABLE)

    assert response.status_code == 201
    assert again.status_code == 409
    assert again.json()["detail"] == "No hay sitio para «Mesa» en el suelo."


async def test_the_routes_spend_no_ai_quota(user_a, db_sessions, undo_store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 1)
    rate_limit.reset_all()
    room_id = await rectangle_room(user_a)

    statuses = [(await add(user_a, room_id, {**TABLE, "name": f"Mesa {i}", "widthCm": 50})).status_code
                for i in range(3)]

    assert statuses == [201, 201, 201]


# --- PATCH --------------------------------------------------------------------------------------------


async def test_a_colliding_move_is_adjusted(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    bed = (await add(user_a, room_id, {**TABLE, "name": "Cama", "widthCm": 160, "depthCm": 200})).json()
    table = (await add(user_a, room_id, {**TABLE, "widthCm": 60, "depthCm": 60})).json()

    response = await user_a.patch(
        items_url(room_id, table["item"]["id"]), json={"position": {"x": bed["item"]["x"], "y": 0}}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adjusted"] is True
    assert body["item"]["x"] == pytest.approx(1.6 + 0.01)


async def test_patch_slides_a_picture_and_resizes(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    picture = (await add(user_a, room_id, PICTURE)).json()["item"]

    moved = await user_a.patch(
        items_url(room_id, picture["id"]),
        json={
            "name": "Cuadro grande",
            "depthCm": 80,
            "position": {"x": 2, "y": 0.5, "elevationM": 1, "wall": "left"},
        },
    )

    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert (body["wall"], body["adjusted"]) == ("left", False)
    item = body["item"]
    assert (item["name"], item["x"], item["y"], item["depthM"], item["elevationM"]) == pytest.approx(
        ("Cuadro grande", 0, 0.5, 0.8, 1)
    )


async def test_patch_errors(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    other_id = await rectangle_room(user_a)
    item_id = (await add(user_a, room_id, TABLE)).json()["item"]["id"]

    empty = await user_a.patch(items_url(room_id, item_id), json={})
    elsewhere = await user_a.patch(items_url(other_id, item_id), json={"name": "Otra"})
    missing = await user_a.patch(items_url(room_id, 999), json={"name": "Otra"})
    too_tall = await user_a.patch(items_url(room_id, item_id), json={"heightCm": 300})

    assert empty.status_code == 422
    assert (elsewhere.status_code, missing.status_code) == (404, 404)
    assert elsewhere.json()["detail"] == f"No existe un objeto con id {item_id} en esta sala."
    assert too_tall.status_code == 409


# --- DELETE -------------------------------------------------------------------------------------------


async def test_delete_is_204_then_404(user_a, db_sessions, undo_store):
    room_id = await rectangle_room(user_a)
    item_id = (await add(user_a, room_id, TABLE)).json()["item"]["id"]

    first = await user_a.delete(items_url(room_id, item_id))
    second = await user_a.delete(items_url(room_id, item_id))

    assert (first.status_code, first.content) == (204, b"")
    assert second.status_code == 404
    assert await stored(db_sessions, room_id) == []


# --- Deshacer la foto nunca borra trabajo posterior: antes se deshace ese trabajo ---------------------------


async def photo_then(client, room_id):
    response = await upload(client, room_id)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_changes_after_a_photo_are_undone_before_the_photo(
    user_a, db_sessions, undo_store, vision_model
):
    room_id = await rectangle_room(user_a)
    items = await photo_then(user_a, room_id)
    added = await add(user_a, room_id, PICTURE)
    assert added.status_code == 201
    renamed = await user_a.patch(items_url(room_id, items[0]["id"]), json={"name": "Movido"})
    assert renamed.status_code == 200
    assert (await user_a.delete(items_url(room_id, added.json()["item"]["id"]))).status_code == 204

    undone = [(await user_a.post(f"{ROOMS}/{room_id}/undo")).json() for _ in range(4)]

    assert [body["undone"] for body in undone] == ["delete", "update", "add", "photo"]
    # Tras deshacer los tres cambios de objetos, la sala es exactamente la de la foto.
    assert [(i["name"], i["x"], i["y"]) for i in undone[2]["items"]] == [
        (i["name"], i["x"], i["y"]) for i in items
    ]
    assert undone[3]["items"] == []
    last = await user_a.post(f"{ROOMS}/{room_id}/undo")
    assert (last.status_code, last.json()["detail"]) == (409, NOTHING_TO_UNDO)


async def test_a_refused_change_keeps_the_photo_undo(user_a, db_sessions, undo_store, vision_model):
    room_id = await rectangle_room(user_a)
    await photo_then(user_a, room_id)

    assert (await add(user_a, room_id, {**TABLE, "heightCm": 300})).status_code == 409
    assert (await user_a.patch(items_url(room_id, 999), json={"name": "X"})).status_code == 404
    assert (await add(user_a, room_id, {**PICTURE, "widthCm": 1})).status_code == 422

    assert (await user_a.post(f"{ROOMS}/{room_id}/photo-undo")).status_code == 200


async def test_a_failed_commit_is_503_stores_nothing_and_keeps_the_photo_undo(
    user_a, db_sessions, undo_store, vision_model, monkeypatch
):
    room_id = await rectangle_room(user_a)
    before = await photo_then(user_a, room_id)

    async def failing_commit(self):
        raise OSError("connection lost")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_commit)
        response = await add(user_a, room_id, TABLE)

    assert response.status_code == 503
    assert "connection lost" not in response.text
    assert len(await stored(db_sessions, room_id)) == len(before)
    assert undo_store.depth("user-a", room_id) == 1
