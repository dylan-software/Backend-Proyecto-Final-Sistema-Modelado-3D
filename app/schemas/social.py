"""Contrato JSON de la red social "Habita" (CONTRATO-FASE9.md, sección 2.3): camelCase en el cable.

Lo público se arma campo a campo: código público, título, descripción de la publicación, fechas, copias y
la planta. Nunca correos, nombres de usuario, ids de cuenta, de sala, de objeto ni el id interno.
"""

from datetime import datetime

from pydantic import ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from app.schemas.room import CamelModel, Vertex
from app.schemas.text import MAX_PROJECT_NAME_LENGTH, optional_description, plain_name

# Publicaciones por página del catálogo y última página que se puede pedir.
CATALOG_PAGE_SIZE = 24
MAX_CATALOG_PAGE = 100


class PublicationWrite(CamelModel):
    """Cuerpo de PUT /rooms/{id}/publication: título obligatorio; descripción opcional (null o "" = sin)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    title: str
    description: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def check_title(cls, value):
        return plain_name(value, MAX_PROJECT_NAME_LENGTH)

    @field_validator("description", mode="before")
    @classmethod
    def check_description(cls, value):
        return optional_description(value)


class OwnPublication(CamelModel):
    """La publicación de un proyecto, vista por su dueño."""

    code: str
    title: str
    description: str | None
    published_at: datetime
    updated_at: datetime
    copies: int


class RoomPublicationStatus(CamelModel):
    """GET /rooms/{id}/publication: si el proyecto está publicado y cómo."""

    published: bool
    publication: OwnPublication | None


class PublicRoom(CamelModel):
    vertices: list[Vertex]
    height_m: float
    area_m2: float


class PublicPublication(CamelModel):
    """Una tarjeta del catálogo. `mine` dice si es de quien mira; no identifica a nadie."""

    code: str
    title: str
    description: str | None
    published_at: datetime
    updated_at: datetime
    copies: int
    mine: bool
    room: PublicRoom
    item_count: int


class PublicationCatalog(CamelModel):
    items: list[PublicPublication]
    page: int
    has_more: bool
