"""Estimación de una sala a partir de una foto: lo que debe devolver la IA y cómo se valida.

La respuesta del modelo es un dato NO confiable. Se valida en dos niveles:
1. El sobre (sala, descripción, lista de objetos): si no cumple, la estimación entera se rechaza
   con EstimateError (el endpoint responde 502).
2. Cada objeto por separado: el que no cumple se descarta con un motivo y los demás siguen.

Ningún mensaje de error ni motivo de descarte copia texto del modelo, salvo nombres que ya pasaron
la limpieza de texto plano. Contrato completo: CONTRATO-FOTO-A-3D.md (carpeta padre de los repos).
"""

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.geometry.units import cm_to_m

# Límites de la sección 2 del v4, en un solo lugar.
ROOM_MIN_SIDE_M = 1.5
ROOM_MAX_SIDE_M = 12.0
ROOM_MIN_HEIGHT_M = 2.0
ROOM_MAX_HEIGHT_M = 4.0
OBJECT_MIN_SIDE_CM = 5.0
OBJECT_MAX_SIDE_CM = 400.0
MAX_NAME_LENGTH = 60
MAX_DESCRIPTION_LENGTH = 500
MAX_INSTRUCTIONS_LENGTH = 500
# Se revisan como mucho MAX_OBJECTS_CHECKED objetos y se aceptan MAX_PHOTO_OBJECTS.
MAX_OBJECTS_CHECKED = 30
MAX_PHOTO_OBJECTS = 10
# Un objeto que se sale de la sala hasta esta distancia se desplaza hacia dentro; si se sale más, se descarta.
FIT_TOLERANCE_M = 0.10
# Margen numérico para comparaciones con coma flotante.
EPSILON = 1e-9

UNNAMED = "(sin nombre)"
REASON_OUTSIDE = "fuera de la habitación"
REASON_INVALID = "datos fuera de los límites"
REASON_LIMIT = "límite de 10 objetos"


class EstimateError(Exception):
    """La IA no devolvió una estimación utilizable. El mensaje es fijo y nunca contiene su salida."""


def clean_plain_text(value):
    """Nombre en texto plano: espacios colapsados, sin caracteres de control ni '<' '>'.

    Raises:
        ValueError: no es texto, queda vacío, pasa de MAX_NAME_LENGTH o tiene caracteres prohibidos.
    """
    if not isinstance(value, str):
        raise ValueError("el nombre debe ser texto")
    text = " ".join(value.split())
    if not 1 <= len(text) <= MAX_NAME_LENGTH:
        raise ValueError("longitud de nombre fuera de los límites")
    for char in text:
        if char in "<>" or unicodedata.category(char) in ("Cc", "Cf"):
            raise ValueError("el nombre no es texto plano")
    return text


def _clean_description(value):
    # La descripción no se rechaza: se quitan los caracteres de control y se recorta.
    text = "".join(c for c in value if unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(text.split())[:MAX_DESCRIPTION_LENGTH]


class StrictModel(BaseModel):
    # Campos desconocidos rechazados, sin conversiones de tipo ("2" no es 2) y sin NaN ni infinitos.
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class EstimatedRoom(StrictModel):
    width_m: float = Field(ge=ROOM_MIN_SIDE_M, le=ROOM_MAX_SIDE_M)
    length_m: float = Field(ge=ROOM_MIN_SIDE_M, le=ROOM_MAX_SIDE_M)
    height_m: float = Field(ge=ROOM_MIN_HEIGHT_M, le=ROOM_MAX_HEIGHT_M)


class EstimatedObject(StrictModel):
    """Un objeto tal como lo describe la IA: medidas en cm, centro de la huella y base en metros."""

    name: str
    kind: Literal["floor", "wall"]
    width_cm: float = Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    depth_cm: float = Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    height_cm: float = Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    x_m: float
    z_m: float
    elevation_m: float = Field(0.0, ge=0)

    @field_validator("name", mode="before")
    @classmethod
    def plain_name(cls, value):
        return clean_plain_text(value)


class PhotoEstimateReply(StrictModel):
    """El sobre de la respuesta. objects queda sin validar aquí: cada uno se valida aparte."""

    room: EstimatedRoom
    description: str = ""
    objects: list[Any] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def short_description(cls, value):
        return _clean_description(value)


@dataclass
class PlacedObject:
    """Objeto aceptado, en metros, listo para guardarse en muebles_sugeridos (centro y base)."""

    name: str
    width_m: float
    depth_m: float
    height_m: float
    center_x: float
    center_z: float
    elevation_m: float


@dataclass
class PhotoPlan:
    room: EstimatedRoom
    description: str
    objects: list[PlacedObject] = field(default_factory=list)
    discarded: list[tuple[str, str]] = field(default_factory=list)  # (nombre, motivo)


def _safe_name(raw):
    """Nombre de un objeto descartado: el suyo si es texto plano válido, si no UNNAMED."""
    try:
        return clean_plain_text(raw.get("name")) if isinstance(raw, dict) else UNNAMED
    except ValueError:
        return UNNAMED


def _fit_axis(center, size, side):
    """Centro ajustado para que [center - size/2, center + size/2] quede en [0, side], o None.

    Solo se desplaza si el exceso es de FIT_TOLERANCE_M o menos.
    """
    if size > side + EPSILON:
        return None
    low, high = center - size / 2, center + size / 2
    shift = -low if low < 0 else (side - high if high > side else 0.0)
    if abs(shift) > FIT_TOLERANCE_M + EPSILON:
        return None
    return center + shift


def _place(item, room):
    """El objeto colocado dentro de la sala, o None si se sale más de FIT_TOLERANCE_M."""
    width, depth, height = cm_to_m(item.width_cm), cm_to_m(item.depth_cm), cm_to_m(item.height_cm)
    center_x = _fit_axis(item.x_m, width, room.width_m)
    center_z = _fit_axis(item.z_m, depth, room.length_m)
    elevation = 0.0 if item.kind == "floor" else item.elevation_m
    # En vertical solo puede sobrar por arriba (la base nunca es negativa).
    excess = elevation + height - room.height_m
    if excess > 0:
        elevation = elevation - excess if excess <= FIT_TOLERANCE_M + EPSILON else -1.0
    if center_x is None or center_z is None or elevation < -EPSILON:
        return None
    return PlacedObject(item.name, width, depth, height, center_x, center_z, max(elevation, 0.0))


def build_photo_plan(data):
    """Valida la respuesta de la IA y decide qué objetos se aceptan.

    Args:
        data: el JSON ya decodificado de la respuesta del modelo.

    Returns:
        PhotoPlan con la sala, la descripción limpia, hasta MAX_PHOTO_OBJECTS objetos colocados y los
        descartados con su motivo.

    Raises:
        EstimateError: el sobre no cumple el esquema o la sala está fuera de los límites.
    """
    try:
        reply = PhotoEstimateReply.model_validate(data)
    except ValidationError:
        # Sin encadenar: el error de Pydantic incluye la entrada, que es salida del modelo.
        raise EstimateError("estimación inválida") from None

    plan = PhotoPlan(room=reply.room, description=reply.description)
    for raw in reply.objects[:MAX_OBJECTS_CHECKED]:
        try:
            item = EstimatedObject.model_validate(raw)
        except ValidationError:
            plan.discarded.append((_safe_name(raw), REASON_INVALID))
            continue
        placed = _place(item, reply.room)
        if placed is None:
            plan.discarded.append((item.name, REASON_OUTSIDE))
        elif len(plan.objects) >= MAX_PHOTO_OBJECTS:
            plan.discarded.append((item.name, REASON_LIMIT))
        else:
            plan.objects.append(placed)
    return plan
