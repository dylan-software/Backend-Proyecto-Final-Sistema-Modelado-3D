"""JSON contract of the room endpoints: camelCase on the wire, snake_case in Python.

Coordinates are meters. A vertex's y is the room's z axis (depth), as in app.geometry.room.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

# Sanity limits: far beyond any real room, small enough to keep the geometry checks cheap.
MAX_COORDINATE_M = 1000
MAX_VERTICES = 200
MAX_HEIGHT_M = 20


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
