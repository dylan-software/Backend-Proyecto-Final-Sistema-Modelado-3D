"""Archivo de proyecto exportado ("habita-project", versión 1): el esquema estricto de la importación.

El archivo llega del usuario y NO es confiable: campos desconocidos rechazados, sin conversiones de tipo
("2" no es 2), sin NaN ni infinitos y con topes de tamaño en todas las listas. Las claves van en
snake_case, como el archivo que exporta el frontend (CONTRATO-FASE6.md, sección 1). No lleva ids, correos
ni datos de la cuenta: un campo así es un campo de más y se rechaza.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.photo import OBJECT_MAX_SIDE_CM, OBJECT_MIN_SIDE_CM, clean_plain_text
from app.schemas.room import MAX_COORDINATE_M, MAX_HEIGHT_M, MAX_VERTICES, WallSide
from app.schemas.text import MAX_PROJECT_NAME_LENGTH, plain_name

PROJECT_FILE_FORMAT = "habita-project"
PROJECT_FILE_VERSION = 1
# Topes del archivo (PLAN-COMPLETO.md, FASE 6). Los vértices, el mismo tope que crear y editar una sala
# (POST /rooms y POST /rooms/{id}/vertices): así todo proyecto exportado se puede volver a importar.
MAX_FILE_VERTICES = MAX_VERTICES
MAX_FILE_ITEMS = 200
MAX_FILE_PARTS = 8
# Medidas de una parte (solo se validan: no hay columna donde guardarlas).
PART_MIN_SIDE_CM = 0.5
PART_MAX_OFFSET_CM = 400.0

Coordinate = Annotated[float, Field(ge=-MAX_COORDINATE_M, le=MAX_COORDINATE_M)]
ObjectSide = Annotated[float, Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)]
PartSide = Annotated[float, Field(ge=PART_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)]
PartOffset = Annotated[float, Field(ge=-PART_MAX_OFFSET_CM, le=PART_MAX_OFFSET_CM)]


class FileModel(BaseModel):
    """Campos desconocidos rechazados, sin conversiones de tipo y sin NaN ni infinitos."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class FilePart(FileModel):
    """Una pieza del dibujo de un objeto. Se valida y se descarta: la base aún no guarda partes."""

    shape: Literal["box", "cylinder", "sphere", "plane"]
    size_cm: list[PartSide] = Field(min_length=3, max_length=3)
    offset_cm: list[PartOffset] = Field(min_length=3, max_length=3)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class FilePosition(FileModel):
    """x_m/z_m = CENTRO de la huella; elevation_m = altura de la base. Solo se admite rotation_deg 0."""

    x_m: Coordinate
    z_m: Coordinate
    elevation_m: float = Field(ge=0, le=MAX_HEIGHT_M)
    rotation_deg: float = Field(ge=0, le=0)


class FileItem(FileModel):
    """Un objeto: size_cm = [ancho (eje x), alto, fondo (eje z)] en cm."""

    name: str
    category: str | None
    kind: Literal["floor", "wall"]
    size_cm: list[ObjectSide] = Field(min_length=3, max_length=3)
    position: FilePosition
    wall: WallSide | None
    parts: list[FilePart] = Field(max_length=MAX_FILE_PARTS)

    @field_validator("name", mode="before")
    @classmethod
    def check_name(cls, value):
        return clean_plain_text(value)

    @field_validator("category", mode="before")
    @classmethod
    def check_category(cls, value):
        return None if value is None else clean_plain_text(value)


class FileRoom(FileModel):
    """Mismos límites que POST /rooms (decisión del usuario), también el máximo de vértices."""

    height_m: float = Field(gt=0, le=MAX_HEIGHT_M)
    vertices: list[Annotated[list[Coordinate], Field(min_length=2, max_length=2)]] = Field(
        min_length=3, max_length=MAX_FILE_VERTICES
    )
    estimated: bool


class ProjectFile(FileModel):
    format: Literal["habita-project"]
    version: Literal[1]
    name: str
    room: FileRoom
    items: list[FileItem] = Field(max_length=MAX_FILE_ITEMS)

    @field_validator("name", mode="before")
    @classmethod
    def check_name(cls, value):
        return plain_name(value, MAX_PROJECT_NAME_LENGTH)
