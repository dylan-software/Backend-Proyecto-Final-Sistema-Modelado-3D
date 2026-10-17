"""JSON contract of the room endpoints: camelCase on the wire, snake_case in Python.

Coordinates are meters. A vertex's y is the room's z axis (depth), as in app.geometry.room.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

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

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        # Runs before min_length, so a name made only of spaces is rejected instead of stored empty.
        return value.strip() if isinstance(value, str) else value


class VertexInsert(Vertex):
    after_index: int = Field(ge=0)


class RoomDetail(CamelModel):
    id: int
    name: str
    vertices: list[Vertex]
    height_m: float
    area_m2: float


class RoomItem(CamelModel):
    """A piece placed in the room. x/y are the MINIMUM corner of its footprint, in meters."""

    id: int
    name: str
    x: float
    y: float
    rotation: float


class RoomItemsOut(CamelModel):
    items: list[RoomItem]


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
    """Result of POST /rooms/{id}/analyze-photo: the room and the AI furniture now placed in it."""

    room: RoomDetail
    description: str
    items: list[RoomItem]
    discarded: list[DiscardedItem]
    visual_context: VisualContextOut
