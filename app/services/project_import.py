"""Importación de un archivo de proyecto ("habita-project", versión 1): parseo, validación y plan de guardado.

El archivo NO es confiable. Se lee en este orden y se para en el primer problema:
1. JSON: UTF-8 estricto (con o sin BOM), sin NaN ni infinitos, sin claves repetidas; un JSON demasiado
   anidado también es un archivo inválido, nunca un error 500.
2. format y version, con su mensaje propio.
3. Esquema estricto (app.schemas.project_file).
4. Geometría: polígono válido para Room, objetos de suelo en el suelo, cada objeto dentro de la sala y
   bajo el techo. Los solapes no se comprueban: hay apilados legítimos (la mochila sobre la cama).

Los mensajes de error (ProjectImportError.message) son frases fijas en español armadas solo con números de
posición y nombres de campo del esquema: nunca repiten un valor recibido ni el nombre de un campo
desconocido. Contrato: CONTRATO-FASE6.md.
"""

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.geometry.room import Room, RoomError
from app.geometry.units import cm_to_m
from app.schemas.photo import MAX_NAME_LENGTH
from app.schemas.project_file import (
    MAX_FILE_ITEMS,
    MAX_FILE_PARTS,
    MAX_FILE_VERTICES,
    PROJECT_FILE_FORMAT,
    PROJECT_FILE_VERSION,
    ProjectFile,
)
from app.schemas.text import MAX_PROJECT_NAME_LENGTH

# Tamaño máximo del cuerpo de POST /rooms/import (lo corta BodySizeLimitMiddleware en app/main.py).
MAX_IMPORT_BYTES = 256 * 1024
IMPORT_PATH = r"^/api/v1/spaces/rooms/import/?$"
# Holgura para los redondeos del exportador: 1 cm.
IMPORT_TOLERANCE_M = 0.01

NOT_JSON = "El archivo no es un JSON válido."
NOT_A_PROJECT = "El archivo no es un proyecto de Habita."
WRONG_VERSION = (
    f"Versión de archivo no compatible: esta aplicación solo lee la versión {PROJECT_FILE_VERSION}."
)
VERTEX_COUNT = f"La habitación debe tener entre 3 y {MAX_FILE_VERTICES} vértices."
ITEM_COUNT = f"El proyecto puede tener como mucho {MAX_FILE_ITEMS} objetos."
INVALID_POLYGON = "La habitación no es válida: sus paredes se cruzan o su área es demasiado pequeña."

# Campos del esquema que pueden aparecer en un mensaje (los nombres de campo desconocidos, nunca).
_KNOWN_FIELDS = frozenset(
    {
        "format",
        "version",
        "name",
        "room",
        "items",
        "height_m",
        "vertices",
        "estimated",
        "category",
        "kind",
        "size_cm",
        "position",
        "wall",
        "parts",
        "x_m",
        "z_m",
        "elevation_m",
        "rotation_deg",
        "shape",
        "offset_cm",
        "color",
    }
)


class ProjectImportError(ValueError):
    """El archivo no se puede importar; message es el detail del 422 (fijo, sin eco de la entrada)."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def too_many_parts(number):
    return f"El objeto {number} tiene más de {MAX_FILE_PARTS} partes."


def floor_object_raised(number):
    return f"El objeto {number} es de suelo: debe estar en el suelo y sin pared."


def object_outside(number):
    return f"El objeto {number} se sale de la habitación."


def object_above_ceiling(number):
    return f"El objeto {number} no cabe bajo el techo."


@dataclass
class ImportPlan:
    """Lo que se guarda: columnas de habitaciones y de cada muebles_sugeridos (metros, centro y base)."""

    name: str
    room: dict
    pieces: list[dict] = field(default_factory=list)


# --- 1. JSON ------------------------------------------------------------------------------------------


def _reject_constant(_name):
    raise ValueError("NaN e Infinity no son JSON")


def _unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("clave repetida")
        obj[key] = value
    return obj


def parse_json(raw):
    """El JSON del cuerpo, o ProjectImportError(NOT_JSON).

    Un número enorme (1e400) se lee como infinito y lo rechaza después el esquema (allow_inf_nan=False).
    """
    try:
        text = raw.decode("utf-8-sig")
        return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, ValueError, RecursionError):
        # Sin encadenar: el error de json puede citar un trozo del archivo.
        raise ProjectImportError(NOT_JSON) from None


# --- 2 y 3. Esquema -----------------------------------------------------------------------------------


def _place(loc):
    """Dónde está el problema, en palabras, a partir de una ruta de Pydantic (solo índices y campos)."""
    if not loc:
        return "el proyecto"
    if loc[0] == "room":
        return "la habitación"
    if loc[0] == "items" and len(loc) > 1 and isinstance(loc[1], int):
        number = loc[1] + 1
        if len(loc) > 2 and loc[2] == "position":
            return f"la posición del objeto {number}"
        if len(loc) > 3 and loc[2] == "parts" and isinstance(loc[3], int):
            return f"la parte {loc[3] + 1} del objeto {number}"
        if len(loc) > 2 and loc[2] == "category":
            return f"la categoría del objeto {number}"
        return f"el objeto {number}"
    return "el proyecto"


def _schema_message(error):
    """Mensaje fijo para el primer error de Pydantic."""
    loc, kind = tuple(error["loc"]), error["type"]
    if kind == "extra_forbidden":
        # El último elemento de loc es el nombre del campo desconocido: no se muestra.
        return f"El archivo tiene un campo que no se admite en {_place(loc[:-1])}."
    if loc == ("room", "vertices") and kind in ("too_short", "too_long"):
        return VERTEX_COUNT
    if loc == ("items",) and kind == "too_long":
        return ITEM_COUNT
    if len(loc) == 3 and loc[0] == "items" and loc[2] == "parts" and kind == "too_long":
        return too_many_parts(loc[1] + 1)
    if kind == "missing":
        return f"Falta un campo obligatorio en {_place(loc[:-1])}."
    if loc and loc[-1] in ("name", "category") and kind == "value_error":
        longest = MAX_PROJECT_NAME_LENGTH if loc == ("name",) else MAX_NAME_LENGTH
        return (
            f"El nombre de {_place(loc)} no es válido: texto plano de 1 a {longest} caracteres, sin < ni >."
        )
    names = [part for part in loc if isinstance(part, str) and part in _KNOWN_FIELDS]
    suffix = f" ({names[-1]})" if names else ""
    return f"Hay un valor no válido en {_place(loc)}{suffix}."


def read_project_file(raw):
    """El archivo validado contra el esquema estricto.

    Raises:
        ProjectImportError: no es JSON, no es un proyecto de Habita, otra versión o no cumple el esquema.
    """
    data = parse_json(raw)
    if not isinstance(data, dict) or data.get("format") != PROJECT_FILE_FORMAT:
        raise ProjectImportError(NOT_A_PROJECT)
    version = data.get("version")
    # type() y no isinstance(): True no es la versión 1, ni 1.0 tampoco.
    if type(version) is not int or version != PROJECT_FILE_VERSION:
        raise ProjectImportError(WRONG_VERSION)
    try:
        return ProjectFile.model_validate(data)
    except ValidationError as exc:
        # Sin encadenar: el error de Pydantic incluye la entrada.
        raise ProjectImportError(_schema_message(exc.errors()[0])) from None


# --- 4. Geometría y plan ------------------------------------------------------------------------------


def plan_import(project):
    """Comprueba la geometría del archivo ya validado y arma las filas que se guardarán.

    Raises:
        ProjectImportError: polígono inválido, objeto de suelo levantado o con pared, objeto fuera de la
            sala o por encima del techo.
    """
    try:
        room = Room([tuple(vertex) for vertex in project.room.vertices], project.room.height_m)
    except RoomError:
        raise ProjectImportError(INVALID_POLYGON) from None

    pieces = []
    for number, item in enumerate(project.items, start=1):
        width, height, depth = (cm_to_m(side) for side in item.size_cm)
        position = item.position
        elevation = position.elevation_m
        if item.kind == "floor":
            if item.wall is not None or elevation > IMPORT_TOLERANCE_M:
                raise ProjectImportError(floor_object_raised(number))
            elevation = 0.0
        # La huella reducida 1 cm por lado debe quedar dentro del polígono real (vale en salas cóncavas).
        inner_width, inner_depth = width - 2 * IMPORT_TOLERANCE_M, depth - 2 * IMPORT_TOLERANCE_M
        corner_x = position.x_m - inner_width / 2
        corner_z = position.z_m - inner_depth / 2
        if not room.contains_rectangle(corner_x, corner_z, inner_width, inner_depth):
            raise ProjectImportError(object_outside(number))
        if elevation + height > room.height_m + IMPORT_TOLERANCE_M:
            raise ProjectImportError(object_above_ceiling(number))
        pieces.append(
            {
                "nombre": item.name,
                "ancho": width,
                "alto": height,
                "profundidad": depth,
                "posicion_x": position.x_m,
                "posicion_y": elevation,
                "posicion_z": position.z_m,
            }
        )
    return ImportPlan(name=project.name, room=room.to_record(), pieces=pieces)


def load_project(raw):
    """Del cuerpo crudo de la petición al plan de guardado.

    Raises:
        ProjectImportError: cualquier motivo de rechazo; message es el detail del 422.
    """
    return plan_import(read_project_file(raw))
