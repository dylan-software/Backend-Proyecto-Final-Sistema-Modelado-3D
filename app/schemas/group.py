"""Contrato JSON de los grupos de proyectos (CONTRATO-GRUPOS.md): camelCase en el cable."""

from datetime import datetime

from pydantic import field_validator, model_validator

from app.schemas.room import CamelModel
from app.schemas.text import MAX_GROUP_NAME_LENGTH, optional_description, plain_name


class GroupCreate(CamelModel):
    name: str
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def check_name(cls, value):
        return plain_name(value, MAX_GROUP_NAME_LENGTH)

    @field_validator("description", mode="before")
    @classmethod
    def check_description(cls, value):
        return optional_description(value)


class GroupUpdate(CamelModel):
    """Al menos un campo. description null o "" borra la descripción; name no puede ser null."""

    name: str | None = None
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def check_name(cls, value):
        return plain_name(value, MAX_GROUP_NAME_LENGTH)

    @field_validator("description", mode="before")
    @classmethod
    def check_description(cls, value):
        return optional_description(value)

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("indica al menos un campo: name o description")
        return self


class GroupOut(CamelModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    project_count: int


class GroupsOut(CamelModel):
    groups: list[GroupOut]
