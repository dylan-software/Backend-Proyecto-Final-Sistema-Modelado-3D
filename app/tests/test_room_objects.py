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
    move_to_wall,
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


# --- Mover a una pared (CONTRATO-FASE2.md, 2.3) ----------------------------------------------------------


async def moved_to_wall(db_sessions, room_id, item_id, wall):
    """move_to_wall (núcleo sin commit) y el commit, como hará el chat."""
    async with db_sessions() as session:
        row = await load(session, room_id)
        result = move_to_wall(row, item_id, wall)
        await session.commit()
        return result


BED = piece("Cama", 1.0, 2.0, 0.5, 3.4, 1.0)  # x de 2,9 a 3,9 y z de 0 a 2, contra la pared del fondo


async def test_a_picture_moved_to_another_wall_keeps_its_width_and_height(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **PICTURE)
    (picture,) = await stored(db_sessions, room_id)

    result = await moved_to_wall(db_sessions, room_id, picture.id, "back")

    assert (result.wall, result.adjusted) == ("back", False)
    (moved,) = await stored(db_sessions, room_id)
    assert (moved.ancho, moved.profundidad) == pytest.approx((0.6, 0.05))
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((2, 0.025, 1.3))


async def test_a_loose_sofa_goes_against_the_wall_with_its_long_side(db_sessions):
    room_id = await new_room(db_sessions, pieces=[piece("Sofá", 2.0, 0.9, 0.85, 2, 1.5)])
    (sofa,) = await stored(db_sessions, room_id)

    result = await moved_to_wall(db_sessions, room_id, sofa.id, "left")

    assert result.wall == "left"
    (moved,) = await stored(db_sessions, room_id)
    assert (moved.ancho, moved.profundidad) == pytest.approx((0.9, 2.0))
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((0.45, 1.5, 0))


async def test_a_floor_object_moved_to_a_wall_slides_and_never_climbs(db_sessions):
    room_id = await new_room(db_sessions, pieces=[BED, piece("Sofá", 2.0, 0.9, 0.85, 1.5, 2.5)])
    bed, sofa = await stored(db_sessions, room_id)

    result = await moved_to_wall(db_sessions, room_id, sofa.id, "back")

    assert (result.wall, result.adjusted) == ("back", True)
    _, moved = await stored(db_sessions, room_id)
    assert moved.posicion_y == 0
    assert not overlaps(as_object(moved), as_object(bed))


async def test_patch_with_a_wall_never_puts_a_floor_object_on_top_of_another(db_sessions):
    room_id = await new_room(db_sessions, pieces=[BED, piece("Mesa", 0.6, 0.6, 0.7, 0.5, 2.5)])
    bed, table = await stored(db_sessions, room_id)

    target = ItemPlacement(x=3.0, y=0, wall="back")  # encima de la cama
    result = await run(db_sessions, room_id, update_object, table.id, position=target)

    assert result.adjusted is True
    _, moved = await stored(db_sessions, room_id)
    assert moved.posicion_y == 0
    assert not overlaps(as_object(moved), as_object(bed))


async def test_moving_to_a_wall_is_refused_without_changes(db_sessions):
    wide = piece("Tarima", 4, 0.5, 0.3, 2, 0.25)
    room_id = await new_room(db_sessions, pieces=[wide, piece("Sofá", 2.0, 0.9, 0.85, 2, 2)])
    table_piece = piece("Mesa", 1, 1, 0.7, 1, 1)
    l_room = await new_room(db_sessions, vertices=L_ROOM, width=4, depth=4, pieces=[table_piece])
    _, sofa = await stored(db_sessions, room_id)
    (table,) = await stored(db_sessions, l_room)

    with pytest.raises(RoomObjectError) as full:
        await moved_to_wall(db_sessions, room_id, sofa.id, "back")
    with pytest.raises(RoomObjectError) as not_rectangle:
        await moved_to_wall(db_sessions, l_room, table.id, "left")
    with pytest.raises(RoomObjectError) as foreign:
        await moved_to_wall(db_sessions, room_id, table.id, "left")

    no_space = "No hay sitio para «Sofá» en la pared del fondo."
    assert (full.value.status_code, full.value.message) == (409, no_space)
    assert (not_rectangle.value.status_code, not_rectangle.value.message) == (422, WALL_NEEDS_RECTANGLE)
    assert foreign.value.status_code == 404
    _, kept = await stored(db_sessions, room_id)
    assert (kept.posicion_x, kept.posicion_z) == (2, 2)


# --- PATCH sin wall de un objeto de pared (PREGUNTAS.md 8) -------------------------------------------


def gap_to_nearest_wall(stored_piece, width=4, depth=3):
    """Distancia de la huella a la pared más cercana de una sala [0, width] x [0, depth]."""
    obj = as_object(stored_piece)
    x0, x1 = obj.center_x - obj.width_m / 2, obj.center_x + obj.width_m / 2
    z0, z1 = obj.center_z - obj.depth_m / 2, obj.center_z + obj.depth_m / 2
    assert -1e-9 <= x0 and x1 <= width + 1e-9 and -1e-9 <= z0 and z1 <= depth + 1e-9
    return min(x0, z0, width - x1, depth - z1)


async def moved_without_wall(db_sessions, room_id, item_id, **position):
    return await run(db_sessions, room_id, update_object, item_id, position=ItemPlacement(**position))


async def test_a_picture_dragged_into_the_room_goes_to_the_nearest_wall(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **PICTURE)  # pared izquierda, 5 x 60 cm
    (picture,) = await stored(db_sessions, room_id)

    # Centro pedido (1,525; 0,7): la pared del fondo (z = 0) es la más cercana.
    result = await moved_without_wall(db_sessions, room_id, picture.id, x=1.5, y=0.4, elevation_m=1.1)

    assert (result.wall, result.adjusted) == ("back", True)
    (moved,) = await stored(db_sessions, room_id)
    # Sigue siendo 60 cm a lo largo de la pared y 5 cm de grosor (no gira).
    assert (moved.ancho, moved.profundidad, moved.alto) == pytest.approx((0.6, 0.05, 0.4))
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((1.525, 0.025, 1.1))
    assert gap_to_nearest_wall(moved) == pytest.approx(0)


async def test_a_picture_moved_near_its_own_wall_stays_on_it_at_0_cm(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **PICTURE)
    (picture,) = await stored(db_sessions, room_id)

    result = await moved_without_wall(db_sessions, room_id, picture.id, x=0.3, y=0.4)

    assert (result.wall, result.adjusted) == ("left", True)
    (moved,) = await stored(db_sessions, room_id)
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((0.025, 0.7, 1.3))


async def test_a_drag_along_the_wall_is_not_adjusted(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **PICTURE)
    (picture,) = await stored(db_sessions, room_id)

    result = await moved_without_wall(db_sessions, room_id, picture.id, x=0, y=0.4)

    assert (result.wall, result.adjusted) == ("left", False)


async def test_a_picture_past_the_corner_stays_whole_inside_its_wall(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **{**PICTURE, "wall": "back"})
    (picture,) = await stored(db_sessions, room_id)

    result = await moved_without_wall(db_sessions, room_id, picture.id, x=3.9, y=0)

    assert (result.wall, result.adjusted) == ("back", True)
    (moved,) = await stored(db_sessions, room_id)
    # Entero dentro del tramo: de 3,4 a 4,0 m.
    assert (moved.posicion_x, moved.posicion_z) == pytest.approx((3.7, 0.025))


async def test_a_floating_picture_goes_back_to_a_wall(db_sessions):
    floating = piece("Cuadro", 0.6, 0.05, 0.4, 2, 1.5, y=1.3)  # en medio de la sala, sin pared
    room_id = await new_room(db_sessions, pieces=[floating])
    (picture,) = await stored(db_sessions, room_id)

    result = await moved_without_wall(db_sessions, room_id, picture.id, x=1.7, y=2.6)

    assert result.wall == "front"
    (moved,) = await stored(db_sessions, room_id)
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((2.0, 2.975, 1.3))
    assert gap_to_nearest_wall(moved) == pytest.approx(0)


async def test_a_picture_dropped_on_another_slides_without_overlapping(db_sessions):
    room_id = await new_room(db_sessions)
    await run(db_sessions, room_id, place_object, **{**PICTURE, "wall": "back"})
    await run(db_sessions, room_id, place_object, **{**PICTURE, "name": "Cuadro 2", "wall": "right"})
    first, second = await stored(db_sessions, room_id)

    # Encima del primero (centro 2; 0,025), a la misma altura.
    result = await moved_without_wall(db_sessions, room_id, second.id, x=1.8, y=0.3)

    assert (result.wall, result.adjusted) == ("back", True)
    first, second = await stored(db_sessions, room_id)
    assert not overlaps(as_object(first), as_object(second))
    assert gap_to_nearest_wall(second) == pytest.approx(0)
    assert (second.ancho, second.profundidad) == pytest.approx((0.6, 0.05))


async def test_an_object_resting_on_furniture_still_moves_freely(db_sessions):
    backpack = piece("Mochila", 0.3, 0.3, 0.4, 3.4, 1.0, y=0.5)  # encima de la cama
    room_id = await new_room(db_sessions, pieces=[BED, backpack])
    _, bag = await stored(db_sessions, room_id)

    result = await moved_without_wall(db_sessions, room_id, bag.id, x=3.2, y=0.4)

    assert result.wall is None
    _, moved = await stored(db_sessions, room_id)
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((3.35, 0.55, 0.5))


async def test_a_raised_object_in_an_l_room_keeps_the_free_move(db_sessions):
    shelf = piece("Repisa", 0.5, 0.2, 0.1, 1, 1, y=1.5)
    room_id = await new_room(db_sessions, vertices=L_ROOM, width=4, depth=4, pieces=[shelf])
    (stored_shelf,) = await stored(db_sessions, room_id)

    result = await moved_without_wall(db_sessions, room_id, stored_shelf.id, x=0.5, y=0.5)

    assert result.wall is None
    (moved,) = await stored(db_sessions, room_id)
    assert (moved.posicion_x, moved.posicion_z, moved.posicion_y) == pytest.approx((0.75, 0.6, 1.5))
