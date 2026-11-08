"""Colocación de UN objeto en una sala guardada (CONTRATO-FASE0.md): pared pedida, primer hueco del
suelo y revalidación de una posición."""

from dataclasses import dataclass

import pytest

from app.geometry.arrangement import (
    REASON_NO_SPACE,
    REASON_OUTSIDE,
    REASON_TOO_TALL,
    RoomBox,
    attached_wall,
    first_free_floor_spot,
    overlap_reason,
    overlaps,
    place_on_wall,
    rectangle_of,
    settle_at,
    wall_extents,
)
from app.geometry.room import Room


@dataclass(frozen=True)
class Item:
    name: str
    kind: str
    width_m: float
    depth_m: float
    height_m: float
    center_x: float = 0.0
    center_z: float = 0.0
    elevation_m: float = 0.0


SQUARE = Room.from_record(None, 4, 3, 2.5)  # [0, 4] x [0, 3], 2,5 m de alto
L_ROOM = Room([(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)], 2.5)  # muesca: x 2-4, z 2-4


def picture(name="Cuadro", elevation=1.3):
    return Item(name, "wall", 0.6, 0.03, 0.4, elevation_m=elevation)


def box(name, width, depth, height, x, z, elevation=0.0, kind="floor"):
    return Item(name, kind, width, depth, height, x, z, elevation)


def inside(item, room):
    return room.contains_rectangle(
        item.center_x - item.width_m / 2, item.center_z - item.depth_m / 2, item.width_m, item.depth_m
    )


# --- Sala rectangular ---------------------------------------------------------------------------------


def test_a_room_without_vertices_is_its_rectangle():
    assert rectangle_of(SQUARE) == (0, 0, RoomBox(4, 3, 2.5))


def test_an_offset_rectangle_with_a_collinear_vertex_is_a_rectangle():
    room = Room([(1, 2), (3, 2), (5, 2), (5, 5), (1, 5)], 2.6)

    assert rectangle_of(room) == (1, 2, RoomBox(4, 3, 2.6))


def test_an_l_shaped_room_is_not_a_rectangle():
    assert rectangle_of(L_ROOM) is None


@pytest.mark.parametrize(
    ("side", "expected"),
    [("back", (2, 0.015, 0.6, 0.03)), ("front", (2, 2.985, 0.6, 0.03)),
     ("left", (0.015, 1.5, 0.03, 0.6)), ("right", (3.985, 1.5, 0.03, 0.6))],
)
def test_a_wall_object_goes_to_the_requested_wall_centered(side, expected):
    placed, reason = place_on_wall(picture(), SQUARE, side, [])

    assert reason is None
    assert (placed.center_x, placed.center_z, placed.width_m, placed.depth_m) == pytest.approx(expected)
    assert placed.elevation_m == 1.3
    assert attached_wall(placed, SQUARE) == side


def test_an_offset_rectangle_keeps_its_coordinates():
    room = Room([(1, 2), (5, 2), (5, 5), (1, 5)], 2.5)

    placed, _ = place_on_wall(picture(), room, "left", [])

    assert (placed.center_x, placed.center_z) == pytest.approx((1.015, 3.5))
    assert attached_wall(placed, room) == "left"


def test_wall_objects_need_a_rectangular_room_and_a_known_wall():
    with pytest.raises(ValueError):
        place_on_wall(picture(), L_ROOM, "left", [])
    with pytest.raises(ValueError):
        place_on_wall(picture(), SQUARE, "ceiling", [])


def test_a_door_goes_to_the_floor():
    door = Item("Puerta", "wall", 0.9, 0.05, 2.0, elevation_m=1.0)

    placed, _ = place_on_wall(door, SQUARE, "front", [])

    assert placed.elevation_m == 0


def test_the_elevation_is_kept_below_the_ceiling():
    placed, _ = place_on_wall(picture(elevation=2.4), SQUARE, "back", [])

    assert placed.elevation_m == pytest.approx(2.1)


def test_a_second_picture_slides_along_the_wall():
    first, _ = place_on_wall(picture("Cuadro 1"), SQUARE, "back", [])

    second, reason = place_on_wall(picture("Cuadro 2"), SQUARE, "back", [first])

    assert reason is None
    assert second.center_z == pytest.approx(0.015)
    assert second.center_x != pytest.approx(2)
    assert not overlaps(first, second)


def test_a_picture_rises_above_a_wardrobe_against_the_wall():
    wardrobe = box("Armario", 1.2, 0.6, 2.0, 2, 0.3)

    placed, reason = place_on_wall(picture(), SQUARE, "back", [wardrobe])

    assert reason is None
    assert placed.elevation_m == pytest.approx(2.0)


def test_a_full_wall_has_no_room_left():
    blockers = [box(f"Estante {i}", 1.0, 0.03, 2.5, 0.5 + i, 0.015, kind="wall") for i in range(4)]

    placed, reason = place_on_wall(picture(), SQUARE, "back", blockers)

    assert placed is None
    assert reason == overlap_reason("Estante 1")


def test_too_tall_or_too_long_for_the_wall():
    assert place_on_wall(Item("Poste", "wall", 0.2, 0.2, 2.6), SQUARE, "back", []) == (None, REASON_TOO_TALL)
    assert place_on_wall(Item("Friso", "wall", 3.5, 0.02, 0.2), SQUARE, "left", []) == (None, REASON_OUTSIDE)


def test_without_centering_the_position_along_the_wall_is_kept_and_bounded():
    near_corner, _ = place_on_wall(
        Item("Cuadro", "wall", 0.6, 0.03, 0.4, 0.5, 1.0, 1.3), SQUARE, "back", [], centered=False
    )
    beyond, _ = place_on_wall(
        Item("Cuadro", "wall", 0.6, 0.03, 0.4, 9.0, 1.0, 1.3), SQUARE, "back", [], centered=False
    )

    assert near_corner.center_x == pytest.approx(0.5)
    assert beyond.center_x == pytest.approx(3.7)


def test_wall_extents_swap_on_the_side_walls():
    assert wall_extents(0.6, 0.03, "back") == (0.6, 0.03)
    assert wall_extents(0.03, 0.6, "left") == (0.6, 0.03)


def test_a_floor_object_is_not_attached_in_an_l_room():
    assert attached_wall(box("Mesa", 1, 1, 0.7, 0.5, 0.5), L_ROOM) is None


# --- Primer hueco del suelo ---------------------------------------------------------------------------


def test_an_empty_room_starts_at_the_minimum_corner():
    placed, reason = first_free_floor_spot(box("Mesa", 1.2, 0.8, 0.75, 9, 9), SQUARE, [])

    assert reason is None
    assert (placed.center_x, placed.center_z, placed.elevation_m) == pytest.approx((0.6, 0.4, 0))


def test_the_first_gap_skips_what_is_there_and_is_deterministic():
    bed = box("Cama", 1.6, 2.0, 0.5, 0.8, 1.0)
    table = box("Mesa", 1.2, 0.8, 0.75, 0, 0)

    first, _ = first_free_floor_spot(table, SQUARE, [bed])
    again, _ = first_free_floor_spot(table, SQUARE, [bed])

    assert first == again
    assert not overlaps(first, bed)
    assert inside(first, SQUARE)
    assert (first.center_x, first.center_z) == pytest.approx((2.2, 0.4))


def test_a_picture_high_on_the_wall_does_not_block_the_floor():
    high, _ = place_on_wall(picture(), SQUARE, "back", [])
    stool = box("Taburete", 0.4, 0.4, 0.5, 0, 0)

    placed, _ = first_free_floor_spot(stool, SQUARE, [replace_x(high, 0.3)])

    assert (placed.center_x, placed.center_z) == pytest.approx((0.2, 0.2))


def replace_x(item, center_x):
    return Item(item.name, item.kind, item.width_m, item.depth_m, item.height_m, center_x, item.center_z,
                item.elevation_m)


def test_the_floor_gap_respects_the_real_polygon():
    blocker = box("Sofá", 2.0, 2.0, 0.8, 1.0, 1.0)
    for size in (0.5, 1.0, 1.5):
        placed, reason = first_free_floor_spot(box("Caja", size, size, 0.5, 0, 0), L_ROOM, [blocker])
        assert reason is None
        assert inside(placed, L_ROOM)
        assert not overlaps(placed, blocker)


def test_a_full_floor_or_a_too_tall_object_has_no_gap():
    filler = box("Tarima", 4, 3, 0.3, 2, 1.5)

    assert first_free_floor_spot(box("Mesa", 1, 1, 0.7, 0, 0), SQUARE, [filler]) == (None, REASON_NO_SPACE)
    assert first_free_floor_spot(box("Torre", 1, 1, 2.6, 0, 0), SQUARE, []) == (None, REASON_TOO_TALL)


def test_the_floor_search_has_a_work_budget():
    filler = box("Tarima", 4, 3, 0.3, 2, 1.5)

    result = first_free_floor_spot(box("Mesa", 1, 1, 0.7, 0, 0), SQUARE, [filler], max_work=10)

    assert result == (None, REASON_NO_SPACE)


# --- Revalidar una posición pedida --------------------------------------------------------------------


def test_a_free_position_is_kept():
    table = box("Mesa", 1, 1, 0.7, 2, 1.5)

    assert settle_at(table, SQUARE, []) == (table, None)


def test_a_position_outside_the_box_is_bounded_and_the_elevation_too():
    table = box("Mesa", 1, 1, 0.7, 9, -3, elevation=5)

    placed, _ = settle_at(table, SQUARE, [])

    assert (placed.center_x, placed.center_z, placed.elevation_m) == pytest.approx((3.5, 0.5, 1.8))


def test_a_position_in_the_notch_of_an_l_room_is_outside():
    assert settle_at(box("Mesa", 1, 1, 0.7, 3, 3), L_ROOM, []) == (None, REASON_OUTSIDE)


def test_a_colliding_position_moves_to_the_nearest_gap():
    bed = box("Cama", 1.6, 2.0, 0.5, 2, 1.5)
    table = box("Mesa", 0.6, 0.6, 0.7, 2.3, 1.5)

    placed, reason = settle_at(table, SQUARE, [bed])

    assert reason is None
    assert not overlaps(placed, bed)
    assert placed.center_x == pytest.approx(2.81 + 0.3)
    assert placed.center_z == pytest.approx(1.5)


def test_a_collision_without_a_gap_is_refused():
    filler = box("Tarima", 4, 3, 0.3, 2, 1.5)

    assert settle_at(box("Mesa", 1, 1, 0.7, 2, 1.5), SQUARE, [filler]) == (None, overlap_reason("Tarima"))


def test_the_shift_can_be_limited_to_one_axis():
    bed = box("Cama", 1.6, 2.0, 0.5, 2, 1.0)
    table = box("Mesa", 0.6, 0.6, 0.7, 2.0, 1.2)

    placed, _ = settle_at(table, SQUARE, [bed], axes=("z",))

    assert placed.center_x == pytest.approx(2.0)
    assert placed.center_z == pytest.approx(2.0 + 0.01 + 0.3)


def test_too_big_for_the_room():
    assert settle_at(box("Torre", 1, 1, 2.6, 2, 1.5), SQUARE, []) == (None, REASON_TOO_TALL)
    assert settle_at(box("Tarima", 4.5, 1, 0.3, 2, 1.5), SQUARE, []) == (None, REASON_OUTSIDE)
