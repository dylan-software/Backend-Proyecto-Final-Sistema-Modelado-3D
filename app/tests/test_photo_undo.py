"""Deshacer la foto a 3D (CONTRATO-DESHACER-FOTO.md): el almacén de copias y sus pilas."""

import dataclasses

import pytest

from app.models.space import Habitacion, MuebleSugerido
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
