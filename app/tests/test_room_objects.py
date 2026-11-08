"""Objetos de una sala guardada (CONTRATO-FASE0.md): esquemas y servicio, con SQLite en memoria."""

import math

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.geometry.arrangement import overlaps
from app.models.space import Habitacion, MuebleSugerido
from app.routers.errors import DatabaseFailure
from app.schemas.room import ItemCreate, ItemPlacement, ItemUpdate
from app.services.room_objects import (
    MAX_ROOM_ITEMS,
    WALL_NEEDS_RECTANGLE,
    RoomObjectError,
    as_object,
    delete_object,
    place_object,
    update_object,
)
from app.services.room_undo import RoomSnapshot, RoomUndoStore

pytestmark = pytest.mark.anyio

L_ROOM = [{"x": x, "y": y} for x, y in [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]]
PICTURE = {"name": "Cuadro", "kind": "wall", "wall": "left", "width_cm": 60, "depth_cm": 5, "height_cm": 40}
TABLE = {"name": "Mesa", "kind": "floor", "width_cm": 120, "depth_cm": 80, "height_cm": 75}


# --- Esquemas -------------------------------------------------------------------------------------------


def create_body(**changes):
    body = {"name": "Cuadro", "kind": "wall", "wall": "left", "widthCm": 60, "depthCm": 5, "heightCm": 40}
    body.update(changes)
    return body


def test_a_valid_body_collapses_spaces_in_the_name():
    item = ItemCreate.model_validate(create_body(name="  Cuadro   grande ", elevationCm=120))

    assert (item.name, item.wall, item.elevation_cm) == ("Cuadro grande", "left", 120)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "x" * 61},
        {"name": "<b>Cuadro</b>"},
        {"name": "Cua\u0000dro"},
        {"name": 5},
        {"widthCm": 4.9},
        {"heightCm": 400.1},
        {"depthCm": "2"},
        {"widthCm": math.nan},
        {"widthCm": math.inf},
        {"kind": "ceiling"},
        {"wall": "top"},
        {"wall": None},
        {"kind": "floor"},  # con wall
        {"kind": "floor", "wall": None, "elevationCm": 100},
        {"elevationCm": -1},
        {"color": "rojo"},
    ],
)
def test_invalid_create_bodies_are_rejected(changes):
    with pytest.raises(ValidationError):
        ItemCreate.model_validate(create_body(**changes))


def test_a_floor_object_needs_no_wall():
    item = ItemCreate.model_validate(create_body(kind="floor", wall=None))

    assert item.wall is None


def test_an_update_needs_at_least_one_field_and_rejects_unknown_ones():
    with pytest.raises(ValidationError):
        ItemUpdate.model_validate({})
    with pytest.raises(ValidationError):
        ItemUpdate.model_validate({"rotation": 90})
    with pytest.raises(ValidationError):
        ItemUpdate.model_validate({"position": {"x": 1, "y": 1, "z": 3}})
    with pytest.raises(ValidationError):
        ItemUpdate.model_validate({"position": {"x": 1001, "y": 1}})
    update = ItemUpdate.model_validate({"position": {"x": 1, "y": 0.5, "elevationM": 1.2, "wall": "back"}})
    assert update.position == ItemPlacement(x=1, y=0.5, elevation_m=1.2, wall="back")


def test_the_error_does_not_echo_the_value():
    secret = "<script>robar()</script>"

    with pytest.raises(ValidationError) as caught:
        ItemCreate.model_validate(create_body(name=secret))

    assert "robar" not in str([error["msg"] for error in caught.value.errors()])


# --- Servicio -----------------------------------------------------------------------------------------


async def new_room(db_sessions, vertices=None, width=4, depth=3, height=2.5, pieces=()):
    async with db_sessions() as session:
        row = Habitacion(
            user_id="user-a", nombre="Sala", ancho=width, profundidad=depth, alto=height, vertices=vertices
        )
        row.muebles = [MuebleSugerido(**piece) for piece in pieces]
        session.add(row)
        await session.commit()
        return row.id


async def load(session, room_id):
    query = select(Habitacion).where(Habitacion.id == room_id).options(selectinload(Habitacion.muebles))
    return await session.scalar(query)


async def stored(db_sessions, room_id):
    """Los objetos releídos con una sesión NUEVA."""
    async with db_sessions() as session:
        row = await load(session, room_id)
        return sorted(row.muebles, key=lambda piece: piece.id)


async def run(db_sessions, room_id, operation, *args, **kwargs):
    async with db_sessions() as session:
        row = await load(session, room_id)
        return await operation(session, row, *args, **kwargs)


def piece(name, width, depth, height, x, z, y=0.0):
    return {
        "nombre": name, "ancho": width, "profundidad": depth, "alto": height,
        "posicion_x": x, "posicion_y": y, "posicion_z": z,
    }


async def test_a_floor_object_goes_to_the_first_gap_and_is_stored(db_sessions):
    room_id = await new_room(db_sessions)

    result = await run(db_sessions, room_id, place_object, **TABLE)

    assert (result.wall, result.adjusted) == ("back", False)
    (table,) = await stored(db_sessions, room_id)
    assert table.nombre == "Mesa"
    assert (table.ancho, table.profundidad, table.alto) == pytest.approx((1.2, 0.8, 0.75))
    assert (table.posicion_x, table.posicion_y, table.posicion_z) == pytest.approx((0.6, 0, 0.4))


async def test_a_wall_object_is_stored_on_its_wall_with_swapped_extents(db_sessions):
    room_id = await new_room(db_sessions)

    result = await run(db_sessions, room_id, place_object, **PICTURE)

    assert (result.wall, result.adjusted) == ("left", False)
    (picture,) = await stored(db_sessions, room_id)
    assert (picture.ancho, picture.profundidad) == pytest.approx((0.05, 0.6))
    assert (picture.posicion_x, picture.posicion_z) == pytest.approx((0.025, 1.5))
    # Centro a 1,50 m: base a 1,30 m.
    assert picture.posicion_y == pytest.approx(1.3)


async def test_a_door_goes_to_the_floor_and_elevation_cm_is_respected(db_sessions):
    room_id = await new_room(db_sessions)

    await run(db_sessions, room_id, place_object, **{**PICTURE, "name": "Puerta", "height_cm": 200})
    await run(db_sessions, room_id, place_object, **{**PICTURE, "wall": "right", "elevation_cm": 90})

    door, picture = await stored(db_sessions, room_id)
    assert door.posicion_y == 0
    assert picture.posicion_y == pytest.approx(0.9)


async def test_a_second_picture_slides_and_is_reported_as_adjusted(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **PICTURE)

    result = await run(db_sessions, room_id, place_object, **{**PICTURE, "name": "Cuadro 2"})

    assert (result.wall, result.adjusted) == ("left", True)
    first, second = await stored(db_sessions, room_id)
    assert not overlaps(as_object(first), as_object(second))


async def test_wall_objects_need_a_rectangular_room_but_floor_ones_do_not(db_sessions):
    room_id = await new_room(db_sessions, vertices=L_ROOM, width=4, depth=4)

    with pytest.raises(RoomObjectError) as caught:
        await run(db_sessions, room_id, place_object, **PICTURE)
    await run(db_sessions, room_id, place_object, **TABLE)

    assert (caught.value.status_code, caught.value.message) == (422, WALL_NEEDS_RECTANGLE)
    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Mesa"]


async def test_no_space_is_409_and_stores_nothing(db_sessions):
    room_id = await new_room(db_sessions, pieces=[piece("Tarima", 4, 3, 0.3, 2, 1.5)])

    with pytest.raises(RoomObjectError) as floor:
        await run(db_sessions, room_id, place_object, **TABLE)
    with pytest.raises(RoomObjectError) as tall:
        await run(db_sessions, room_id, place_object, **{**TABLE, "height_cm": 300})

    assert (floor.value.status_code, floor.value.message) == (409, "No hay sitio para «Mesa» en el suelo.")
    assert (tall.value.status_code, tall.value.message) == (409, "«Mesa» es más alto que la sala.")
    assert len(await stored(db_sessions, room_id)) == 1


async def test_a_room_with_200_objects_is_full(db_sessions):
    pieces = [
        piece(f"Caja {i}", 0.05, 0.05, 0.05, 0.1 + (i % 20) * 0.2, 0.1 + (i // 20) * 0.2) for i in range(200)
    ]
    room_id = await new_room(db_sessions, pieces=pieces)

    with pytest.raises(RoomObjectError) as caught:
        await run(db_sessions, room_id, place_object, **TABLE)

    assert caught.value.status_code == 409
    assert str(MAX_ROOM_ITEMS) in caught.value.message
    assert len(await stored(db_sessions, room_id)) == 200


async def test_moving_onto_another_object_is_adjusted(db_sessions):
    room_id = await new_room(
        db_sessions, pieces=[piece("Cama", 1.6, 2.0, 0.5, 2, 1.5), piece("Mesa", 0.6, 0.6, 0.7, 0.3, 0.3)]
    )
    bed, table = await stored(db_sessions, room_id)
    target = ItemPlacement(x=2.0, y=1.2)  # centro (2,3; 1,5), dentro de la cama

    result = await run(db_sessions, room_id, update_object, table.id, position=target)

    assert result.adjusted is True
    _, moved = await stored(db_sessions, room_id)
    assert not overlaps(as_object(moved), as_object(bed))
    assert (moved.posicion_x, moved.posicion_z) == pytest.approx((3.11, 1.5))


async def test_a_free_move_is_not_adjusted(db_sessions):
    room_id = await new_room(db_sessions, pieces=[piece("Mesa", 0.6, 0.6, 0.7, 0.3, 0.3)])
    (table,) = await stored(db_sessions, room_id)

    result = await run(db_sessions, room_id, update_object, table.id, position=ItemPlacement(x=2, y=1))

    assert (result.adjusted, result.wall) == (False, None)
    (moved,) = await stored(db_sessions, room_id)
    assert (moved.posicion_x, moved.posicion_z) == pytest.approx((2.3, 1.3))


async def test_a_move_without_space_is_409_and_changes_nothing(db_sessions):
    room_id = await new_room(
        db_sessions, pieces=[piece("Tarima", 4, 2.4, 0.3, 2, 1.8), piece("Mesa", 0.6, 0.6, 0.7, 0.3, 0.3)]
    )
    _, table = await stored(db_sessions, room_id)

    with pytest.raises(RoomObjectError) as caught:
        await run(db_sessions, room_id, update_object, table.id, position=ItemPlacement(x=2, y=2))

    assert caught.value.status_code == 409
    _, kept = await stored(db_sessions, room_id)
    assert (kept.posicion_x, kept.posicion_z) == (0.3, 0.3)


async def test_sliding_a_picture_along_its_wall(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **PICTURE)
    (picture,) = await stored(db_sessions, room_id)

    target = ItemPlacement(x=0.5, y=0.4, elevation_m=1.1, wall="left")
    result = await run(db_sessions, room_id, update_object, picture.id, position=target)

    assert (result.wall, result.adjusted) == ("left", False)
    (moved,) = await stored(db_sessions, room_id)
    # x lo fija la pared; z = 0,4 + 0,6 / 2.
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((0.025, 0.7, 1.1))


async def test_a_wall_move_in_an_l_room_is_422(db_sessions):
    table_piece = piece("Mesa", 1, 1, 0.7, 1, 1)
    room_id = await new_room(db_sessions, vertices=L_ROOM, width=4, depth=4, pieces=[table_piece])
    (table,) = await stored(db_sessions, room_id)

    with pytest.raises(RoomObjectError) as caught:
        target = ItemPlacement(x=0, y=0, wall="left")
        await run(db_sessions, room_id, update_object, table.id, position=target)

    assert caught.value.status_code == 422


async def test_resizing_a_picture_keeps_it_on_its_wall(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **{**PICTURE, "wall": "right"})
    (picture,) = await stored(db_sessions, room_id)

    sizes = {"width_cm": 5, "depth_cm": 100, "height_cm": 50}
    result = await run(db_sessions, room_id, update_object, picture.id, **sizes)

    assert result.wall == "right"
    (resized,) = await stored(db_sessions, room_id)
    assert (resized.ancho, resized.profundidad, resized.alto) == pytest.approx((0.05, 1.0, 0.5))
    assert resized.posicion_x == pytest.approx(4 - 0.025)
    assert (resized.posicion_z, resized.posicion_y) == pytest.approx((1.5, 1.3))


async def test_renaming_only_keeps_the_geometry(db_sessions):
    room_id = await new_room(db_sessions, pieces=[piece("Mesa", 1, 1, 0.7, 2, 1.5)])
    (table,) = await stored(db_sessions, room_id)

    result = await run(db_sessions, room_id, update_object, table.id, name="Mesa grande")

    assert result.adjusted is False
    (renamed,) = await stored(db_sessions, room_id)
    assert (renamed.nombre, renamed.posicion_x, renamed.posicion_z) == ("Mesa grande", 2, 1.5)


async def test_an_object_of_another_room_is_404(db_sessions):
    room_id = await new_room(db_sessions)
    other_id = await new_room(db_sessions, pieces=[piece("Mesa", 1, 1, 0.7, 2, 1.5)])
    (foreign,) = await stored(db_sessions, other_id)

    for operation, kwargs in ((update_object, {"name": "Otra"}), (delete_object, {})):
        with pytest.raises(RoomObjectError) as caught:
            await run(db_sessions, room_id, operation, foreign.id, **kwargs)
        assert (caught.value.status_code, caught.value.message) == (
            404, f"No existe un objeto con id {foreign.id} en esta sala."
        )
    assert len(await stored(db_sessions, other_id)) == 1


async def test_delete_removes_the_object(db_sessions):
    pieces = [piece("Mesa", 1, 1, 0.7, 2, 1.5), piece("Silla", 0.5, 0.5, 0.9, 0.3, 0.3)]
    room_id = await new_room(db_sessions, pieces=pieces)
    table, _ = await stored(db_sessions, room_id)

    await run(db_sessions, room_id, delete_object, table.id)

    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Silla"]


# --- Pila de Deshacer de la sala (CONTRATO-FASE2.md) -------------------------------------------------------


def store_with_photo(room_id):
    store = RoomUndoStore()
    copy = RoomSnapshot(vertices=None, ancho=4, profundidad=3, alto=2.5, furniture=(), change="photo")
    store.push("user-a", room_id, copy)
    return store


async def test_every_saved_change_stacks_the_room_as_it_was(db_sessions):
    room_id = await new_room(db_sessions)
    store = store_with_photo(room_id)

    await run(db_sessions, room_id, place_object, **TABLE, undo_store=store)
    (table,) = await stored(db_sessions, room_id)
    await run(db_sessions, room_id, update_object, table.id, name="Mesa 2", undo_store=store)
    await run(db_sessions, room_id, delete_object, table.id, undo_store=store)

    assert store.depth("user-a", room_id) == 4
    deleted, renamed, added, photo = (store.pop("user-a", room_id) for _ in range(4))
    assert [copy.change for copy in (deleted, renamed, added, photo)] == ["delete", "update", "add", "photo"]
    # Cada copia es la sala justo antes de su cambio.
    assert [piece.nombre for piece in deleted.furniture] == ["Mesa 2"]
    assert [piece.nombre for piece in renamed.furniture] == ["Mesa"]
    assert added.furniture == ()


async def test_a_refused_change_keeps_the_photo_undo_stack(db_sessions):
    room_id = await new_room(db_sessions, vertices=L_ROOM, width=4, depth=4)
    store = store_with_photo(room_id)

    with pytest.raises(RoomObjectError):
        await run(db_sessions, room_id, place_object, **PICTURE, undo_store=store)

    assert store.depth("user-a", room_id) == 1


async def test_a_failed_commit_rolls_back_and_keeps_the_stack(db_sessions, monkeypatch):
    room_id = await new_room(db_sessions)
    store = store_with_photo(room_id)

    async def failing_commit(self):
        raise OSError("connection lost")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_commit)
        with pytest.raises(DatabaseFailure) as caught:
            await run(db_sessions, room_id, place_object, **TABLE, undo_store=store)

    assert caught.value.status_code == 503
    assert store.depth("user-a", room_id) == 1
    assert await stored(db_sessions, room_id) == []
