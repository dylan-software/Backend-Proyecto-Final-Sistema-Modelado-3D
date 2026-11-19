"""Contrato JSON de la red social "Habita" (CONTRATO-FASE9.md, sección 2.3): camelCase en el cable.

Lo público se arma campo a campo: código público, título, descripción de la publicación, fechas, copias y
la planta. Nunca correos, nombres de usuario, ids de cuenta, de sala, de objeto ni el id interno.
"""

from datetime import datetime

from pydantic import ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from app.models.space import MAX_DISPLAY_NAME_LENGTH
from app.schemas.room import CamelModel, Vertex
from app.schemas.text import (
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
    optional_description,
    plain_name,
)

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
        # Mismo límite que la descripción del proyecto (MAX_PROJECT_DESCRIPTION_LENGTH).
        return optional_description(value, MAX_PROJECT_DESCRIPTION_LENGTH)


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


# --- Perfiles públicos (CONTRATO-HABITA-PERFILES.md, parte A) ------------------------------------------


def display_name_or_none(value):
    """Nombre visible validado: None si falta o queda vacío tras recortar (= anónimo).

    Raises:
        ValueError: no es texto, pasa de MAX_DISPLAY_NAME_LENGTH o tiene '<', '>' o caracteres de control.
            El mensaje nunca repite el texto.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return plain_name(value, MAX_DISPLAY_NAME_LENGTH)


class ProfileWrite(CamelModel):
    """Cuerpo de PATCH /social/me/profile. `displayName` es obligatorio; null o "" = anónimo."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    display_name: str | None

    @field_validator("display_name", mode="before")
    @classmethod
    def check_display_name(cls, value):
        return display_name_or_none(value)


class AuthorOut(CamelModel):
    """Autor de una tarjeta: solo el código público y el nombre que eligió (None = anónimo)."""

    code: str
    display_name: str | None
    mine: bool


class ProfileOut(CamelModel):
    """Perfil público: código, nombre visible, número de publicaciones públicas y si es de quien mira."""

    code: str
    display_name: str | None
    publication_count: int
    mine: bool


class AuthorCard(PublicPublication):
    """Tarjeta del catálogo por autor: su publicación pública más reciente, con el autor.

    `author` es None solo si la cuenta no tiene perfil (no debería pasar tras el relleno de la 007).
    """

    author: AuthorOut | None
    author_publication_count: int


class AuthorCatalog(CamelModel):
    items: list[AuthorCard]
    page: int
    has_more: bool


class ProfilePage(CamelModel):
    """GET /social/profiles/{code}: el perfil y una página de sus publicaciones públicas."""

    profile: ProfileOut
    items: list[PublicPublication]
    page: int
    has_more: bool


class MyProfile(CamelModel):
    """GET /social/me/profile: el perfil de la cuenta, o None si aún no tiene."""

    profile: ProfileOut | None
