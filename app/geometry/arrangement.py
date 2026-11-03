"""Coherencia espacial de los objetos que la IA estima a partir de una foto.

Después de validar la respuesta del modelo, los objetos se recolocan de forma DETERMINISTA (sin azar
ni IA; mismo resultado con la misma entrada) en una sala rectangular [0, W] x [0, L] x [0, H]:

1. Puertas, ventanas, espejos y cuadros (kind="wall") se pegan a la pared más cercana, paralelos a
   ella. En una pared vertical (x = 0 o x = W) se intercambian ancho y fondo. La puerta va al suelo;
   el resto se acota entre 0 y la altura de la sala.
2. Los objetos de suelo se procesan de mayor a menor área. Uno pequeño (menos de 0,6 m de alto y
   menos del 40 % del área) con el centro dentro de otro de suelo se coloca ENCIMA; si no, se desplaza
   lo mínimo a un lado del que estorba, dentro de la sala. Si no cabe, se descarta.
3. Lo que está apoyado encima de otro solo toca su tapa: no cuenta como solape.
4. Un objeto de pared que se mete en un mueble sube a la tapa del mueble (el espejo sobre la cama).

Los objetos son dataclasses con name, kind, width_m (eje x), depth_m (eje z), height_m, center_x,
center_z y elevation_m (base). Se devuelven copias; las entradas no se modifican.
"""

import unicodedata
from dataclasses import replace

# Objeto pequeño que se puede poner encima de otro (en metros y fracción del área del de abajo).
STACK_MAX_HEIGHT_M = 0.6
STACK_MAX_AREA_RATIO = 0.4
# Separación al desplazar un objeto junto al que le estorba.
GAP_M = 0.01
EPSILON = 1e-9
# Palabras que identifican una puerta en el nombre normalizado (sin tildes, minúsculas).
DOOR_WORDS = ("puerta", "door")

REASON_OUTSIDE = "fuera de la habitación"


def overlap_reason(name):
    return f"se solapa con {name}"


def normalized(text):
    """Minúsculas y sin tildes: "Puerta Corrediza" -> "puerta corrediza"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def is_door(item):
    return item.kind == "wall" and any(word in normalized(item.name) for word in DOOR_WORDS)


def footprint(item):
    """(min_x, max_x, min_z, max_z) de la huella."""
    half_w, half_d = item.width_m / 2, item.depth_m / 2
    return item.center_x - half_w, item.center_x + half_w, item.center_z - half_d, item.center_z + half_d


def area(item):
    return item.width_m * item.depth_m


def _span_overlap(low_a, high_a, low_b, high_b):
    return min(high_a, high_b) - max(low_a, low_b)


def overlaps(a, b):
    """True si las dos cajas comparten volumen. Tocarse (apoyado encima, pegado al lado) no cuenta."""
    ax0, ax1, az0, az1 = footprint(a)
    bx0, bx1, bz0, bz1 = footprint(b)
    return (
        _span_overlap(ax0, ax1, bx0, bx1) > EPSILON
        and _span_overlap(az0, az1, bz0, bz1) > EPSILON
        and _span_overlap(
            a.elevation_m, a.elevation_m + a.height_m, b.elevation_m, b.elevation_m + b.height_m
        )
        > EPSILON
    )


def inside_room(item, room):
    x0, x1, z0, z1 = footprint(item)
    return (
        x0 >= -EPSILON
        and z0 >= -EPSILON
        and x1 <= room.width_m + EPSILON
        and z1 <= room.length_m + EPSILON
        and item.elevation_m >= -EPSILON
        and item.elevation_m + item.height_m <= room.height_m + EPSILON
    )


def _clamp(value, low, high):
    return min(max(value, low), high)


def snap_to_wall(item, room):
    """El objeto de pared pegado a la pared más cercana a su centro, o None si no cabe.

    width_m es su ancho a lo largo de la pared y depth_m su grosor, como los da la IA.

    Returns:
        (objeto, eje en el que puede deslizarse: "x" o "z"), o None.
    """
    along, thickness = item.width_m, item.depth_m
    distances = [
        (item.center_z, "back"),  # z = 0
        (item.center_x, "left"),  # x = 0
        (room.width_m - item.center_x, "right"),  # x = W
        (room.length_m - item.center_z, "front"),  # z = L
    ]
    # min es estable: con distancias iguales gana la primera de la lista (orden fijo).
    side = min(distances, key=lambda entry: entry[0])[1]
    if side in ("back", "front"):
        if along > room.width_m + EPSILON or thickness > room.length_m + EPSILON:
            return None
        center_z = thickness / 2 if side == "back" else room.length_m - thickness / 2
        center_x = _clamp(item.center_x, along / 2, room.width_m - along / 2)
        snapped = replace(item, width_m=along, depth_m=thickness, center_x=center_x, center_z=center_z)
        axis = "x"
    else:
        if along > room.length_m + EPSILON or thickness > room.width_m + EPSILON:
            return None
        center_x = thickness / 2 if side == "left" else room.width_m - thickness / 2
        center_z = _clamp(item.center_z, along / 2, room.length_m - along / 2)
        # Pared vertical: el ancho va a lo largo de z, así que se intercambian ancho y fondo.
        snapped = replace(item, width_m=thickness, depth_m=along, center_x=center_x, center_z=center_z)
        axis = "z"
    if item.height_m > room.height_m + EPSILON:
        return None
    elevation = 0.0 if is_door(item) else _clamp(item.elevation_m, 0.0, room.height_m - item.height_m)
    return replace(snapped, elevation_m=elevation), axis


def _shift_candidates(item, blockers, axes):
    """Posiciones pegadas a cada objeto que estorba, a cada lado de los ejes dados, de menor a mayor
    desplazamiento (empates: orden de los que estorban y lados en orden fijo)."""
    candidates = []
    for order, blocker in enumerate(blockers):
        bx0, bx1, bz0, bz1 = footprint(blocker)
        if "x" in axes:
            for side, center_x in enumerate((bx0 - GAP_M - item.width_m / 2, bx1 + GAP_M + item.width_m / 2)):
                candidates.append(
                    (abs(center_x - item.center_x), order, side, replace(item, center_x=center_x))
                )
        if "z" in axes:
            for side, center_z in enumerate((bz0 - GAP_M - item.depth_m / 2, bz1 + GAP_M + item.depth_m / 2)):
                candidates.append(
                    (abs(center_z - item.center_z), order, 2 + side, replace(item, center_z=center_z))
                )
    candidates.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in candidates]


def _free_position(item, placed, room, axes):
    """item si no choca con nada; si no, la posición desplazada más cercana libre, o None."""
    blockers = [other for other in placed if overlaps(item, other)]
    if not blockers:
        return item
    for candidate in _shift_candidates(item, blockers, axes):
        if inside_room(candidate, room) and not any(overlaps(candidate, other) for other in placed):
            return candidate
    return None


def _stack_on(item, placed, room):
    """item puesto encima de un objeto de suelo que lo contiene, o None si no se puede apilar."""
    for base in placed:
        # Solo sobre objetos de suelo que están en el suelo: no se apila sobre algo ya apilado.
        if base.kind != "floor" or base.elevation_m > EPSILON or not overlaps(item, base):
            continue
        bx0, bx1, bz0, bz1 = footprint(base)
        center_inside = bx0 <= item.center_x <= bx1 and bz0 <= item.center_z <= bz1
        small = item.height_m < STACK_MAX_HEIGHT_M and area(item) < STACK_MAX_AREA_RATIO * area(base)
        if not (center_inside and small):
            continue
        stacked = replace(item, elevation_m=base.elevation_m + base.height_m)
        if inside_room(stacked, room) and not any(overlaps(stacked, other) for other in placed):
            return stacked
    return None


def _first_blocker_name(item, placed):
    return next(other.name for other in placed if overlaps(item, other))


def _place_floor(item, placed, room):
    """(objeto colocado, None) o (None, motivo de descarte)."""
    if not any(overlaps(item, other) for other in placed):
        return item, None
    stacked = _stack_on(item, placed, room)
    if stacked is not None:
        return stacked, None
    moved = _free_position(item, placed, room, axes=("x", "z"))
    if moved is not None:
        return moved, None
    return None, overlap_reason(_first_blocker_name(item, placed))


def _raise_above_furniture(item, placed, room):
    """Sube un objeto de pared hasta la tapa de los muebles de suelo con los que choca, o None."""
    for _ in range(len(placed) + 1):
        hits = [other for other in placed if other.kind == "floor" and overlaps(item, other)]
        if not hits:
            return item
        top = max(other.elevation_m + other.height_m for other in hits)
        if top + item.height_m > room.height_m + EPSILON:
            return None
        item = replace(item, elevation_m=top)
    return None


def _place_wall(item, placed, room):
    """(objeto colocado, None) o (None, motivo de descarte)."""
    snapped = snap_to_wall(item, room)
    if snapped is None:
        return None, REASON_OUTSIDE
    item, axis = snapped
    if not is_door(item):
        raised = _raise_above_furniture(item, placed, room)
        if raised is None:
            return None, overlap_reason(_first_blocker_name(item, placed))
        item = raised
    # Contra otros objetos de pared (o una puerta contra un mueble), solo se desliza a lo largo de la pared.
    moved = _free_position(item, placed, room, axes=(axis,))
    if moved is None:
        return None, overlap_reason(_first_blocker_name(item, placed))
    return moved, None


def arrange_objects(room, items):
    """Recoloca los objetos para que no se atraviesen.

    Orden de proceso: puertas, objetos de suelo de mayor a menor área (empates por orden original) y
    el resto de objetos de pared.

    Args:
        room: con width_m, length_m y height_m.
        items: objetos ya validados y dentro de la sala, en el orden del modelo.

    Returns:
        (colocados en el orden original, [(nombre, motivo)] de los descartados en el orden original).
    """
    indexed = list(enumerate(items))
    doors = [(i, item) for i, item in indexed if is_door(item)]
    floor = sorted(
        ((i, item) for i, item in indexed if item.kind == "floor"),
        key=lambda entry: (-area(entry[1]), entry[0]),
    )
    walls = [(i, item) for i, item in indexed if item.kind == "wall" and not is_door(item)]

    placed, kept, dropped = [], {}, {}
    for i, item in doors + floor + walls:
        place = _place_floor if item.kind == "floor" else _place_wall
        result, reason = place(item, placed, room)
        if result is None:
            dropped[i] = (item.name, reason)
        else:
            placed.append(result)
            kept[i] = result
    return [kept[i] for i in sorted(kept)], [dropped[i] for i in sorted(dropped)]
