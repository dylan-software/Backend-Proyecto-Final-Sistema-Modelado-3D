"""JSON contract of the room endpoints: camelCase on the wire, snake_case in Python.

Coordinates are meters. A vertex's y is the room's z axis (depth), as in app.geometry.room.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.schemas.photo import OBJECT_MAX_SIDE_CM, OBJECT_MIN_SIDE_CM, clean_plain_text
from app.schemas.text import MAX_PROJECT_NAME_LENGTH, optional_description, plain_name

# Sanity limits: far beyond any real room, small enough to keep the geometry checks cheap.
MAX_COORDINATE_M = 1000
MAX_VERTICES = 200
MAX_HEIGHT_M = 20
MAX_LAYOUT_ITEMS = 200


class CamelModel(BaseModel):
    """Reads and writes camelCase JSON (heightM); Python code keeps snake_case (height_m)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Vertex(CamelModel):
    x: float = Field(ge=-MAX_COORDINATE_M, le=MAX_COORDINATE_M, allow_inf_nan=False)
    y: float = Field(ge=-MAX_COORDINATE_M, le=MAX_COORDINATE_M, allow_inf_nan=False)


class RoomCreate(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    vertices: list[Vertex] = Field(min_length=3, max_length=MAX_VERTICES)
    height_m: float = Field(gt=0, le=MAX_HEIGHT_M, allow_inf_nan=False)
    # Grupo del proyecto (null = sin grupo) y su descripción (CONTRATO-GRUPOS.md).
    group_id: int | None = Field(None, gt=0)
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        # Runs before min_length, so a name made only of spaces is rejected instead of stored empty.
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def check_description(cls, value):
        return optional_description(value)


class RoomUpdate(CamelModel):
    """PATCH /rooms/{id}: al menos un campo. groupId null saca el proyecto de su grupo; description null o
    "" borra la descripción. No toca vértices, altura ni muebles."""

    name: str | None = None
    description: str | None = None
    group_id: int | None = Field(None, gt=0)

    @field_validator("name", mode="before")
    @classmethod
    def check_name(cls, value):
        return plain_name(value, MAX_PROJECT_NAME_LENGTH)

    @field_validator("description", mode="before")
    @classmethod
    def check_description(cls, value):
        return optional_description(value)

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("indica al menos un campo: name, description o groupId")
        return self


class VertexInsert(Vertex):
    after_index: int = Field(ge=0)


def _is_none(value):
    return value is None


class RoomDetail(CamelModel):
    id: int
    name: str
    vertices: list[Vertex]
    height_m: float
    area_m2: float
    # Solo aparecen cuando tienen valor: el cliente trata "ausente" como null (PREGUNTAS.md, 1).
    description: str | None = Field(None, exclude_if=_is_none)
    group_id: int | None = Field(None, exclude_if=_is_none)


class RoomItem(CamelModel):
    """Un mueble colocado en la sala. x/y son la esquina MÍNIMA de su huella, en metros.

    widthM (eje x), depthM (eje z), heightM y elevationM (altura de la base) permiten dibujarlo como
    una caja sin otra petición.
    """

    id: int
    name: str
    x: float
    y: float
    rotation: float
    width_m: float
    depth_m: float
    height_m: float
    elevation_m: float


class RoomItemsOut(CamelModel):
    items: list[RoomItem]


# --- Objetos de la sala: añadir, mover o cambiar medidas (CONTRATO-FASE0.md) ------------------------

WallSide = Literal["back", "front", "left", "right"]


class StrictCamelModel(CamelModel):
    """camelCase, campos desconocidos rechazados, sin conversiones de tipo ("2" no es 2) ni NaN/infinitos."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", strict=True, allow_inf_nan=False
    )


def _object_name(value):
    return clean_plain_text(value)


class ItemCreate(StrictCamelModel):
    """POST /rooms/{id}/items. Medidas en cm; widthCm a lo largo de la pared y depthCm su grosor."""

    name: str
    kind: Literal["floor", "wall"]
    wall: WallSide | None = None
    width_cm: float = Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    depth_cm: float = Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    height_cm: float = Field(ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    elevation_cm: float | None = Field(None, ge=0, le=OBJECT_MAX_SIDE_CM)

    @field_validator("name", mode="before")
    @classmethod
    def plain_name(cls, value):
        return _object_name(value)

    @model_validator(mode="after")
    def wall_only_for_wall_objects(self):
        if self.kind == "wall" and self.wall is None:
            raise ValueError("un objeto de pared necesita wall: back, front, left o right")
        if self.kind == "floor" and (self.wall is not None or self.elevation_cm is not None):
            raise ValueError("wall y elevationCm solo se usan con kind=wall")
        return self


class ItemPlacement(StrictCamelModel):
    """Posición pedida: x/y = esquina mínima en metros (como GET /items); con wall se pega a esa pared."""

    x: float = Field(ge=-MAX_COORDINATE_M, le=MAX_COORDINATE_M)
    y: float = Field(ge=-MAX_COORDINATE_M, le=MAX_COORDINATE_M)
    elevation_m: float | None = Field(None, ge=0, le=MAX_HEIGHT_M)
    wall: WallSide | None = None


class ItemUpdate(StrictCamelModel):
    """PATCH /rooms/{id}/items/{itemId}: al menos un campo. widthCm/depthCm = extensión en x/z."""

    name: str | None = None
    width_cm: float | None = Field(None, ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    depth_cm: float | None = Field(None, ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    height_cm: float | None = Field(None, ge=OBJECT_MIN_SIDE_CM, le=OBJECT_MAX_SIDE_CM)
    position: ItemPlacement | None = None

    @field_validator("name", mode="before")
    @classmethod
    def plain_name(cls, value):
        return _object_name(value)

    @model_validator(mode="after")
    def at_least_one_field(self):
        values = (self.name, self.width_cm, self.depth_cm, self.height_cm, self.position)
        if all(value is None for value in values):
            raise ValueError("indica al menos un campo: name, widthCm, depthCm, heightCm o position")
        return self


class PlacedItemResponse(CamelModel):
    """Resultado de POST y PATCH: el objeto guardado, la pared a la que quedó pegado (o null) y si la
    posición final difiere de la pedida."""

    item: RoomItem
    wall: WallSide | None
    adjusted: bool


class LayoutConstraints(CamelModel):
    min_distance_between_items_cm: float = Field(gt=0, le=500, allow_inf_nan=False)
    # Accepted for compatibility with the old contract but not used yet: no doors are stored.
    min_clear_path_cm: float = Field(gt=0, le=1000, allow_inf_nan=False)


class LayoutRequest(CamelModel):
    constraints: LayoutConstraints
    # Catalog items to place; all of the account's catalog when omitted.
    item_ids: list[int] | None = Field(None, min_length=1, max_length=MAX_LAYOUT_ITEMS)


class ItemPositionOut(CamelModel):
    item_id: int
    x: float
    y: float
    rotation: float


class LayoutOut(CamelModel):
    positions: list[ItemPositionOut]
    warnings: list[str]


class CollisionOut(CamelModel):
    item_a: int
    item_b: int
    overlap_cm: float


class ValidationOut(CamelModel):
    valid: bool
    collisions: list[CollisionOut]


class Vector3(CamelModel):
    x: float
    y: float
    z: float


class LightingOut(CamelModel):
    position: Vector3
    intensity: float
    color: str
    source: str


class LayoutResponse(CamelModel):
    layout: LayoutOut
    validation: ValidationOut
    lighting: LightingOut
    ready_to_render: bool
    errors: list[str]


class DiscardedItem(CamelModel):
    name: str
    reason: str


class ColorShareOut(CamelModel):
    hex: str
    share: float


class VisualContextOut(CamelModel):
    brightness: float
    colors: list[ColorShareOut]


class AnalyzePhotoResponse(CamelModel):
    """Resultado de POST /rooms/{id}/analyze-photo: la sala estimada y los objetos ya guardados en ella.

    estimated es siempre true aquí y no se guarda (no hay columna). Contrato: CONTRATO-FOTO-A-3D.md.
    """

    room: RoomDetail
    estimated: bool
    description: str
    items: list[RoomItem]
    discarded: list[DiscardedItem]


UndoneChange = Literal["photo", "chat", "add", "update", "delete", "layout", "vertices"]


class RoomUndoResponse(CamelModel):
    """Resultado de POST /rooms/{id}/undo (y de su alias photo-undo): la sala y los objetos de antes
    del último cambio.

    room e items tienen la misma forma que en analyze-photo; remaining dice cuántos cambios más se
    pueden deshacer y undone qué cambio se deshizo. Contrato: CONTRATO-FASE2.md, sección 1.3.
    """

    room: RoomDetail
    items: list[RoomItem]
    remaining: int
    undone: UndoneChange
