"""Perfiles públicos de la red social y catálogo por autor (CONTRATO-HABITA-PERFILES.md, parte A).

Un perfil por cuenta, con un código público aleatorio y un nombre visible opcional que escribe el dueño
(None = anónimo). Nada de aquí lee usuarios.nombre ni el correo. Las consultas no confirman la sesión: las
rutas deciden cuándo hacer commit y traducen los errores de la base.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.space import ProjectPublication, PublicProfile
from app.services.project_publication import new_public_code


def _public():
    return ProjectPublication.public.is_(True)


async def profile_of(session, user_id):
    """Perfil de la cuenta, o None si aún no tiene."""
    return await session.scalar(select(PublicProfile).where(PublicProfile.user_id == user_id))


async def profile_by_code(session, code):
    """Perfil con ese código público, o None."""
    return await session.scalar(select(PublicProfile).where(PublicProfile.code == code))


async def ensure_profile(session, user_id):
    """Perfil de la cuenta; si no tiene, lo añade ANÓNIMO a la sesión (sin commit).

    El código sale de secrets, como el de las publicaciones. Dos peticiones simultáneas de la misma cuenta
    sin perfil chocarían en uq_perfiles_publicos_user_id al confirmar (error de base, aceptado: es la
    primera publicación de una cuenta repetida a la vez).
    """
    profile = await profile_of(session, user_id)
    if profile is None:
        now = datetime.now(timezone.utc)
        profile = PublicProfile(
            user_id=user_id, code=new_public_code(), display_name=None, created_at=now, updated_at=now
        )
        session.add(profile)
    return profile


def set_display_name(profile, display_name):
    """Cambia el nombre visible (None = anónimo) y la fecha de cambio. Ya viene validado."""
    profile.display_name = display_name
    profile.updated_at = datetime.now(timezone.utc)


async def featured_publications(session, page, page_size):
    """Una publicación pública por autor: la de id más alto. Autores de la más nueva a la más vieja.

    Devuelve hasta page_size + 1 filas (la de más sirve para saber si hay otra página). Subconsulta con
    max(id) ... GROUP BY user_id en vez de DISTINCT ON, para que valga también en SQLite.
    """
    newest = (
        select(func.max(ProjectPublication.id).label("id"))
        .where(_public())
        .group_by(ProjectPublication.user_id)
        .subquery()
    )
    query = (
        select(ProjectPublication)
        .join(newest, ProjectPublication.id == newest.c.id)
        .order_by(ProjectPublication.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    return list(await session.scalars(query))


async def profiles_of(session, user_ids):
    """{user_id: PublicProfile} de las cuentas que tienen perfil."""
    ids = set(user_ids)
    if not ids:
        return {}
    rows = await session.scalars(select(PublicProfile).where(PublicProfile.user_id.in_(ids)))
    return {row.user_id: row for row in rows}


async def public_counts(session, user_ids):
    """{user_id: número de publicaciones públicas} en una sola consulta agrupada (sin N+1)."""
    ids = set(user_ids)
    if not ids:
        return {}
    query = (
        select(ProjectPublication.user_id, func.count())
        .where(_public(), ProjectPublication.user_id.in_(ids))
        .group_by(ProjectPublication.user_id)
    )
    return {user_id: count for user_id, count in await session.execute(query)}


async def profile_publications(session, user_id, page, page_size):
    """Publicaciones públicas de una cuenta, de la más nueva a la más vieja; hasta page_size + 1 filas."""
    query = (
        select(ProjectPublication)
        .where(_public(), ProjectPublication.user_id == user_id)
        .order_by(ProjectPublication.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    return list(await session.scalars(query))
