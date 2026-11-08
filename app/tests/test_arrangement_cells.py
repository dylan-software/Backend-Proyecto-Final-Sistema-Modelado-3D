"""Celdas del suelo (FloorCells) y franja de pared (wall_strip) sobre FixedArray: prefiltros exactos."""

import random
from dataclasses import dataclass, replace

from app.geometry.arrangement import (
    EPSILON,
    FLOOR_CELLS_PER_SIDE,
    REASON_NO_SPACE,
    FloorCells,
    _span_overlap,
    _steps,
    first_free_floor_spot,
    footprint,
    grid_step,
    overlaps,
    wall_strip,
)
from app.geometry.room import BoundingBox, Room
from app.structures import FixedArray

BOX = BoundingBox(0, 0, 8, 6)


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


def box(name, width, depth, x, z, height=1.0, elevation=0.0):
    """Caja con su esquina mínima en (x, z)."""
    return Item(name, "floor", width, depth, height, x + width / 2, z + depth / 2, elevation)


def random_box(rng, name):
    width, depth = rng.uniform(0.05, 4), rng.uniform(0.05, 4)
    return box(name, width, depth, rng.uniform(-1, 8), rng.uniform(-1, 6), rng.uniform(0.1, 2))


def test_the_cells_are_a_fixed_array_of_side_by_side_slots():
    cells = FloorCells(BOX, [box("a", 1, 1, 0, 0)])
    assert isinstance(cells._cells, FixedArray)
    assert cells._cells.size == FLOOR_CELLS_PER_SIDE * FLOOR_CELLS_PER_SIDE
    assert isinstance(cells._items, FixedArray) and cells._items.size == 1


def test_no_objects_means_nothing_is_near():
    assert FloorCells(BOX, []).near(box("x", 1, 1, 3, 3)) == []


def test_far_objects_are_left_out_and_close_ones_kept_in_order():
    corner = box("esquina", 0.5, 0.5, 0, 0)
    far = box("lejos", 0.5, 0.5, 7.4, 5.4)
    middle = box("centro", 2, 2, 3, 2)
    cells = FloorCells(BOX, [middle, far, corner])
    assert cells.near(box("nuevo", 0.4, 0.4, 0.1, 0.1)) == [corner]
    assert cells.near(box("grande", 8, 6, 0, 0)) == [middle, far, corner]  # orden de entrada


def test_objects_outside_the_box_go_to_the_border_cells():
    outside = box("fuera", 1, 1, 9, 7)
    cells = FloorCells(BOX, [outside])
    assert cells.near(box("borde", 0.3, 0.3, 7.7, 5.7)) == [outside]


def test_near_never_misses_an_overlapping_object():
    for seed in range(40):
        rng = random.Random(seed)
        placed = [random_box(rng, f"o{i}") for i in range(rng.randint(1, 40))]
        cells = FloorCells(BOX, placed)
        for _ in range(200):
            candidate = random_box(rng, "c")
            near = cells.near(candidate)
            assert len(near) == len({id(o) for o in near})
            hits = [o for o in placed if overlaps(candidate, o)]
            assert hits == [o for o in near if overlaps(candidate, o)]


def test_first_free_floor_spot_matches_a_brute_force_scan():
    """El hueco elegido es el mismo que comparando cada candidato con TODOS los objetos."""

    def brute_force(item, room, placed):
        b = room.bounding_box()
        step = grid_step(b)
        for z0 in _steps(b.min_y, b.max_y - item.depth_m, step):
            for x0 in _steps(b.min_x, b.max_x - item.width_m, step):
                c = replace(
                    item, center_x=x0 + item.width_m / 2, center_z=z0 + item.depth_m / 2, elevation_m=0.0
                )
                if room.contains_rectangle(x0, z0, item.width_m, item.depth_m) and not any(
                    overlaps(c, o) for o in placed
                ):
                    return c
        return None

    room = Room([(0, 0), (6, 0), (6, 3), (3, 3), (3, 5), (0, 5)], 2.5)
    for seed in range(15):
        rng = random.Random(seed)
        placed = []
        for _ in range(12):
            item = box("m", rng.uniform(0.3, 2), rng.uniform(0.3, 2), 0, 0, rng.uniform(0.3, 2.4))
            found, reason = first_free_floor_spot(item, room, placed, max_work=10**9)
            expected = brute_force(item, room, placed)
            assert found == expected
            assert (reason == REASON_NO_SPACE) == (expected is None)
            if found is not None:
                assert found.elevation_m <= EPSILON
                placed.append(found)


def test_wall_strip_keeps_only_objects_across_the_strip_in_order():
    picture = Item("Cuadro", "wall", 1.0, 0.05, 0.5, center_x=2, center_z=0.025, elevation_m=1.2)
    bed = box("Cama", 2, 2, 0, 0)  # toca la franja z 0-0,05
    table = box("Mesa", 1, 1, 3, 1)  # lejos de la pared de atrás
    shelf = box("Estante", 0.5, 0.3, 6, 0)  # en la franja, más a la derecha
    strip = wall_strip(picture, "x", [bed, table, shelf])
    assert isinstance(strip, FixedArray)
    assert list(strip) == [bed, shelf]


def test_wall_strip_on_a_side_wall_uses_the_x_band():
    picture = Item("Cuadro", "wall", 0.05, 1.0, 0.5, center_x=0.025, center_z=2, elevation_m=1.2)
    near_wall = box("Armario", 0.6, 1, 0, 4)
    away = box("Mesa", 1, 1, 2, 2)
    assert list(wall_strip(picture, "z", [near_wall, away])) == [near_wall]


def test_wall_strip_is_empty_when_nothing_is_on_the_wall():
    picture = Item("Cuadro", "wall", 1.0, 0.05, 0.5, center_x=2, center_z=0.025)
    assert wall_strip(picture, "x", [box("Mesa", 1, 1, 3, 1)]) == ()
    assert wall_strip(picture, "x", []) == ()


def test_wall_strip_matches_the_overlap_rule_on_random_objects():
    rng = random.Random(3)
    for _ in range(300):
        axis = rng.choice(("x", "z"))
        item = random_box(rng, "pared")
        placed = [random_box(rng, f"o{i}") for i in range(rng.randint(0, 20))]
        x0, x1, z0, z1 = footprint(item)
        low, high = (z0, z1) if axis == "x" else (x0, x1)
        expected = []
        for other in placed:
            ox0, ox1, oz0, oz1 = footprint(other)
            o_low, o_high = (oz0, oz1) if axis == "x" else (ox0, ox1)
            if _span_overlap(low, high, o_low, o_high) > EPSILON:
                expected.append(other)
        assert list(wall_strip(item, axis, placed)) == expected
