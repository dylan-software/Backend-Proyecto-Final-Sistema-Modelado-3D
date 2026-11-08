"""Objetos de una sala guardada: añadir, mover o cambiar medidas y borrar (CONTRATO-FASE0.md).

Una sola lógica para las rutas /rooms/{id}/items, el formulario manual y, en la FASE 2, las acciones del
chat. La geometría está en app.geometry.arrangement; aquí se leen y escriben las filas de
muebles_sugeridos (centro x/z y base y, en metros) y se guardan en una transacción con rollback. No usa
IA ni gasta cuota.

muebles_sugeridos no guarda si un objeto es de suelo o de pared (no hay migración): para los obstáculos
se deduce de su base (en el suelo = "floor"; levantado = "wall"). Solo influye en que un objeto de pared
nuevo suba por encima de los muebles de suelo con los que choca.

Tras cada cambio guardado se vacía la pila de deshacer la foto de la sala: deshacer una foto nunca
borra trabajo posterior. Si el guardado falla, la pila no se toca.
"""

from dataclasses import dataclass, replace

from app.geometry.arrangement import (
    ATTACH_TOLERANCE_M,
    REASON_TOO_TALL,
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
from app.routers.errors import DATABASE_ERRORS, database_error
from app.schemas.photo import PlacedObject
from app.schemas.room import MAX_LAYOUT_ITEMS

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


def find_piece(row, item_id):
    """El objeto item_id de la sala row (muebles cargados).

    Raises:
        RoomObjectError: 404 no existe o es de otra sala (mismo mensaje).
    """
    piece = next((piece for piece in row.muebles if piece.id == item_id), None)
    if piece is None:
        raise RoomObjectError(404, f"No existe un objeto con id {item_id} en esta sala.")
    return piece


async def _save(session, row, undo_store):
    """Commit con rollback si falla; tras un commit correcto, vacía la pila de deshacer la foto."""
    # Se leen antes del commit: así no hace falta recargar la fila después.
    user_id, room_id = row.user_id, row.id
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc
    if undo_store is not None:
        undo_store.forget(user_id, room_id)


async def place_object(
    session, row, *, name, kind, width_cm, depth_cm, height_cm, wall=None, elevation_cm=None, undo_store=None
):
    """Añade un objeto a la sala y lo guarda en la posición que calcula el servidor.

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
            sala no rectangular. HTTPException 503/500 si falla el guardado (con rollback).
    """
    if len(row.muebles) >= MAX_ROOM_ITEMS:
        raise RoomObjectError(
            409, f"Esta sala ya tiene el máximo de {MAX_ROOM_ITEMS} objetos; borra alguno para añadir otro."
        )
    room = room_shape(row)
    width, depth, height = cm_to_m(width_cm), cm_to_m(depth_cm), cm_to_m(height_cm)
    placed = [as_object(piece) for piece in row.muebles]
    if kind == "wall":
        if rectangle_of(room) is None:
            raise RoomObjectError(422, WALL_NEEDS_RECTANGLE)
        elevation = cm_to_m(elevation_cm) if elevation_cm is not None else DEFAULT_WALL_CENTER_M - height / 2
        item = PlacedObject(
            name=name, width_m=width, depth_m=depth, height_m=height,
            center_x=0.0, center_z=0.0, elevation_m=max(elevation, 0.0), kind="wall",
        )
        result, reason = place_on_wall(item, room, wall, placed)
        if result is None:
            raise _no_space(name, reason, WALL_LABELS[wall])
        ideal, _ = place_on_wall(item, room, wall, [])
        adjusted = not _same_place(result, ideal)
    else:
        item = PlacedObject(
            name=name, width_m=width, depth_m=depth, height_m=height,
            center_x=0.0, center_z=0.0, elevation_m=0.0, kind="floor",
        )
        result, reason = first_free_floor_spot(item, room, placed)
        if result is None:
            raise _no_space(name, reason, FLOOR_LABEL)
        adjusted = False
    piece = MuebleSugerido(nombre=name)
    _write(piece, result)
    row.muebles.append(piece)
    await _save(session, row, undo_store)
    return PlacementResult(piece, attached_wall(result, room), adjusted)


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


async def update_object(
    session, row, item_id, *, name=None, width_cm=None, depth_cm=None, height_cm=None, position=None,
    undo_store=None,
):
    """Renombra, cambia medidas o mueve un objeto; el servidor revalida y guarda la posición final.

    Solo con name, la geometría no se toca. Con position (x/y = esquina mínima en metros, elevation_m y
    wall opcionales), se pide esa posición; con wall se pega a esa pared (sala rectangular). Sin position,
    conserva su centro y su base, y si tocaba una pared sigue pegado a ella. Si la posición choca, pasa al
    hueco libre más cercano (adjusted=True).

    Raises:
        RoomObjectError: 404 el objeto no es de esta sala; 409 no cabe; 422 pared en sala no rectangular.
            HTTPException 503/500 si falla el guardado (con rollback).
    """
    piece = find_piece(row, item_id)
    new_name = name if name is not None else piece.nombre
    room = room_shape(row)
    if width_cm is None and depth_cm is None and height_cm is None and position is None:
        piece.nombre = new_name
        await _save(session, row, undo_store)
        return PlacementResult(piece, attached_wall(as_object(piece), room), False)

    current = replace(as_object(piece), name=new_name)
    others = [as_object(other) for other in row.muebles if other is not piece]
    width = cm_to_m(width_cm) if width_cm is not None else piece.ancho
    depth = cm_to_m(depth_cm) if depth_cm is not None else piece.profundidad
    height = cm_to_m(height_cm) if height_cm is not None else piece.alto
    skip = None
    if position is None:
        requested, axes = _resized_in_place(current, width, depth, height, room)
        result, reason = settle_at(requested, room, others, axes)
    else:
        requested = replace(
            current,
            width_m=width,
            depth_m=depth,
            height_m=height,
            center_x=position.x + width / 2,
            center_z=position.y + depth / 2,
            elevation_m=position.elevation_m if position.elevation_m is not None else piece.posicion_y,
        )
        if position.wall is None:
            result, reason = settle_at(requested, room, others)
        else:
            if rectangle_of(room) is None:
                raise RoomObjectError(422, WALL_NEEDS_RECTANGLE)
            along, thickness = wall_extents(width, depth, position.wall)
            item = replace(requested, width_m=along, depth_m=thickness, kind="wall")
            result, reason = place_on_wall(item, room, position.wall, others, centered=False)
            # La coordenada perpendicular a la pared la fija la pared: no cuenta como ajuste.
            skip = "z" if position.wall in ("back", "front") else "x"
    if result is None:
        if reason == REASON_TOO_TALL:
            raise _no_space(new_name, reason, "")
        raise RoomObjectError(
            409, f"«{new_name}» no cabe en esa posición sin salirse de la sala ni chocar con otro objeto."
        )
    adjusted = not _same_place(result, requested, skip)
    piece.nombre = new_name
    _write(piece, result)
    await _save(session, row, undo_store)
    return PlacementResult(piece, attached_wall(result, room), adjusted)


async def delete_object(session, row, item_id, *, undo_store=None):
    """Borra un objeto de la sala.

    Raises:
        RoomObjectError: 404 el objeto no es de esta sala. HTTPException 503/500 si falla el guardado.
    """
    piece = find_piece(row, item_id)
    # Cascada delete-orphan: quitarlo de la lista lo borra en el mismo commit.
    row.muebles.remove(piece)
    await _save(session, row, undo_store)
