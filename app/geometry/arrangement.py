"""Coherencia espacial de los objetos que la IA estima a partir de una foto.

Después de validar la respuesta del modelo, los objetos se recolocan de forma DETERMINISTA (sin azar
ni IA; mismo resultado con la misma entrada) en una sala rectangular [0, W] x [0, L] x [0, H]:

1. Puertas, ventanas, espejos y cuadros (kind="wall") se pegan a la pared más cercana, paralelos a
   ella. En una pared vertical (x = 0 o x = W) se intercambian ancho y fondo. La puerta va al suelo;
   el resto se acota entre 0 y la altura de la sala.
   Quedan a 0 cm de la pared (su cara trasera sobre ella, sin atravesarla) y dentro de su tramo.
2. Los objetos de suelo se procesan de mayor a menor área. Uno pequeño (menos de 0,6 m de alto y
   menos del 40 % del área) con el centro dentro de otro de suelo se coloca ENCIMA; si no, se desplaza
   lo mínimo a un lado del que estorba, dentro de la sala. Si no cabe, se descarta.
   Los muebles que van contra una pared (armario, cama, tocador, escritorio, estante; por su nombre) a
   menos de WALL_SNAP_DISTANCE_M de la pared más cercana se arriman a ella (la cama, por el cabecero:
   solo a una pared de los extremos de su lado largo). Si ahí chocan, se deslizan a lo largo de esa
   pared hasta el hueco libre más cercano (el armario junto a la puerta); si no hay ninguno, siguen la
   regla general. Si arrimar hace que se descarte algún objeto que sin arrimar cabía, se usa la
   distribución sin arrimar (arrange_objects).
3. Lo que está apoyado encima de otro solo toca su tapa: no cuenta como solape.
4. Un objeto de pared que se mete en un mueble sube a la tapa del mueble (el espejo sobre la cama).

Los objetos son dataclasses con name, kind, width_m (eje x), depth_m (eje z), height_m, center_x,
center_z y elevation_m (base). Se devuelven copias; las entradas no se modifican.

Además, la colocación de UN objeto en una sala guardada (CONTRATO-FASE0.md), con la misma lógica:
place_on_wall (pared pedida de una sala rectangular), first_free_floor_spot (primer hueco del suelo dentro
del polígono real) y settle_at (revalidar una posición pedida). Esas funciones reciben la sala como
app.geometry.room.Room, en sus propias coordenadas (el eje y del polígono es el eje z de los objetos).
"""

import math
import re
import unicodedata
from dataclasses import dataclass, replace

from app.geometry.layout import MAX_PIECE_WORK, _steps, grid_step
from app.structures import FixedArray

# Objeto pequeño que se puede poner encima de otro (en metros y fracción del área del de abajo).
STACK_MAX_HEIGHT_M = 0.6
STACK_MAX_AREA_RATIO = 0.4
# Separación al desplazar un objeto junto al que le estorba.
GAP_M = 0.01
EPSILON = 1e-9
# Palabras que identifican una puerta en el nombre normalizado (sin tildes, minúsculas).
DOOR_WORDS = ("puerta", "door")
# Muebles de suelo que normalmente van contra una pared: palabras COMPLETAS del nombre normalizado
# ("cámara" no es una cama). La cama se arrima por el cabecero.
BED_WORDS = frozenset({"cama", "camas", "litera", "literas", "bed", "beds"})
WALL_FURNITURE_WORDS = BED_WORDS | frozenset(
    {
        "armario", "armarios", "ropero", "roperos", "closet", "wardrobe",
        "tocador", "tocadores", "dresser", "vanity",
        "escritorio", "escritorios", "desk",
        "estante", "estantes", "estanteria", "estanterias", "librero", "shelf", "shelves", "bookcase",
        "bookshelf",
    }
)  # fmt: skip
# Distancia (en metros) por debajo de la cual esos muebles se arriman a la pared más cercana.
WALL_SNAP_DISTANCE_M = 0.30

REASON_OUTSIDE = "fuera de la habitación"
REASON_TOO_TALL = "más alto que la sala"
REASON_NO_SPACE = "sin sitio libre"

# Paredes de una sala rectangular, en el orden de desempate de snap_to_wall: back = z mínima,
# left = x mínima, right = x máxima, front = z máxima.
WALL_SIDES = ("back", "left", "right", "front")
# Distancia a la que un objeto cuenta como pegado a una pared.
ATTACH_TOLERANCE_M = 0.01
# Relativa al área: el polígono es un rectángulo si su área es la de su caja delimitadora.
RECTANGLE_AREA_TOLERANCE = 1e-6
# Celdas por lado de la rejilla del suelo (FloorCells): 16 x 16 = 256 casillas fijas.
FLOOR_CELLS_PER_SIDE = 16


@dataclass(frozen=True)
class RoomBox:
    """Sala rectangular [0, width_m] x [0, length_m] x [0, height_m]: el marco de este módulo."""

    width_m: float
    length_m: float
    height_m: float


def overlap_reason(name):
    return f"se solapa con {name}"


def normalized(text):
    """Minúsculas y sin tildes: "Puerta Corrediza" -> "puerta corrediza"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def is_door(item):
    return item.kind == "wall" and any(word in normalized(item.name) for word in DOOR_WORDS)


def _name_words(item):
    """Las palabras del nombre normalizado: "Armario-ropero 2" -> {"armario", "ropero", "2"}."""
    return set(re.findall(r"[a-z0-9]+", normalized(item.name)))


def is_bed(item):
    return item.kind == "floor" and bool(_name_words(item) & BED_WORDS)


def goes_against_wall(item):
    """Si es un mueble de suelo que normalmente va contra una pared (armario, cama, tocador...)."""
    return item.kind == "floor" and bool(_name_words(item) & WALL_FURNITURE_WORDS)


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


# --- Ocupación con arreglos de tamaño fijo (FixedArray) ---------------------------------------------


class FloorCells:
    """Celdas del suelo: una rejilla fija sobre la caja de la sala que dice qué objetos hay cerca.

    La caja se parte en FLOOR_CELLS_PER_SIDE x FLOOR_CELLS_PER_SIDE celdas guardadas en un FixedArray
    (fila por fila: casilla = fila * columnas + columna). Cada casilla tiene la lista de posiciones (en
    items) de los objetos cuya huella toca esa celda, con una celda de margen a cada lado; lo que cae
    fuera de la caja va a la celda del borde. near(objeto) devuelve, en el orden de items, los objetos
    registrados en las celdas que toca su huella.

    Es un prefiltro EXACTO: si dos huellas se solapan comparten un punto, y ese punto cae en una celda
    de las dos (la cuenta de la celda es monótona), así que near nunca deja fuera un objeto que choque;
    overlaps decide después igual que antes.

    Costes (c = celdas por lado, n = objetos, k = celdas que toca una huella, m = objetos devueltos):
        construir: O(c² + suma de las k de cada objeto); near: O(k + m log m).
    Memoria: O(c² + suma de las k).
    """

    __slots__ = ("_cell_d", "_cell_w", "_cells", "_items", "_min_x", "_min_z", "_side")

    def __init__(self, box, items):
        """box: caja delimitadora (min_x, min_y, max_x, max_y; su y es el eje z). items: objetos colocados."""
        self._side = FLOOR_CELLS_PER_SIDE
        self._min_x, self._min_z = box.min_x, box.min_y
        self._cell_w = (box.max_x - box.min_x) / self._side or 1.0
        self._cell_d = (box.max_y - box.min_y) / self._side or 1.0
        self._cells = FixedArray(self._side * self._side)
        self._items = FixedArray(len(items)) if items else None
        for position, item in enumerate(items):
            self._items[position] = item
            first_col, last_col, first_row, last_row = self._span(item)
            for row in range(max(first_row - 1, 0), min(last_row + 1, self._side - 1) + 1):
                for col in range(max(first_col - 1, 0), min(last_col + 1, self._side - 1) + 1):
                    slot = row * self._side + col
                    if self._cells[slot] is None:
                        self._cells[slot] = []
                    self._cells[slot].append(position)

    def _cell(self, value, origin, size):
        """Índice de celda de una coordenada, acotado a la rejilla. O(1)."""
        return min(max(math.floor((value - origin) / size), 0), self._side - 1)

    def _span(self, item):
        """(primera columna, última columna, primera fila, última fila) que toca la huella. O(1)."""
        x0, x1, z0, z1 = footprint(item)
        return (
            self._cell(x0, self._min_x, self._cell_w),
            self._cell(x1, self._min_x, self._cell_w),
            self._cell(z0, self._min_z, self._cell_d),
            self._cell(z1, self._min_z, self._cell_d),
        )

    def near(self, item):
        """Los objetos que pueden solaparse con item, sin repetir y en el orden en que se dieron."""
        if self._items is None:
            return []
        first_col, last_col, first_row, last_row = self._span(item)
        found = set()
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                found.update(self._cells[row * self._side + col] or ())
        return [self._items[position] for position in sorted(found)]


def wall_strip(item, axis, placed):
    """Los objetos que ocupan la franja de pared de item: los únicos con los que puede chocar al deslizarse.

    Un objeto pegado a su pared solo se mueve a lo largo de axis ("x" o "z"); su extensión en el otro eje
    (la franja) no cambia. Un objeto cuya huella no se solapa con esa franja nunca puede chocar con él,
    así que se descarta (la misma condición que usa overlaps). Los que quedan, con sus tramos a lo largo
    de la pared, se guardan en un FixedArray del tamaño justo, en el orden de placed.

    Returns:
        FixedArray con los objetos de la franja, o () si no hay ninguno. O(n), dos pasadas.
    """
    x0, x1, z0, z1 = footprint(item)
    low, high = (z0, z1) if axis == "x" else (x0, x1)

    def in_strip(other):
        ox0, ox1, oz0, oz1 = footprint(other)
        other_low, other_high = (oz0, oz1) if axis == "x" else (ox0, ox1)
        return _span_overlap(low, high, other_low, other_high) > EPSILON

    count = sum(1 for other in placed if in_strip(other))
    if count == 0:
        return ()
    strip = FixedArray(count)
    position = 0
    for other in placed:
        if in_strip(other):
            strip[position] = other
            position += 1
    return strip


def snap_to_wall(item, room):
    """El objeto de pared pegado a la pared más cercana a su centro, o None si no cabe.

    width_m es su ancho a lo largo de la pared y depth_m su grosor, como los da la IA.

    Returns:
        (objeto, eje en el que puede deslizarse: "x" o "z"), o None.
    """
    distances = [
        (item.center_z, "back"),  # z = 0
        (item.center_x, "left"),  # x = 0
        (room.width_m - item.center_x, "right"),  # x = W
        (room.length_m - item.center_z, "front"),  # z = L
    ]
    # min es estable: con distancias iguales gana la primera de la lista (orden fijo).
    side = min(distances, key=lambda entry: entry[0])[1]
    return snap_to_side(item, room, side)


def snap_to_side(item, room, side):
    """El objeto de pared pegado a la pared side ("back", "left", "right" o "front"), o None si no cabe.

    width_m es su ancho a lo largo de la pared y depth_m su grosor. La coordenada a lo largo de la pared
    se conserva, acotada al tramo.

    Returns:
        (objeto, eje en el que puede deslizarse: "x" o "z"), o None.
    """
    along, thickness = item.width_m, item.depth_m
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


def _free_position(item, placed, fits, axes):
    """item si no choca con nada; si no, la posición desplazada más cercana libre, o None.

    fits(candidato) dice si un candidato desplazado queda dentro de la sala.
    """
    blockers = [other for other in placed if overlaps(item, other)]
    if not blockers:
        return item
    for candidate in _shift_candidates(item, blockers, axes):
        if fits(candidate) and not any(overlaps(candidate, other) for other in placed):
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


def _headboard_sides(item):
    """Paredes a las que puede ir el cabecero de una cama: las de los extremos de su lado largo."""
    if item.width_m > item.depth_m + EPSILON:
        return ("left", "right")  # larga en x: el cabecero mira a una pared x = 0 o x = W
    if item.depth_m > item.width_m + EPSILON:
        return ("back", "front")
    return WALL_SIDES  # cuadrada: cualquier pared


def snap_furniture_to_wall(item, room):
    """Un mueble de pared (goes_against_wall) arrimado a la pared más cercana, si está a menos de
    WALL_SNAP_DISTANCE_M de ella; la cama, solo por el cabecero. No gira el mueble ni lo mueve a lo
    largo de la pared. Empates: orden de WALL_SIDES.

    Returns:
        (objeto pegado, eje en el que puede deslizarse: "x" o "z"), o None si no se arrima (otro
        mueble, apoyado encima de algo o lejos de las paredes). O(1).
    """
    if not goes_against_wall(item) or item.elevation_m > EPSILON:
        return None
    x0, x1, z0, z1 = footprint(item)
    gaps = {"back": z0, "left": x0, "right": room.width_m - x1, "front": room.length_m - z1}
    sides = _headboard_sides(item) if is_bed(item) else WALL_SIDES
    # "A menos de 30 cm": con la holgura, 30 cm con ruido de coma flotante no cuentan.
    limit = WALL_SNAP_DISTANCE_M - EPSILON
    near = [(gaps[side], WALL_SIDES.index(side), side) for side in sides if gaps[side] < limit]
    if not near:
        return None
    side = min(near)[2]
    if side == "back":
        return replace(item, center_z=item.depth_m / 2), "x"
    if side == "front":
        return replace(item, center_z=room.length_m - item.depth_m / 2), "x"
    if side == "left":
        return replace(item, center_x=item.width_m / 2), "z"
    return replace(item, center_x=room.width_m - item.width_m / 2), "z"


def slide_along_wall(item, axis, placed, room):
    """item deslizado SOLO a lo largo de axis hasta la posición libre más cercana, o None.

    Al contrario que _free_position (que solo prueba junto a los objetos que estorban en la posición
    pedida), recorre todos los de la franja de la pared (wall_strip) que coinciden en altura: la
    posición libre más cercana es la pedida o queda pegada (a GAP_M) a un lado de uno de ellos, así que
    se prueban esas 2k + 1 posiciones de menor a mayor desplazamiento (empate: la de menor coordenada).
    Determinista. O(n + k²) con k = objetos de la franja.
    """
    item_low, item_high = item.elevation_m, item.elevation_m + item.height_m
    blockers = [
        other
        for other in wall_strip(item, axis, placed)
        if _span_overlap(item_low, item_high, other.elevation_m, other.elevation_m + other.height_m) > EPSILON
    ]
    start = item.center_x if axis == "x" else item.center_z
    half = (item.width_m if axis == "x" else item.depth_m) / 2
    positions = {start}
    for other in blockers:
        ox0, ox1, oz0, oz1 = footprint(other)
        low, high = (ox0, ox1) if axis == "x" else (oz0, oz1)
        positions.update((low - GAP_M - half, high + GAP_M + half))
    for position in sorted(positions, key=lambda value: (abs(value - start), value)):
        candidate = replace(item, center_x=position) if axis == "x" else replace(item, center_z=position)
        if inside_room(candidate, room) and not any(overlaps(candidate, other) for other in blockers):
            return candidate
    return None


def _place_floor(item, placed, room, snap=True):
    """(objeto colocado, None) o (None, motivo de descarte). Con snap, arrima los muebles de pared."""
    against = snap_furniture_to_wall(item, room) if snap else None
    # Lo que se apilaría encima de otro mueble (un estante pequeño sobre el escritorio) no se arrima.
    if against is not None and _stack_on(item, placed, room) is None:
        slid = slide_along_wall(*against, placed, room)
        if slid is not None:
            return slid, None
    if not any(overlaps(item, other) for other in placed):
        return item, None
    stacked = _stack_on(item, placed, room)
    if stacked is not None:
        return stacked, None
    moved = _free_position(item, placed, lambda candidate: inside_room(candidate, room), axes=("x", "z"))
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
    return _settle_on_wall(*snapped, placed, room)


def _settle_on_wall(item, axis, placed, room, stack=True):
    """Un objeto ya pegado a su pared: sube sobre los muebles (si stack) y se desliza si choca.

    Returns:
        (objeto, None) o (None, motivo).
    """
    # Solo cuentan los objetos de la franja de esta pared (tramos ocupados): el resultado es el mismo.
    placed = wall_strip(item, axis, placed)
    if stack and not is_door(item):
        raised = _raise_above_furniture(item, placed, room)
        if raised is None:
            return None, overlap_reason(_first_blocker_name(item, placed))
        item = raised
    # Contra otros objetos de pared (o una puerta contra un mueble), solo se desliza a lo largo de la pared.
    moved = _free_position(item, placed, lambda candidate: inside_room(candidate, room), axes=(axis,))
    if moved is None:
        return None, overlap_reason(_first_blocker_name(item, placed))
    return moved, None


def arrange_objects(room, items):
    """Recoloca los objetos para que no se atraviesen.

    Orden de proceso: puertas, objetos de suelo de mayor a menor área (empates por orden original) y
    el resto de objetos de pared.

    Los muebles de pared se arriman a ella (snap_furniture_to_wall). Arrimar uno le quita sitio a los
    que vienen después, así que se distribuye también sin arrimar y, si con el arrimado se descarta
    algún objeto que sin él cabía, se devuelve la distribución sin arrimar: nunca se pierde un objeto por
    arrimar los muebles. Determinista; el coste, como mucho, el doble.

    Args:
        room: con width_m, length_m y height_m.
        items: objetos ya validados y dentro de la sala, en el orden del modelo.

    Returns:
        (colocados en el orden original, [(nombre, motivo)] de los descartados en el orden original).
    """
    kept, dropped = _arrange(room, items, snap=True)
    if not dropped:
        return _in_order(kept), _in_order(dropped)
    plain_kept, plain_dropped = _arrange(room, items, snap=False)
    if dropped.keys() <= plain_dropped.keys():
        return _in_order(kept), _in_order(dropped)
    return _in_order(plain_kept), _in_order(plain_dropped)


def _in_order(by_index):
    return [by_index[i] for i in sorted(by_index)]


def _arrange(room, items, snap):
    """({índice: colocado}, {índice: (nombre, motivo)}) con o sin arrimar los muebles de pared."""
    indexed = list(enumerate(items))
    doors = [(i, item) for i, item in indexed if is_door(item)]
    floor = sorted(
        ((i, item) for i, item in indexed if item.kind == "floor"),
        key=lambda entry: (-area(entry[1]), entry[0]),
    )
    walls = [(i, item) for i, item in indexed if item.kind == "wall" and not is_door(item)]

    placed, kept, dropped = [], {}, {}
    for i, item in doors + floor + walls:
        if item.kind == "floor":
            result, reason = _place_floor(item, placed, room, snap)
        else:
            result, reason = _place_wall(item, placed, room)
        if result is None:
            dropped[i] = (item.name, reason)
        else:
            placed.append(result)
            kept[i] = result
    return kept, dropped


# --- Un objeto en una sala guardada (CONTRATO-FASE0.md) ----------------------------------------------


def _moved(item, dx, dz):
    return replace(item, center_x=item.center_x + dx, center_z=item.center_z + dz)


def rectangle_of(room):
    """(origen_x, origen_z, RoomBox) si el polígono de room es un rectángulo alineado con los ejes, o None.

    Un polígono simple dentro de su caja delimitadora con la misma área que ella ES esa caja, así que los
    vértices colineales o repetidos no importan.
    """
    box = room.bounding_box()
    width, length = box.max_x - box.min_x, box.max_y - box.min_y
    if abs(room.area_m2() - width * length) > RECTANGLE_AREA_TOLERANCE * max(1.0, width * length):
        return None
    return box.min_x, box.min_y, RoomBox(width, length, room.height_m)


def wall_extents(width_x, depth_z, side):
    """(ancho a lo largo de la pared, grosor) de un objeto con extensión width_x en x y depth_z en z."""
    return (width_x, depth_z) if side in ("back", "front") else (depth_z, width_x)


def attached_wall(item, room):
    """Pared a la que el objeto está pegado (a ATTACH_TOLERANCE_M o menos), o None.

    Solo en salas rectangulares; en una esquina gana la primera de WALL_SIDES.
    """
    rectangle = rectangle_of(room)
    if rectangle is None:
        return None
    origin_x, origin_z, box = rectangle
    x0, x1, z0, z1 = footprint(_moved(item, -origin_x, -origin_z))
    gaps = {"back": z0, "left": x0, "right": box.width_m - x1, "front": box.length_m - z1}
    return next((side for side in WALL_SIDES if abs(gaps[side]) <= ATTACH_TOLERANCE_M), None)


def place_on_wall(item, room, side, placed, centered=True, stack=True):
    """Pega un objeto a la pared side de una sala rectangular.

    item.width_m es su ancho a lo largo de la pared y depth_m su grosor (como los da la IA). Con
    centered=True queda en el centro de la pared; si no, conserva su coordenada a lo largo de ella,
    acotada al tramo. La base (elevation_m) se acota entre el suelo y el techo; la puerta va al suelo.
    Si choca, sube sobre los muebles de suelo (solo con stack=True) y se desliza a lo largo de la pared
    (como arrange_objects). Un mueble de suelo arrimado a la pared usa stack=False: nunca se sube
    encima de otro.

    Args:
        room: app.geometry.room.Room rectangular.
        placed: objetos que ya están en la sala, en sus coordenadas.

    Returns:
        (objeto en coordenadas de room, None) o (None, motivo).

    Raises:
        ValueError: la sala no es rectangular o side no es una pared.
    """
    if side not in WALL_SIDES:
        raise ValueError(f"pared desconocida: {side}")
    rectangle = rectangle_of(room)
    if rectangle is None:
        raise ValueError("los objetos de pared solo van en salas rectangulares")
    origin_x, origin_z, box = rectangle
    if item.height_m > box.height_m + EPSILON:
        return None, REASON_TOO_TALL
    local = _moved(item, -origin_x, -origin_z)
    if centered:
        local = replace(local, center_x=box.width_m / 2, center_z=box.length_m / 2)
    snapped = snap_to_side(local, box, side)
    if snapped is None:
        return None, REASON_OUTSIDE
    others = [_moved(other, -origin_x, -origin_z) for other in placed]
    result, reason = _settle_on_wall(*snapped, others, box, stack)
    if result is None:
        return None, reason
    return _moved(result, origin_x, origin_z), None


def first_free_floor_spot(item, room, placed, max_work=MAX_PIECE_WORK):
    """Primer hueco del suelo para item (su posición se ignora; la base queda a 0).

    Recorre la caja delimitadora desde su esquina mínima, por filas (x y luego z), con el paso grid_step
    del layout, y acepta la primera huella dentro del polígono real que no se solapa en 3D con nada de
    placed. Determinista. Cada candidato cuesta (vértices + objetos colocados) unidades, hasta max_work.
    Cada candidato solo se compara con los objetos de las celdas del suelo que toca (FloorCells), con el
    mismo resultado que comparándolo con todos.

    Returns:
        (objeto, None) o (None, motivo).
    """
    if item.height_m > room.height_m + EPSILON:
        return None, REASON_TOO_TALL
    box = room.bounding_box()
    step = grid_step(box)
    cells = FloorCells(box, placed)
    cost = len(room.vertices) + len(placed)
    spent = 0
    for z0 in _steps(box.min_y, box.max_y - item.depth_m, step):
        for x0 in _steps(box.min_x, box.max_x - item.width_m, step):
            spent += cost
            if spent > max_work:
                return None, REASON_NO_SPACE
            candidate = replace(
                item, center_x=x0 + item.width_m / 2, center_z=z0 + item.depth_m / 2, elevation_m=0.0
            )
            if room.contains_rectangle(x0, z0, item.width_m, item.depth_m) and not any(
                overlaps(candidate, other) for other in cells.near(candidate)
            ):
                return candidate, None
    return None, REASON_NO_SPACE


def _fits_polygon(item, room):
    x0, _, z0, _ = footprint(item)
    return room.contains_rectangle(x0, z0, item.width_m, item.depth_m)


def settle_at(item, room, placed, axes=("x", "z")):
    """Revalida un objeto en la posición pedida (mover o cambiar medidas).

    Lo acota a la caja de la sala y entre el suelo y el techo; su huella debe quedar dentro del polígono
    real. Si choca con algo de placed, pasa a la posición libre más cercana desplazándose por axes.

    Returns:
        (objeto, None) o (None, motivo).
    """
    if item.height_m > room.height_m + EPSILON:
        return None, REASON_TOO_TALL
    box = room.bounding_box()
    if item.width_m > box.max_x - box.min_x + EPSILON or item.depth_m > box.max_y - box.min_y + EPSILON:
        return None, REASON_OUTSIDE
    item = replace(
        item,
        center_x=_clamp(item.center_x, box.min_x + item.width_m / 2, box.max_x - item.width_m / 2),
        center_z=_clamp(item.center_z, box.min_y + item.depth_m / 2, box.max_y - item.depth_m / 2),
        elevation_m=_clamp(item.elevation_m, 0.0, room.height_m - item.height_m),
    )
    if not _fits_polygon(item, room):
        return None, REASON_OUTSIDE
    moved = _free_position(item, placed, lambda candidate: _fits_polygon(candidate, room), axes)
    if moved is None:
        return None, overlap_reason(_first_blocker_name(item, placed))
    return moved, None
