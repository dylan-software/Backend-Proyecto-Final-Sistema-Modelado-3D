"""Acciones del chat sobre los objetos de una sala (CONTRATO-FASE2.md, sección 2).

El modelo puede pedir add_object, remove_object y move_object. Aquí se leen esas llamadas (datos NO
confiables), se validan y se aplican con los núcleos de app.services.room_objects (la misma lógica que
las rutas /items, sin copiarla), y se redactan las frases de la respuesta. El guardado (un solo commit
por mensaje) y la copia para Deshacer los hace el router del chat.
"""

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.geometry.arrangement import attached_wall, normalized
from app.routers.errors import DATABASE_ERRORS, database_error
from app.schemas.photo import clean_plain_text
from app.schemas.room import ItemCreate
from app.services.room_objects import (
    FLOOR_EPSILON_M,
    WALL_LABELS,
    RoomObjectError,
    add_object,
    as_object,
    move_to_wall,
    remove_object,
)

MAX_ACTIONS_PER_MESSAGE = 3
# En la pregunta "¿cuál?" se describen como mucho estos objetos con el mismo nombre.
MAX_CHOICES_LISTED = 5

TOO_MANY_ACTIONS = "Solo hago 3 cambios por mensaje; pídeme el resto después."
NOT_UNDERSTOOD = "No entendí una de las acciones que me pediste."
INVALID_OBJECT = (
    "No pude añadir el objeto: el nombre debe ser texto sencillo de hasta 60 caracteres y cada medida, "
    "de 5 a 400 cm."
)

_WALL_ENUM = ["left", "right", "back", "front"]
_NAME = {"type": "string", "minLength": 1, "maxLength": 60, "description": "Nombre en español, texto plano."}
_ITEM_ID = {"type": "integer", "description": "id del objeto, solo si hay varios con el mismo nombre."}
_SIDE_CM = {"type": "number", "minimum": 5, "maximum": 400}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_object",
            "description": "Añade un objeto a la sala. La posición la calcula la aplicación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _NAME,
                    "kind": {"type": "string", "enum": ["floor", "wall"]},
                    "wall": {"type": "string", "enum": _WALL_ENUM, "description": "Solo si kind es wall."},
                    "width_cm": {**_SIDE_CM, "description": "Ancho (a lo largo de la pared si es de pared)."},
                    "depth_cm": {**_SIDE_CM, "description": "Fondo o grosor."},
                    "height_cm": {**_SIDE_CM, "description": "Alto."},
                },
                "required": ["name", "kind", "width_cm", "depth_cm", "height_cm"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_object",
            "description": "Quita un objeto de la sala por su nombre.",
            "parameters": {
                "type": "object",
                "properties": {"name": _NAME, "item_id": _ITEM_ID},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_object",
            "description": "Pega un objeto a una pared, centrado. Solo en salas rectangulares.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _NAME,
                    "item_id": _ITEM_ID,
                    "wall": {"type": "string", "enum": _WALL_ENUM},
                },
                "required": ["name", "wall"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class ToolCall:
    """Una llamada leída: el nombre de la herramienta y sus argumentos (None si no eran un objeto JSON)."""

    name: str | None
    arguments: dict | None


@dataclass(frozen=True)
class ChatChange:
    """Un cambio guardado: action "added", "removed" o "moved", el nombre y dónde quedó en palabras."""

    action: str
    name: str
    where: str


@dataclass
class ActionsOutcome:
    """Los cambios hechos (para `changes`) y las frases de la respuesta, en orden."""

    changes: list[ChatChange] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)


class _Refused(Exception):
    """La acción no se hace; el mensaje es la frase para el usuario."""


# --- Ubicación en palabras -------------------------------------------------------------------------------


def _meters(value):
    return f"{value:.1f}".replace(".", ",")


def describe_location(obj, room, with_height=True):
    """Dónde está un objeto, en palabras y sin coordenadas: "en la pared izquierda, a 1,4 m del suelo",
    "en el suelo, junto a la pared del fondo" o "en el suelo".

    Args:
        obj: objeto de app.geometry.arrangement (as_object).
        with_height: añade la altura de los objetos levantados.
    """
    side = attached_wall(obj, room)
    if obj.elevation_m <= FLOOR_EPSILON_M:
        return f"en el suelo, junto a {WALL_LABELS[side]}" if side else "en el suelo"
    place = f"en {WALL_LABELS[side]}" if side else "sobre otro mueble"
    return f"{place}, a {_meters(obj.elevation_m)} m del suelo" if with_height else place


def prompt_objects(row, room):
    """Los objetos de la sala para <datos_sala>: id, nombre, ubicación en palabras y medidas en cm."""
    objects = []
    for piece in sorted(row.muebles, key=lambda piece: piece.id or 0):
        obj = as_object(piece)
        objects.append(
            {
                "id": piece.id,
                "nombre": piece.nombre,
                "ubicacion": describe_location(obj, room),
                "ancho_cm": round(piece.ancho * 100),
                "fondo_cm": round(piece.profundidad * 100),
                "alto_cm": round(piece.alto * 100),
            }
        )
    return objects


# --- Lectura de las llamadas del modelo -------------------------------------------------------------------


def _parse_one(call):
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        return ToolCall(None, None)
    name = function.get("name") if isinstance(function.get("name"), str) else None
    raw = function.get("arguments")
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        arguments = None
    return ToolCall(name, arguments if isinstance(arguments, dict) else None)


def parse_tool_calls(raw_calls):
    """Las tool_calls del modelo como ToolCall; lo que no tiene la forma esperada queda con None."""
    return [_parse_one(call) for call in raw_calls]


# --- Encontrar el objeto por nombre ----------------------------------------------------------------------


def _name_key(text):
    return normalized(" ".join(text.split()))


def _requested_name(arguments):
    try:
        return clean_plain_text(arguments.get("name"))
    except ValueError:
        raise _Refused(NOT_UNDERSTOOD) from None


def _which_one(name, matches, room, verb):
    places = [describe_location(as_object(piece), room) for piece in matches[:MAX_CHOICES_LISTED]]
    listed = "; ".join(f"{i}) {place}" for i, place in enumerate(places, start=1))
    more = len(matches) - len(places)
    extra = f"; y {more} más" if more > 0 else ""
    return f"Hay {len(matches)} objetos llamados «{name}»: {listed}{extra}. ¿Cuál {verb}?"


def _resolve(row, arguments, room, verb):
    """El objeto pedido por nombre (y item_id si llega), o _Refused con la frase para el usuario.

    Un item_id que no es de esta sala o cuyo nombre no coincide cuenta como "no encontrado": nunca se
    toca un objeto que no sea de la sala.
    """
    name = _requested_name(arguments)
    item_id = arguments.get("item_id")
    matches = [piece for piece in row.muebles if _name_key(piece.nombre) == _name_key(name)]
    if item_id is not None:
        valid_id = type(item_id) is int
        matches = [piece for piece in matches if valid_id and piece.id == item_id]
    if not matches:
        raise _Refused(f"No encontré «{name}» en la sala.")
    if len(matches) > 1:
        raise _Refused(_which_one(name, matches, room, verb))
    return matches[0]


# --- Acciones --------------------------------------------------------------------------------------------


def _add(row, arguments, room, outcome):
    try:
        body = ItemCreate.model_validate(arguments)
    except ValidationError:
        raise _Refused(INVALID_OBJECT) from None
    result = add_object(
        row,
        name=body.name,
        kind=body.kind,
        wall=body.wall,
        width_cm=body.width_cm,
        depth_cm=body.depth_cm,
        height_cm=body.height_cm,
        elevation_cm=body.elevation_cm,
    )
    where = describe_location(as_object(result.piece), room, with_height=False)
    outcome.changes.append(ChatChange("added", body.name, where))
    outcome.sentences.append(f"Añadí «{body.name}» {where}.")


def _remove(row, arguments, room, outcome):
    piece = _resolve(row, arguments, room, "quito")
    where = describe_location(as_object(piece), room, with_height=False)
    remove_object(row, piece.id)
    outcome.changes.append(ChatChange("removed", piece.nombre, where))
    outcome.sentences.append(f"Quité «{piece.nombre}».")


def _move(row, arguments, room, outcome):
    wall = arguments.get("wall")
    if wall not in WALL_LABELS:
        raise _Refused(NOT_UNDERSTOOD)
    piece = _resolve(row, arguments, room, "muevo")
    result = move_to_wall(row, piece.id, wall)
    where = describe_location(as_object(result.piece), room, with_height=False)
    outcome.changes.append(ChatChange("moved", piece.nombre, where))
    outcome.sentences.append(f"Moví «{piece.nombre}» a {WALL_LABELS[wall]}.")


_HANDLERS = {"add_object": _add, "remove_object": _remove, "move_object": _move}


async def _flush(session):
    """Da id a lo añadido (para que una acción posterior lo encuentre) sin cerrar la transacción."""
    try:
        await session.flush()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc


async def apply_actions(session, row, calls, room):
    """Aplica como mucho MAX_ACTIONS_PER_MESSAGE llamadas a row, en orden y SIN commit.

    Una acción rechazada no cambia nada y deja su frase; las demás siguen. El llamador hace un solo
    commit si hubo cambios (o ninguno si no los hubo).

    Args:
        calls: lista de ToolCall (parse_tool_calls).
        room: la forma de la sala (room_objects.room_shape(row)).

    Returns:
        ActionsOutcome.

    Raises:
        HTTPException: 503/500 si falla la base de datos al asignar ids (con rollback).
    """
    outcome = ActionsOutcome()
    for call in calls[:MAX_ACTIONS_PER_MESSAGE]:
        handler = _HANDLERS.get(call.name)
        try:
            if handler is None or call.arguments is None:
                raise _Refused(NOT_UNDERSTOOD)
            handler(row, call.arguments, room, outcome)
        except _Refused as refused:
            outcome.sentences.append(str(refused))
        except RoomObjectError as exc:
            outcome.sentences.append(exc.message)
        else:
            await _flush(session)
    if len(calls) > MAX_ACTIONS_PER_MESSAGE:
        outcome.sentences.append(TOO_MANY_ACTIONS)
    return outcome
