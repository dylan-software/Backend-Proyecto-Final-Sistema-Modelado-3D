"""FASE 4: objetos bien pegados a las paredes en la foto a 3D (app/geometry/arrangement.py)."""

import random
from dataclasses import dataclass, replace

import pytest

from app.geometry.arrangement import (
    WALL_SNAP_DISTANCE_M,
    _arrange,
    arrange_objects,
    footprint,
    goes_against_wall,
    inside_room,
    is_bed,
    overlaps,
    slide_along_wall,
    snap_furniture_to_wall,
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

# La escena del plan: cama con el cabecero a 15 cm del fondo, tocador a 12,5 cm de la pared izquierda,
# armario a 2 cm del fondo metido en la puerta y la puerta en la pared del fondo (x de 3,0 a 3,9).
BED = Item("Cama", "floor", 1.4, 1.9, 0.5, 1.2, 1.1)
DRESSER = Item("Tocador", "floor", 0.45, 1.0, 0.8, 0.35, 2.2)
WARDROBE = Item("Armario", "floor", 1.0, 0.6, 2.0, 3.3, 0.32)
DOOR = Item("Puerta", "wall", 0.9, 0.05, 2.0, 3.45, 0.02)
SCENE = [BED, DRESSER, WARDROBE, DOOR]


def by_name(items):
    return {item.name: item for item in items}


def wall_gaps(item, room):
    x0, x1, z0, z1 = footprint(item)
    return {"back": z0, "left": x0, "right": room.width_m - x1, "front": room.length_m - z1}


def no_overlaps(items):
    return all(not overlaps(a, b) for i, a in enumerate(items) for b in items[i + 1 :])


# --- La escena de la cama, el tocador, el armario y la puerta ------------------------------------------


def test_the_plan_scene_puts_every_piece_against_its_wall():
    kept, dropped = arrange_objects(ROOM, SCENE)

    placed = by_name(kept)
    assert dropped == []
    assert [item.name for item in kept] == ["Cama", "Tocador", "Armario", "Puerta"]  # orden original
    assert no_overlaps(kept)
    # Cama: el cabecero contra el fondo; x no cambia.
    assert footprint(placed["Cama"]) == pytest.approx((0.5, 1.9, 0.0, 1.9))
    # Tocador: contra la pared izquierda; z no cambia.
    assert footprint(placed["Tocador"]) == pytest.approx((0.0, 0.45, 1.7, 2.7))
    # Armario: contra el fondo y deslizado a lo largo de él hasta justo antes de la puerta (1 cm).
    assert footprint(placed["Armario"]) == pytest.approx((1.99, 2.99, 0.0, 0.6))
    # La puerta no se mueve por un mueble y queda a 0 cm de su pared.
    assert placed["Puerta"] == replace(DOOR, center_z=0.025)


def test_the_plan_scene_is_deterministic_and_does_not_modify_its_input():
    copy = list(SCENE)

    assert arrange_objects(ROOM, SCENE) == arrange_objects(ROOM, SCENE)
    assert SCENE == copy


def test_a_wardrobe_by_the_door_slides_past_other_furniture_on_the_same_wall():
    desk = Item("Escritorio", "floor", 1.2, 0.6, 0.75, 2.2, 0.3)  # x de 1,6 a 2,8, contra el fondo
    wardrobe = Item("Armario", "floor", 1.0, 0.6, 2.0, 3.35, 0.35)  # 5 cm del fondo, 15 de la derecha

    kept, dropped = arrange_objects(ROOM, [desk, wardrobe, DOOR])

    placed = by_name(kept)
    assert dropped == []
    # Junto a la puerta choca con el escritorio, así que sigue hasta el otro lado del escritorio.
    assert footprint(placed["Armario"]) == pytest.approx((0.59, 1.59, 0.0, 0.6))
    assert placed["Escritorio"] == desk
    assert no_overlaps(kept)


def test_a_wardrobe_with_no_room_on_its_wall_follows_the_general_rule_and_is_kept():
    narrow = Room(width_m=2.0, length_m=3.0)
    door = Item("Puerta", "wall", 0.9, 0.05, 2.0, 1.0, 0.02)  # x de 0,55 a 1,45
    wardrobe = Item("Armario", "floor", 1.0, 0.6, 2.0, 1.0, 0.32)  # no cabe a ningún lado de la puerta

    kept, dropped = arrange_objects(narrow, [wardrobe, door])

    placed = by_name(kept)
    assert dropped == []
    assert no_overlaps(kept)
    assert inside_room(placed["Armario"], narrow)
    # Como antes de la FASE 4: se aparta lo mínimo, delante de la puerta.
    assert footprint(placed["Armario"]) == pytest.approx((0.5, 1.5, 0.06, 0.66))


# --- Qué se arrima y a qué pared --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Armario", "Ropero", "Tocador", "Escritorio", "Estante", "Estantería de libros", "Librero", "Desk"],
)
def test_wall_furniture_closer_than_30_cm_goes_to_the_nearest_wall(name):
    piece = Item(name, "floor", 1.0, 0.5, 1.0, 2.0, 0.45)  # z de 0,2 a 0,7: a 20 cm del fondo

    snapped, axis = snap_furniture_to_wall(piece, ROOM)

    assert axis == "x"
    assert footprint(snapped) == pytest.approx((1.5, 2.5, 0.0, 0.5))
    assert arrange_objects(ROOM, [piece]) == ([snapped], [])


@pytest.mark.parametrize("name", ["Mesa", "Silla", "Cámara", "Sofá", "Mueble 3"])
def test_other_furniture_is_not_moved_to_the_wall(name):
    piece = Item(name, "floor", 1.0, 0.5, 1.0, 2.0, 0.45)

    assert not goes_against_wall(piece)
    assert snap_furniture_to_wall(piece, ROOM) is None
    assert arrange_objects(ROOM, [piece]) == ([piece], [])


def test_the_limit_is_strictly_less_than_30_cm():
    at_limit = Item("Armario", "floor", 1.0, 0.6, 2.0, 2.0, WALL_SNAP_DISTANCE_M + 0.3)  # z0 = 0,30
    just_inside = replace(at_limit, center_z=0.29 + 0.3)  # z0 = 0,29

    assert snap_furniture_to_wall(at_limit, ROOM) is None
    assert wall_gaps(snap_furniture_to_wall(just_inside, ROOM)[0], ROOM)["back"] == pytest.approx(0)


def test_in_a_corner_the_nearest_wall_wins_and_ties_follow_the_fixed_order():
    corner = Item("Armario", "floor", 1.0, 0.6, 2.0, 0.7, 0.4)  # 20 cm de la izquierda, 10 del fondo
    snapped, axis = snap_furniture_to_wall(corner, ROOM)
    assert (axis, footprint(snapped)) == ("x", pytest.approx((0.2, 1.2, 0.0, 0.6)))

    tie = Item("Armario", "floor", 1.0, 0.5, 2.0, 0.75, 0.5)  # 25 cm de las dos (exacto en binario)
    snapped, axis = snap_furniture_to_wall(tie, ROOM)
    assert (axis, wall_gaps(snapped, ROOM)["back"]) == ("x", pytest.approx(0))  # el fondo gana el empate


def test_a_bed_goes_to_the_wall_only_by_its_headboard():
    # Larga en z: el cabecero solo puede ir al fondo o al frente. El lado largo está a 10 cm de la
    # pared izquierda, pero eso no la arrima; el cabecero está a 50 cm del fondo: tampoco.
    side_by_wall = Item("Cama doble", "floor", 1.4, 1.9, 0.5, 0.8, 1.45)
    assert is_bed(side_by_wall)
    assert snap_furniture_to_wall(side_by_wall, ROOM) is None

    # Larga en x: el cabecero va a la pared derecha (a 20 cm), no al fondo (a 5 cm, su lado largo).
    across = Item("Cama", "floor", 1.9, 1.4, 0.5, 2.85, 0.75)
    snapped, axis = snap_furniture_to_wall(across, ROOM)
    assert (axis, footprint(snapped)) == ("z", pytest.approx((2.1, 4.0, 0.05, 1.45)))


def test_a_small_shelf_on_a_desk_stays_on_the_desk():
    desk = Item("Escritorio", "floor", 1.2, 0.6, 0.75, 2.0, 0.3)
    shelf = Item("Estante", "floor", 0.4, 0.2, 0.3, 2.0, 0.25)  # pequeño, con el centro sobre el escritorio

    kept, _ = arrange_objects(ROOM, [desk, shelf])

    assert by_name(kept)["Estante"] == replace(shelf, elevation_m=0.75)


def test_slide_along_wall_keeps_the_piece_on_its_wall():
    placed = [replace(DOOR, center_z=0.025)]
    snapped, axis = snap_furniture_to_wall(WARDROBE, ROOM)

    slid = slide_along_wall(snapped, axis, placed, ROOM)

    assert slid.center_z == snapped.center_z  # solo cambia la coordenada a lo largo de la pared
    assert footprint(slid)[1] == pytest.approx(3.0 - 0.01)
    # Sin nada que estorbe, se queda donde está.
    assert slide_along_wall(snapped, axis, [], ROOM) == snapped


# --- Propiedades sobre escenas aleatorias ------------------------------------------------------------------

FLOOR_NAMES = ["Cama", "Armario", "Tocador", "Escritorio", "Estante", "Mesa", "Silla", "Baúl"]
WALL_NAMES = ["Puerta", "Ventana", "Espejo", "Cuadro"]


def random_scene(rng):
    width, length = round(rng.uniform(2.0, 6.0), 2), round(rng.uniform(2.0, 6.0), 2)
    room = Room(width, length, round(rng.uniform(2.2, 3.0), 2))
    items = []
    for i in range(rng.randint(3, 10)):
        if rng.random() < 0.35:
            width, depth = round(rng.uniform(0.2, 1.5), 2), round(rng.uniform(0.02, 0.1), 2)
            height = round(rng.uniform(0.2, 2.1), 2)
            name, kind, elevation = f"{rng.choice(WALL_NAMES)} {i}", "wall", round(rng.uniform(0, 1.2), 2)
        else:
            width, depth = round(rng.uniform(0.3, 2.0), 2), round(rng.uniform(0.3, 2.0), 2)
            height = round(rng.uniform(0.3, 2.1), 2)
            name, kind, elevation = f"{rng.choice(FLOOR_NAMES)} {i}", "floor", 0.0
        width, depth = min(width, room.width_m), min(depth, room.length_m)
        # Muchos cerca de las paredes, para que se arrimen y choquen.
        x = rng.uniform(width / 2, room.width_m - width / 2)
        z = rng.uniform(depth / 2, room.length_m - depth / 2)
        items.append(Item(name, kind, width, depth, height, round(x, 2), round(z, 2), elevation))
    return room, items


@pytest.mark.parametrize("seed", range(200))
def test_random_scenes_keep_wall_objects_flush_and_nothing_overlaps(seed):
    room, items = random_scene(random.Random(seed))

    kept, dropped = arrange_objects(room, items)

    assert len(kept) + len(dropped) == len(items)
    assert no_overlaps(kept)
    assert all(inside_room(item, room) for item in kept)
    for item in kept:
        gaps = wall_gaps(item, room)
        if item.kind == "wall":
            # A 0 cm de una pared (sin atravesarla: inside_room) y dentro de su tramo.
            assert min(gaps.values()) == pytest.approx(0, abs=1e-9)
            assert min(gaps.values()) >= -1e-9
    assert arrange_objects(room, items) == (kept, dropped)
    # Arrimar los muebles nunca hace perder un objeto que sin arrimar cabía.
    # (Los nombres de la escena son únicos; el motivo puede cambiar porque estorba otro objeto.)
    _, plain_dropped = _arrange(room, items, snap=False)
    assert {name for name, _ in dropped} <= {name for name, _ in plain_dropped.values()}
