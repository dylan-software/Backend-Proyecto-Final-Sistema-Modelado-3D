"""Deshacer la foto a 3D (CONTRATO-DESHACER-FOTO.md): el almacén de copias y POST /rooms/{id}/photo-undo."""

import dataclasses
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.main import app
from app.models.space import Habitacion, MuebleSugerido
from app.routers.spaces import NOTHING_TO_UNDO, get_photo_undo_store
from app.services import photo_estimator
from app.services.photo_undo import (
    MAX_UNDO_DEPTH,
    MAX_UNDO_ROOMS,
    FurnitureSnapshot,
    PhotoUndoStore,
    RoomSnapshot,
    photo_undo_store,
    restore_room,
    snapshot_room,
)
from app.tests.test_analyze_photo import bare_room, estimate_with, stored, stored_room, upload
from app.tests.test_photo_estimator import ESTIMATE, FakeModel
from app.tests.test_rooms_api import ROOMS, created_id
from app.utils import rate_limit

pytestmark = pytest.mark.anyio


def snap(tag):
    """Una copia distinguible por su alto."""
    return RoomSnapshot(vertices=None, ancho=4, profundidad=3, alto=tag, furniture=())


# --- Almacén ----------------------------------------------------------------------------------------


def test_defaults_follow_the_contract():
    assert (MAX_UNDO_ROOMS, MAX_UNDO_DEPTH) == (200, 5)
    assert (photo_undo_store.max_rooms, photo_undo_store.max_depth) == (200, 5)


def test_pop_returns_the_latest_copy_first():
    store = PhotoUndoStore()
    for tag in (1, 2, 3):
        store.push("user-a", 7, snap(tag))

    assert store.depth("user-a", 7) == 3
    assert [store.pop("user-a", 7).alto for _ in range(3)] == [3, 2, 1]
    assert store.pop("user-a", 7) is None
    assert store.depth("user-a", 7) == 0


def test_an_empty_stack_leaves_the_cache():
    store = PhotoUndoStore()
    store.push("user-a", 7, snap(1))

    store.pop("user-a", 7)

    assert len(store) == 0


def test_a_full_stack_drops_the_oldest_copy():
    store = PhotoUndoStore(depth=5)
    for tag in range(1, 8):
        store.push("user-a", 7, snap(tag))

    assert store.depth("user-a", 7) == 5
    # Se descartaron 1 y 2, las más antiguas; el orden del resto no cambia.
    assert [store.pop("user-a", 7).alto for _ in range(5)] == [7, 6, 5, 4, 3]


def test_keys_of_users_and_rooms_do_not_mix():
    store = PhotoUndoStore()
    store.push("user-a", 1, snap("a1"))
    store.push("user-b", 1, snap("b1"))
    store.push("user-a", 2, snap("a2"))

    assert store.pop("user-b", 2) is None
    assert store.pop("user-a", 1).alto == "a1"
    assert store.pop("user-a", 1) is None
    assert store.pop("user-b", 1).alto == "b1"
    assert store.pop("user-a", 2).alto == "a2"


def test_the_least_used_room_is_evicted():
    store = PhotoUndoStore(max_rooms=2)
    store.push("user-a", 1, snap(1))
    store.push("user-a", 2, snap(2))
    store.push("user-a", 1, snap(11))  # la sala 1 pasa a ser la más reciente

    store.push("user-a", 3, snap(3))

    assert len(store) == 2
    assert store.depth("user-a", 2) == 0  # expulsada
    assert (store.depth("user-a", 1), store.depth("user-a", 3)) == (2, 1)


def test_depth_does_not_refresh_a_room():
    store = PhotoUndoStore(max_rooms=2)
    store.push("user-a", 1, snap(1))
    store.push("user-a", 2, snap(2))

    store.depth("user-a", 1)
    store.push("user-a", 3, snap(3))

    assert store.depth("user-a", 1) == 0


def test_clear_forgets_everything():
    store = PhotoUndoStore()
    store.push("user-a", 1, snap(1))

    store.clear()

    assert (len(store), store.pop("user-a", 1)) == (0, None)


# --- Copia y restauración de una sala --------------------------------------------------------------


def room_row(vertices, furniture=()):
    row = Habitacion(user_id="user-a", nombre="Sala", ancho=5, profundidad=4, alto=2.6, vertices=vertices)
    row.muebles = [MuebleSugerido(**piece) for piece in furniture]
    return row


MESA = {
    "nombre": "Mesa",
    "ancho": 1.2,
    "alto": 0.75,
    "profundidad": 0.8,
    "posicion_x": 2,
    "posicion_y": 0,
    "posicion_z": 1.5,
}
SQUARE = [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 4}, {"x": 0, "y": 4}]


def test_the_snapshot_holds_values_not_rows():
    row = room_row([dict(v) for v in SQUARE], [MESA])

    copy = snapshot_room(row)

    assert copy.vertices == ((0, 0), (5, 0), (5, 4), (0, 4))
    assert (copy.ancho, copy.profundidad, copy.alto) == (5, 4, 2.6)
    assert copy.furniture == (FurnitureSnapshot(**MESA),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        copy.alto = 3
    # Cambiar la fila después no cambia la copia.
    row.vertices[0]["x"] = 99
    row.muebles[0].nombre = "Otra"
    assert copy.vertices[0] == (0, 0)
    assert copy.furniture[0].nombre == "Mesa"


def test_restore_puts_back_shape_and_furniture_with_new_lists():
    row = room_row([dict(v) for v in SQUARE], [MESA])
    copy = snapshot_room(row)
    old_vertices = row.vertices
    row.vertices = [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}]
    row.ancho, row.profundidad, row.alto = 4, 3, 2.5
    row.muebles = []

    restore_room(row, copy)

    assert row.vertices == SQUARE
    assert row.vertices is not old_vertices
    assert (row.ancho, row.profundidad, row.alto) == (5, 4, 2.6)
    (mesa,) = row.muebles
    assert {column: getattr(mesa, column) for column in MESA} == MESA


def test_a_room_without_shape_is_restored_without_vertices():
    row = room_row(None)
    copy = snapshot_room(row)
    row.vertices = list(SQUARE)

    restore_room(row, copy)

    assert (copy.vertices, row.vertices, row.muebles) == (None, None, [])


# --- POST /rooms/{id}/photo-undo ----------------------------------------------------------------------


@pytest.fixture
def store():
    """Almacén limpio para cada prueba, inyectado en analyze-photo y en photo-undo."""
    fresh = PhotoUndoStore()
    app.dependency_overrides[get_photo_undo_store] = lambda: fresh
    yield fresh
    app.dependency_overrides.pop(get_photo_undo_store, None)


@pytest.fixture
def model(monkeypatch):
    """El modelo con visión, sustituido; el estimador real sí se ejecuta."""
    fake = FakeModel(json.dumps(ESTIMATE))
    monkeypatch.setattr(photo_estimator, "chat_completion", fake)
    return fake


@pytest.fixture
def no_ai_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 1000)
    rate_limit.reset_all()


async def undo(client, room_id):
    return await client.post(f"{ROOMS}/{room_id}/photo-undo")


def room_with(width):
    return estimate_with(room={"width_m": width, "length_m": 3, "height_m": 2.5})


async def add_furniture(db_sessions, room_id, *pieces):
    async with db_sessions() as session:
        session.add_all(MuebleSugerido(habitacion_id=room_id, **piece) for piece in pieces)
        await session.commit()


def furniture_values(rows):
    return [{column: getattr(row, column) for column in MESA} for row in rows]


SILLA = {**MESA, "nombre": "Silla", "ancho": 0.45, "profundidad": 0.5, "posicion_x": 3.2, "posicion_z": 2}
L_ROOM = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]


async def test_undo_restores_the_shape_height_and_furniture(user_a, db_sessions, model, store):
    room_id = await created_id(user_a, vertices=L_ROOM, height_m=2.7)
    await add_furniture(db_sessions, room_id, MESA, SILLA)
    assert (await upload(user_a, room_id)).status_code == 200

    response = await undo(user_a, room_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"room", "items", "remaining"}
    assert body["remaining"] == 0
    room = body["room"]
    assert room["id"] == room_id
    assert room["vertices"] == [{"x": x, "y": y} for x, y in L_ROOM]
    assert (room["heightM"], room["areaM2"]) == (2.7, 12)
    names = [(item["name"], item["widthM"], item["depthM"]) for item in body["items"]]
    assert names == [("Mesa", 1.2, 0.8), ("Silla", 0.45, 0.5)]
    assert (body["items"][0]["x"], body["items"][0]["y"]) == pytest.approx((1.4, 1.1))
    # Releído con una sesión NUEVA: vuelven exactos.
    row = await stored_room(db_sessions, room_id)
    assert row.vertices == [{"x": x, "y": y} for x, y in L_ROOM]
    assert (row.ancho, row.profundidad, row.alto) == (4, 4, 2.7)
    assert furniture_values(await stored(db_sessions, room_id)) == [MESA, SILLA]


async def test_undo_gives_a_room_without_shape_its_none_vertices_back(user_a, db_sessions, model, store):
    room_id = await bare_room(db_sessions)
    await upload(user_a, room_id, confirm=False)

    response = await undo(user_a, room_id)

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    row = await stored_room(db_sessions, room_id)
    assert (row.vertices, row.ancho, row.profundidad, row.alto) == (None, 4, 5, 3)
    assert await stored(db_sessions, room_id) == []


async def test_two_photos_are_undone_last_first(user_a, db_sessions, model, store):
    room_id = await created_id(user_a, vertices=L_ROOM)
    for width in (5, 6):
        model.reply = room_with(width)
        assert (await upload(user_a, room_id)).status_code == 200

    first, second, third = [await undo(user_a, room_id) for _ in range(3)]

    assert (first.json()["remaining"], second.json()["remaining"]) == (1, 0)
    assert first.json()["room"]["vertices"][1] == {"x": 5, "y": 0}  # la sala de la primera foto
    assert second.json()["room"]["vertices"] == [{"x": x, "y": y} for x, y in L_ROOM]
    assert third.status_code == 409
    assert third.json()["detail"] == NOTHING_TO_UNDO


async def test_only_the_last_5_photos_can_be_undone(user_a, db_sessions, model, store, no_ai_limit):
    room_id = await created_id(user_a, vertices=L_ROOM)
    for width in range(2, 8):  # 6 fotos: anchos 2, 3, 4, 5, 6 y 7
        model.reply = room_with(width)
        assert (await upload(user_a, room_id)).status_code == 200

    responses = [await undo(user_a, room_id) for _ in range(6)]

    assert [r.status_code for r in responses] == [200] * 5 + [409]
    assert [r.json()["remaining"] for r in responses[:5]] == [4, 3, 2, 1, 0]
    assert [r.json()["room"]["vertices"][1]["x"] for r in responses[:5]] == [6, 5, 4, 3, 2]
    # La copia más antigua (la sala en L de antes de la primera foto) se descartó.
    assert (await stored_room(db_sessions, room_id)).ancho == 2


async def test_undo_needs_a_token(client, db_sessions, store):
    assert (await undo(client, 1)).status_code == 401


async def test_a_room_of_another_account_is_404_and_keeps_the_owners_stack(
    user_a, user_b, db_sessions, model, store
):
    room_id = await created_id(user_a)
    await upload(user_a, room_id)

    response = await undo(user_b, room_id)

    assert response.status_code == 404
    assert response.json()["detail"] == f"No existe una habitación con id {room_id}."
    assert store.depth("user-a", room_id) == 1
    assert (await undo(user_a, room_id)).status_code == 200


async def test_a_missing_room_is_404(user_a, db_sessions, store):
    assert (await undo(user_a, 999)).status_code == 404


async def test_nothing_to_undo_is_409(user_a, db_sessions, store):
    room_id = await created_id(user_a)

    response = await undo(user_a, room_id)

    assert response.status_code == 409
    assert response.json()["detail"] == NOTHING_TO_UNDO


async def test_a_restart_empties_the_history(user_a, db_sessions, model, store):
    room_id = await created_id(user_a)
    await upload(user_a, room_id)

    store.clear()  # lo que pasa al reiniciar el servidor
    response = await undo(user_a, room_id)

    assert response.status_code == 409
    assert response.json()["detail"] == NOTHING_TO_UNDO


async def test_undo_works_without_ai_quota_and_spends_none(user_a, db_sessions, model, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 3)
    rate_limit.reset_all()
    room_id = await created_id(user_a)
    assert (await upload(user_a, room_id)).status_code == 200
    assert (await upload(user_a, room_id)).status_code == 429
    used = len(rate_limit.ai_limiter()._events["user-a"])

    response = await undo(user_a, room_id)

    assert response.status_code == 200, response.text
    assert len(rate_limit.ai_limiter()._events["user-a"]) == used == 3
    assert len(model.calls) == 1


async def test_a_failed_photo_leaves_nothing_to_undo(user_a, db_sessions, model, store, monkeypatch):
    room_id = await created_id(user_a)
    assert (await upload(user_a, room_id, confirm=False)).status_code == 409
    model.reply = "no es JSON"
    assert (await upload(user_a, room_id)).status_code == 502
    model.reply = json.dumps(ESTIMATE)
    monkeypatch.setattr(get_settings(), "ai_photo_timeout", 0.05)
    model.delay = 5
    assert (await upload(user_a, room_id)).status_code == 504

    assert store.depth("user-a", room_id) == 0
    assert (await undo(user_a, room_id)).status_code == 409


async def test_a_failed_commit_puts_the_copy_back(user_a, db_sessions, model, store, monkeypatch):
    room_id = await created_id(user_a, vertices=L_ROOM)
    await upload(user_a, room_id)

    async def failing_commit(self):
        raise OSError("connection lost")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_commit)
        failed = await undo(user_a, room_id)

    assert failed.status_code == 503
    assert store.depth("user-a", room_id) == 1
    assert (await stored_room(db_sessions, room_id)).ancho == 4  # sigue la sala de la foto
    retried = await undo(user_a, room_id)
    assert retried.status_code == 200, retried.text
    assert retried.json()["room"]["vertices"] == [{"x": x, "y": y} for x, y in L_ROOM]
