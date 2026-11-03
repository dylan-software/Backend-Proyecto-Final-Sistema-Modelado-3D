"""Coherencia espacial de la foto a 3D: recolocación determinista, sin IA."""

from dataclasses import dataclass

import pytest

from app.geometry.arrangement import (
    REASON_OUTSIDE,
    arrange_objects,
    is_door,
    normalized,
    overlap_reason,
    overlaps,
    snap_to_wall,
)


@dataclass(frozen=True)
class Room:
    width_m: float = 4.0
    length_m: float = 3.0
    height_m: float = 2.5


@dataclass(frozen=True)
class Item:
    name: str
    kind: str
    width_m: float
    depth_m: float
    height_m: float
    center_x: float
    center_z: float
    elevation_m: float = 0.0


ROOM = Room()


def bed(name="Cama", x=2.0, z=1.5):
    return Item(name, "floor", 1.6, 2.0, 0.5, x, z)


def laptop(x=2.0, z=1.5):
    return Item("Portátil", "floor", 0.35, 0.25, 0.03, x, z)


def by_name(items):
    return {item.name: item for item in items}


# --- Objetos de suelo ----------------------------------------------------------------------------


def test_a_laptop_inside_a_bed_goes_on_top_of_it():
    kept, dropped = arrange_objects(ROOM, [laptop(), bed()])

    placed = by_name(kept)
    assert dropped == []
    assert placed["Portátil"].elevation_m == pytest.approx(0.5)  # la tapa de la cama
    assert (placed["Portátil"].center_x, placed["Portátil"].center_z) == (2.0, 1.5)
    assert placed["Cama"].elevation_m == 0
    assert not overlaps(placed["Portátil"], placed["Cama"])  # apoyado encima no es solape


def test_two_overlapping_beds_are_pushed_apart_by_the_shortest_move():
    first, second = bed("Cama 1", x=1.0, z=1.0), bed("Cama 2", x=1.5, z=1.0)

    kept, dropped = arrange_objects(ROOM, [first, second])

    placed = by_name(kept)
    assert dropped == []
    assert placed["Cama 1"] == first  # la primera (mismo tamaño, antes en la lista) no se mueve
    # Solo cabe a su derecha: 1.8 + 0.01 de separación + 0.8 de media cama.
    assert placed["Cama 2"].center_x == pytest.approx(2.61)
    assert placed["Cama 2"].center_z == 1.0
    assert not overlaps(placed["Cama 1"], placed["Cama 2"])


def test_a_bed_that_cannot_fit_anywhere_is_discarded_naming_the_other():
    small_room = Room(width_m=2.0, length_m=2.2)
    first, second = bed("Cama 1", x=1.0, z=1.1), bed("Cama 2", x=1.2, z=1.1)

    kept, dropped = arrange_objects(small_room, [first, second])

    assert [item.name for item in kept] == ["Cama 1"]
    assert dropped == [("Cama 2", "se solapa con Cama 1")]


def test_the_bigger_object_stays_and_the_smaller_one_moves():
    table = Item("Mesa", "floor", 1.2, 0.8, 0.75, 2.0, 1.5)
    chair = Item("Silla", "floor", 0.5, 0.5, 0.9, 2.3, 1.5)  # demasiado alta para ir encima

    kept, _ = arrange_objects(ROOM, [chair, table])

    placed = by_name(kept)
    assert placed["Mesa"] == table
    assert placed["Silla"].elevation_m == 0
    assert not overlaps(placed["Mesa"], placed["Silla"])
    assert [item.name for item in kept] == ["Silla", "Mesa"]  # orden original del modelo


def test_nothing_is_stacked_on_something_already_stacked():
    book = Item("Libro", "floor", 0.2, 0.15, 0.04, 2.0, 1.5)

    kept, _ = arrange_objects(ROOM, [bed(), laptop(), book])

    placed = by_name(kept)
    assert placed["Portátil"].elevation_m == pytest.approx(0.5)
    # Sobre la cama ya está el portátil en ese sitio, y sobre el portátil no se apila: se aparta.
    assert placed["Libro"].elevation_m == 0
    assert all(not overlaps(a, b) for a in kept for b in kept if a is not b)


def test_no_stacking_when_it_would_go_through_the_ceiling():
    low_room = Room(height_m=2.0)
    wardrobe = Item("Armario", "floor", 1.5, 1.0, 1.9, 2.0, 1.5)
    box = Item("Caja", "floor", 0.3, 0.3, 0.3, 2.0, 1.5)

    kept, _ = arrange_objects(low_room, [wardrobe, box])

    assert by_name(kept)["Caja"].elevation_m == 0
    assert not overlaps(*kept)


def test_objects_that_only_touch_are_left_alone():
    left = Item("A", "floor", 1.0, 1.0, 0.5, 1.0, 1.0)
    right = Item("B", "floor", 1.0, 1.0, 0.5, 2.0, 1.0)  # comparten el borde x = 1.5

    assert arrange_objects(ROOM, [left, right]) == ([left, right], [])


# --- Objetos de pared ----------------------------------------------------------------------------


def test_a_mirror_inside_a_bed_goes_to_the_wall_above_the_bed():
    headboard_bed = bed(x=2.0, z=1.0)  # pegada a la pared del fondo (z de 0 a 2)
    mirror = Item("Espejo", "wall", 0.8, 0.05, 1.0, 2.0, 0.6, elevation_m=0.2)

    kept, dropped = arrange_objects(ROOM, [headboard_bed, mirror])

    placed = by_name(kept)
    assert dropped == []
    assert placed["Espejo"].center_z == pytest.approx(0.025)  # pegado a la pared z = 0
    assert placed["Espejo"].elevation_m == pytest.approx(0.5)  # sobre la cama, no dentro
    assert not overlaps(placed["Espejo"], placed["Cama"])


def test_a_door_on_a_vertical_wall_is_turned_and_put_on_the_floor():
    door = Item("Puerta del baño", "wall", 0.9, 0.1, 2.1, 3.9, 1.5, elevation_m=0.5)

    ((placed,), dropped) = arrange_objects(ROOM, [door])

    assert dropped == []
    # Pared x = W: ancho y fondo se intercambian; el grosor va pegado a la pared.
    assert (placed.width_m, placed.depth_m) == (0.1, 0.9)
    assert placed.center_x == pytest.approx(3.95)
    assert placed.center_z == 1.5
    assert placed.elevation_m == 0


def test_a_window_on_a_horizontal_wall_keeps_its_width_along_x_and_its_height():
    window = Item("Ventana", "wall", 1.2, 0.1, 1.0, 1.0, 2.8, elevation_m=1.0)

    ((placed,), _) = arrange_objects(ROOM, [window])

    assert (placed.width_m, placed.depth_m) == (1.2, 0.1)
    assert placed.center_z == pytest.approx(2.95)  # pared z = L
    assert placed.elevation_m == 1.0


def test_wall_objects_are_kept_within_the_wall_and_below_the_ceiling():
    picture = Item("Cuadro", "wall", 1.0, 0.03, 0.6, 0.2, 0.01, elevation_m=2.4)

    ((placed,), _) = arrange_objects(ROOM, [picture])

    assert placed.center_x == pytest.approx(0.5)  # no sobresale de la esquina
    assert placed.elevation_m == pytest.approx(2.5 - 0.6)


def test_two_pictures_on_the_same_wall_slide_apart_along_it():
    first = Item("Cuadro 1", "wall", 0.8, 0.03, 0.6, 1.0, 0.02, elevation_m=1.4)
    second = Item("Cuadro 2", "wall", 0.8, 0.03, 0.6, 1.3, 0.02, elevation_m=1.4)

    kept, dropped = arrange_objects(ROOM, [first, second])

    placed = by_name(kept)
    assert dropped == []
    assert placed["Cuadro 2"].center_z == placed["Cuadro 1"].center_z  # sigue en la pared
    assert placed["Cuadro 2"].center_x == pytest.approx(1.4 + 0.01 + 0.4)
    assert not overlaps(placed["Cuadro 1"], placed["Cuadro 2"])


def test_a_wall_object_wider_than_its_wall_is_outside():
    huge = Item("Mural", "wall", 4.5, 0.02, 1.0, 2.0, 0.01, elevation_m=1.0)

    assert arrange_objects(ROOM, [huge]) == ([], [("Mural", REASON_OUTSIDE)])


def test_furniture_in_front_of_a_door_is_moved_out_of_the_way():
    door = Item("Puerta", "wall", 0.9, 0.1, 2.1, 2.0, 0.05)
    shelf = Item("Estantería", "floor", 1.0, 0.4, 1.8, 2.0, 0.2)

    kept, _ = arrange_objects(ROOM, [shelf, door])

    assert not overlaps(*kept)
    assert by_name(kept)["Puerta"].center_x == 2.0  # la puerta no se mueve por un mueble


# --- Determinismo y utilidades -----------------------------------------------------------------------


def test_the_result_is_deterministic():
    scene = [
        laptop(),
        bed(),
        bed("Cama 2", x=2.5),
        Item("Espejo", "wall", 0.8, 0.05, 1.0, 2.0, 0.6, elevation_m=0.2),
        Item("Puerta", "wall", 0.9, 0.1, 2.1, 3.9, 1.5),
    ]

    assert arrange_objects(ROOM, scene) == arrange_objects(ROOM, scene)


def test_the_input_objects_are_not_modified():
    original = [laptop(), bed()]
    copy = list(original)

    arrange_objects(ROOM, original)

    assert original == copy


@pytest.mark.parametrize(
    ("name", "door"),
    [("Puerta", True), ("PUERTA principal", True), ("Door", True), ("Ventana", False), ("Espejo", False)],
)
def test_doors_are_recognized_by_their_normalized_name(name, door):
    assert is_door(Item(name, "wall", 0.9, 0.1, 2.0, 1.0, 0.05)) is door


def test_a_floor_object_named_door_is_not_a_door():
    assert not is_door(Item("Puerta", "floor", 0.9, 0.1, 2.0, 1.0, 0.05))


def test_normalized_removes_accents_and_case():
    assert normalized("Pórtico ÁÉÍÓÚ") == "portico aeiou"
    assert overlap_reason("Cama") == "se solapa con Cama"


def test_snapping_breaks_ties_in_a_fixed_order():
    square = Room(width_m=4.0, length_m=4.0)
    centered = Item("Cuadro", "wall", 0.5, 0.02, 0.5, 2.0, 2.0, elevation_m=1.0)

    snapped, axis = snap_to_wall(centered, square)

    assert (snapped.center_z, axis) == (pytest.approx(0.01), "x")  # el fondo gana el empate
