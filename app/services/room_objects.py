"""Objetos de una sala guardada: añadir, mover o cambiar medidas y borrar (CONTRATO-FASE0.md).

Una sola lógica para las rutas /rooms/{id}/items, el formulario manual y las acciones del chat
(CONTRATO-FASE2.md). La geometría está en app.geometry.arrangement; aquí se leen y escriben las filas de
muebles_sugeridos (centro x/z y base y, en metros). No usa IA ni gasta cuota.

Dos niveles:
- Núcleos síncronos (add_object, change_object, remove_object): validan y cambian la fila SIN commit.
  Si rechazan la operación (RoomObjectError), no han tocado nada. El chat aplica varios en una sola
  transacción.
- Envoltorios async (place_object, update_object, delete_object): copia de la sala, núcleo y
  commit_with_undo, que guarda con rollback y apila la copia en el Deshacer de la sala.

muebles_sugeridos no guarda si un objeto es de suelo o de pared (no hay migración): para los obstáculos
se deduce de su base (en el suelo = "floor"; levantado = "wall"). Solo influye en que un objeto de pared
nuevo suba por encima de los muebles de suelo con los que choca.
"""

from dataclasses import dataclass, replace

from app.geometry.arrangement import (
    ATTACH_TOLERANCE_M,
    REASON_TOO_TALL,
    WALL_SIDES,
    attached_wall,
    first_free_floor_spot,
    footprint,
    place_on_wall,
    rectangle_of,
    settle_at,
    wall_extents,
)
from app.geometry.room import Room
from app.geometry.units import cm_to_m
from app.models.space import MuebleSugerido
from app.schemas.photo import PlacedObject
from app.schemas.room import MAX_LAYOUT_ITEMS
from app.services.room_undo import commit_with_undo, snapshot_room

# Máximo de objetos por sala: el mismo tope que el layout.
MAX_ROOM_ITEMS = MAX_LAYOUT_ITEMS
# Altura por defecto del CENTRO de un objeto de pared nuevo (respuesta 1 del contrato).
DEFAULT_WALL_CENTER_M = 1.5
# Base por debajo de la cual un objeto guardado cuenta como apoyado en el suelo.
FLOOR_EPSILON_M = 1e-6
# Diferencia de posición por debajo de la cual no se considera un ajuste.
SAME_PLACE_EPSILON_M = 1e-6

WALL_LABELS = {
    "back": "la pared del fondo",
    "front": "la pared de enfrente",
    "left": "la pared izquierda",
    "right": "la pared derecha",
}
FLOOR_LABEL = "el suelo"
WALL_NEEDS_RECTANGLE = "Los objetos de pared solo se pueden colocar en salas rectangulares."


class RoomObjectError(Exception):
    """La operación no se puede hacer: status_code (404, 409 o 422) y un mensaje en español."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class PlacementResult:
    """El objeto guardado, la pared a la que quedó pegado (o None) y si se movió respecto a lo pedido."""

    piece: MuebleSugerido
    wall: str | None
    adjusted: bool


def room_shape(row):
    """La forma de una fila de habitaciones (las salas sin vértices son su rectángulo)."""
    return Room.from_record(row.vertices, row.ancho, row.profundidad, row.alto)


def as_object(piece):
    """Un muebles_sugeridos como objeto de app.geometry.arrangement (el tipo se deduce de la base)."""
    return PlacedObject(
        name=piece.nombre,
        width_m=piece.ancho,
        depth_m=piece.profundidad,
        height_m=piece.alto,
        center_x=piece.posicion_x,
        center_z=piece.posicion_z,
        elevation_m=piece.posicion_y,
        kind="floor" if piece.posicion_y <= FLOOR_EPSILON_M else "wall",
    )


def _write(piece, obj):
    piece.ancho = obj.width_m
    piece.profundidad = obj.depth_m
    piece.alto = obj.height_m
    piece.posicion_x = obj.center_x
    piece.posicion_y = obj.elevation_m
    piece.posicion_z = obj.center_z


def _same_place(a, b, skip=None):
    """Mismo centro y misma base, sin mirar el eje skip ("x", "z" o None)."""
    pairs = [(a.elevation_m, b.elevation_m)]
    if skip != "x":
        pairs.append((a.center_x, b.center_x))
    if skip != "z":
        pairs.append((a.center_z, b.center_z))
    return all(abs(p - q) <= SAME_PLACE_EPSILON_M for p, q in pairs)


def _no_space(name, reason, where):
    if reason == REASON_TOO_TALL:
        return RoomObjectError(409, f"«{name}» es más alto que la sala.")
    return RoomObjectError(409, f"No hay sitio para «{name}» en {where}.")


def _require_rectangle(room):
    if rectangle_of(room) is None:
        raise RoomObjectError(422, WALL_NEEDS_RECTANGLE)


def find_piece(row, item_id):
    """El objeto item_id de la sala row (muebles cargados).

    Raises:
        RoomObjectError: 404 no existe o es de otra sala (mismo mensaje).
    """
    piece = next((piece for piece in row.muebles if piece.id == item_id), None)
    if piece is None:
        raise RoomObjectError(404, f"No existe un objeto con id {item_id} en esta sala.")
    return piece


# --- Añadir ---------------------------------------------------------------------------------------------


def _wall_spot(name, size, wall, elevation_cm, room, placed):
    """(objeto pegado a la pared wall, adjusted) o RoomObjectError."""
    _require_rectangle(room)
    width, depth, height = size
    elevation = cm_to_m(elevation_cm) if elevation_cm is not None else DEFAULT_WALL_CENTER_M - height / 2
    item = PlacedObject(
        name=name, width_m=width, depth_m=depth, height_m=height,
        center_x=0.0, center_z=0.0, elevation_m=max(elevation, 0.0), kind="wall",
    )
    result, reason = place_on_wall(item, room, wall, placed)
    if result is None:
        raise _no_space(name, reason, WALL_LABELS[wall])
    ideal, _ = place_on_wall(item, room, wall, [])
    return result, not _same_place(result, ideal)


def _floor_spot(name, size, room, placed):
    """(objeto en el primer hueco libre del suelo, adjusted=False) o RoomObjectError."""
    width, depth, height = size
    item = PlacedObject(
        name=name, width_m=width, depth_m=depth, height_m=height,
        center_x=0.0, center_z=0.0, elevation_m=0.0, kind="floor",
    )
    result, reason = first_free_floor_spot(item, room, placed)
    if result is None:
        raise _no_space(name, reason, FLOOR_LABEL)
    return result, False


def add_object(row, *, name, kind, width_cm, depth_cm, height_cm, wall=None, elevation_cm=None):
    """Añade un objeto a row en la posición que calcula el servidor (sin commit).

    De pared (kind="wall"): pegado a la pared wall de una sala rectangular, centrado y con la base en
    elevation_cm (por defecto, el centro a DEFAULT_WALL_CENTER_M); width_cm a lo largo de la pared y
    depth_cm su grosor. De suelo: en el primer hueco libre dentro del polígono real.

    Args:
        row: Habitacion de la cuenta, con sus muebles cargados (selectinload).
        name: nombre ya validado como texto plano.

    Returns:
        PlacementResult.

    Raises:
        RoomObjectError: 409 sala llena, sin sitio o más alto que la sala; 422 objeto de pared en una
            sala no rectangular. En ese caso row no cambia.
    """
    if len(row.muebles) >= MAX_ROOM_ITEMS:
        raise RoomObjectError(
            409, f"Esta sala ya tiene el máximo de {MAX_ROOM_ITEMS} objetos; borra alguno para añadir otro."
        )
    room = room_shape(row)
    size = (cm_to_m(width_cm), cm_to_m(depth_cm), cm_to_m(height_cm))
    placed = [as_object(piece) for piece in row.muebles]
    if kind == "wall":
        result, adjusted = _wall_spot(name, size, wall, elevation_cm, room, placed)
    else:
        result, adjusted = _floor_spot(name, size, room, placed)
    piece = MuebleSugerido(nombre=name)
    _write(piece, result)
    row.muebles.append(piece)
    return PlacementResult(piece, attached_wall(result, room), adjusted)


# --- Cambiar nombre, medidas o posición --------------------------------------------------------------------


def _resized_in_place(current, width, depth, height, room):
    """El objeto con medidas nuevas en su sitio: el lado pegado a una pared de la caja sigue pegado.

    Returns:
        (objeto pedido, ejes por los que puede desplazarse si choca).
    """
    box = room.bounding_box()
    x0, x1, z0, z1 = footprint(current)
    center_x, center_z = current.center_x, current.center_z
    anchored = set()
    if x0 - box.min_x <= ATTACH_TOLERANCE_M:
        center_x, anchored = x0 + width / 2, anchored | {"x"}
    elif box.max_x - x1 <= ATTACH_TOLERANCE_M:
        center_x, anchored = x1 - width / 2, anchored | {"x"}
    if z0 - box.min_y <= ATTACH_TOLERANCE_M:
        center_z, anchored = z0 + depth / 2, anchored | {"z"}
    elif box.max_y - z1 <= ATTACH_TOLERANCE_M:
        center_z, anchored = z1 - depth / 2, anchored | {"z"}
    requested = replace(
        current, width_m=width, depth_m=depth, height_m=height, center_x=center_x, center_z=center_z
    )
    # Pegado a una pared, solo se desliza a lo largo de ella; en una esquina o suelto, en los dos ejes.
    axes = tuple(axis for axis in ("x", "z") if axis not in anchored) or ("x", "z")
    return requested, axes


def _new_size(piece, width_cm, depth_cm, height_cm):
    """Medidas finales en metros: las pedidas o, si faltan, las actuales."""
    width = cm_to_m(width_cm) if width_cm is not None else piece.ancho
    depth = cm_to_m(depth_cm) if depth_cm is not None else piece.profundidad
    height = cm_to_m(height_cm) if height_cm is not None else piece.alto
    return width, depth, height


def _resize(current, size, room, others):
    """Medidas nuevas en su sitio. Returns: (resultado, motivo, objeto pedido, eje que no cuenta)."""
    requested, axes = _resized_in_place(current, *size, room)
    result, reason = settle_at(requested, room, others, axes)
    return result, reason, requested, None


def _rests_on(item, other):
    """Si item está apoyado encima de other: su base a la altura del techo de other y huellas solapadas."""
    if abs(item.elevation_m - (other.elevation_m + other.height_m)) > FLOOR_EPSILON_M:
        return False
    ax0, ax1, az0, az1 = footprint(item)
    bx0, bx1, bz0, bz1 = footprint(other)
    return min(ax1, bx1) - max(ax0, bx0) > FLOOR_EPSILON_M and min(az1, bz1) - max(az0, bz0) > FLOOR_EPSILON_M


def _is_wall_object(current, others, room):
    """Si un objeto guardado es de pared (no hay columna kind; ver la cabecera del módulo).

    Es de pared si está levantado, la sala es rectangular y, o bien está pegado a una pared, o bien no
    está apoyado encima de otro objeto. Así, la mochila que la foto dejó encima de la cama sigue siendo
    un objeto apoyado (se mueve libre), y un cuadro que quedó separado de la pared vuelve a ella.
    """
    if current.kind != "wall" or rectangle_of(room) is None:
        return False
    if attached_wall(current, room) is not None:
        return True
    return not any(_rests_on(current, other) for other in others)


def _nearest_wall(item, room):
    """La pared de la sala rectangular más cercana al centro de item; empates en el orden de WALL_SIDES.

    Se mide la distancia a la recta de cada pared (en valor absoluto): un cuadro arrastrado a lo largo
    de la pared del fondo hasta pasarse de la esquina sigue en la pared del fondo.
    """
    origin_x, origin_z, box = rectangle_of(room)
    x, z = item.center_x - origin_x, item.center_z - origin_z
    gaps = {"back": abs(z), "left": abs(x), "right": abs(box.width_m - x), "front": abs(box.length_m - z)}
    # min es estable: con distancias iguales gana la primera de WALL_SIDES.
    return min(WALL_SIDES, key=lambda side: gaps[side])


def _move_to_nearest_wall(current, requested, room, others):
    """Un objeto de pared movido sin wall: pegado (0 cm) a la pared más cercana a la posición pedida.

    Conserva lo que iba a lo largo de su pared (un cuadro no gira 90°), su altura pedida y su posición a
    lo largo de la nueva pared, acotada para que quede entero dentro del tramo. Si choca, sube sobre
    los muebles y se desliza a lo largo de la pared, como con wall (place_on_wall).

    Returns:
        (objeto, None) o (None, motivo).
    """
    along, thickness = _along_and_thickness(current, room, requested.width_m, requested.depth_m)
    item = replace(requested, width_m=along, depth_m=thickness)
    return place_on_wall(item, room, _nearest_wall(requested, room), others, centered=False)


def _move(current, size, position, room, others):
    """A la posición pedida (esquina mínima x/y; con wall, pegado a esa pared).

    Returns:
        (resultado, motivo, objeto pedido, eje que no cuenta como ajuste).
    """
    width, depth, height = size
    requested = replace(
        current,
        width_m=width,
        depth_m=depth,
        height_m=height,
        center_x=position.x + width / 2,
        center_z=position.y + depth / 2,
        elevation_m=position.elevation_m if position.elevation_m is not None else current.elevation_m,
    )
    if position.wall is None:
        if _is_wall_object(current, others, room):
            # Sin wall, un objeto de pared no se queda flotando: va a la pared más cercana (PREGUNTAS 8).
            result, reason = _move_to_nearest_wall(current, requested, room, others)
            return result, reason, requested, None
        result, reason = settle_at(requested, room, others)
        return result, reason, requested, None
    _require_rectangle(room)
    along, thickness = wall_extents(width, depth, position.wall)
    item = replace(requested, width_m=along, depth_m=thickness)
    # Un objeto de suelo se arrima a la pared en el suelo: nunca se sube encima de otro mueble.
    stack = current.kind == "wall"
    result, reason = place_on_wall(item, room, position.wall, others, centered=False, stack=stack)
    # La coordenada perpendicular a la pared la fija la pared: no cuenta como ajuste.
    skip = "z" if position.wall in ("back", "front") else "x"
    return result, reason, requested, skip


def change_object(row, item_id, *, name=None, width_cm=None, depth_cm=None, height_cm=None, position=None):
    """Renombra, cambia medidas o mueve un objeto de row (sin commit); revalida la posición final.

    Solo con name, la geometría no se toca. Con position (x/y = esquina mínima en metros, elevation_m y
    wall opcionales), se pide esa posición; con wall se pega a esa pared (sala rectangular), y sin wall
    un objeto de pared se pega a la pared más cercana (_is_wall_object, _move_to_nearest_wall). Sin position,
    conserva su centro y su base, y si tocaba una pared sigue pegado a ella. Si la posición choca, pasa al
    hueco libre más cercano (adjusted=True).

    Raises:
        RoomObjectError: 404 el objeto no es de esta sala; 409 no cabe; 422 pared en sala no rectangular.
            En ese caso row no cambia.
    """
    piece = find_piece(row, item_id)
    new_name = name if name is not None else piece.nombre
    room = room_shape(row)
    if width_cm is None and depth_cm is None and height_cm is None and position is None:
        piece.nombre = new_name
        return PlacementResult(piece, attached_wall(as_object(piece), room), False)

    current = replace(as_object(piece), name=new_name)
    others = [as_object(other) for other in row.muebles if other is not piece]
    size = _new_size(piece, width_cm, depth_cm, height_cm)
    if position is None:
        result, reason, requested, skip = _resize(current, size, room, others)
    else:
        result, reason, requested, skip = _move(current, size, position, room, others)
    if result is None:
        if reason == REASON_TOO_TALL:
            raise _no_space(new_name, reason, "")
        raise RoomObjectError(
            409, f"«{new_name}» no cabe en esa posición sin salirse de la sala ni chocar con otro objeto."
        )
    piece.nombre = new_name
    _write(piece, result)
    return PlacementResult(piece, attached_wall(result, room), not _same_place(result, requested, skip))


# --- Mover a una pared (acción move_object del chat) --------------------------------------------------------


def _along_and_thickness(current, room, width, depth):
    """(ancho a lo largo de la pared, grosor) de un objeto que se va a pegar a otra pared.

    width y depth son sus medidas finales en x y z. Si current ya está pegado a una pared, conserva lo
    que iba a lo largo de ella (un cuadro sigue siendo igual de ancho); si no, el lado largo va contra
    la pared (un sofá de espaldas a ella).
    """
    side = attached_wall(current, room)
    if side is not None:
        return wall_extents(width, depth, side)
    return max(width, depth), min(width, depth)


def move_to_wall(row, item_id, wall):
    """Pega un objeto de row a la pared wall, centrado, sin commit.

    Uno de pared conserva su altura; uno de suelo se arrima en el suelo y, si choca, solo se desliza a
    lo largo de la pared (nunca se sube encima de otro mueble).

    Returns:
        PlacementResult (adjusted si no quedó en el centro de la pared).

    Raises:
        RoomObjectError: 404 el objeto no es de esta sala; 409 no hay sitio en esa pared; 422 la sala
            no es rectangular. En ese caso row no cambia.
    """
    piece = find_piece(row, item_id)
    room = room_shape(row)
    _require_rectangle(room)
    current = as_object(piece)
    along, thickness = _along_and_thickness(current, room, current.width_m, current.depth_m)
    item = replace(current, width_m=along, depth_m=thickness)
    others = [as_object(other) for other in row.muebles if other is not piece]
    stack = current.kind == "wall"
    result, reason = place_on_wall(item, room, wall, others, stack=stack)
    if result is None:
        raise _no_space(piece.nombre, reason, WALL_LABELS[wall])
    ideal, _ = place_on_wall(item, room, wall, [], stack=stack)
    _write(piece, result)
    return PlacementResult(piece, attached_wall(result, room), not _same_place(result, ideal))


# --- Borrar ---------------------------------------------------------------------------------------------


def remove_object(row, item_id):
    """Quita un objeto de row (sin commit) y lo devuelve.

    Raises:
        RoomObjectError: 404 el objeto no es de esta sala.
    """
    piece = find_piece(row, item_id)
    # Cascada delete-orphan: quitarlo de la lista lo borra en el commit.
    row.muebles.remove(piece)
    return piece


# --- Envoltorios con guardado y Deshacer -----------------------------------------------------------------


async def place_object(session, row, *, undo_store=None, **fields):
    """add_object(row, **fields) y guardado; tras un commit correcto apila la copia "add".

    Raises:
        RoomObjectError: lo de add_object (nada se guarda). HTTPException 503/500 si falla el guardado
            (con rollback).
    """
    before = snapshot_room(row, "add")
    result = add_object(row, **fields)
    await commit_with_undo(session, row, undo_store, before)
    return result


async def update_object(session, row, item_id, *, undo_store=None, **fields):
    """change_object(row, item_id, **fields) y guardado; apila la copia "update"."""
    before = snapshot_room(row, "update")
    result = change_object(row, item_id, **fields)
    await commit_with_undo(session, row, undo_store, before)
    return result


async def delete_object(session, row, item_id, *, undo_store=None):
    """remove_object(row, item_id) y guardado; apila la copia "delete"."""
    before = snapshot_room(row, "delete")
    remove_object(row, item_id)
    await commit_with_undo(session, row, undo_store, before)
