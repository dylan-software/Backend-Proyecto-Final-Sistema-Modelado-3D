"""Red social "Habita" (CONTRATO-FASE9.md): publicar, despublicar, catálogo y copiar a mi cuenta.

Todas las rutas piden sesión. Un proyecto es privado hasta que su dueño lo publica: entonces se guarda una
copia congelada en el formato del archivo exportado (app/services/project_publication.py), y copiar una
publicación es importarla con el mismo código que POST /rooms/import. Lo público nunca lleva correos,
nombres de usuario ni ids de cuenta, de sala o de objeto: solo el código público aleatorio.

Perfiles (CONTRATO-HABITA-PERFILES.md, parte A): catálogo por autor en /social/authors (el de
/social/publications no cambia), página de un perfil y el nombre visible propio en /social/me/profile.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database.connection import get_session
from app.models.space import Habitacion, ProjectPublication
from app.routers.auth import get_current_user
from app.routers.errors import DATABASE_ERRORS, database_error
from app.routers.rooms import load_owned_room, save_imported_project
from app.schemas.room import RoomImportResponse
from app.schemas.social import (
    CATALOG_PAGE_SIZE,
    MAX_CATALOG_PAGE,
    AuthorCard,
    AuthorCatalog,
    AuthorOut,
    MyProfile,
    OwnPublication,
    ProfileOut,
    ProfilePage,
    ProfileWrite,
    PublicationCatalog,
    PublicationWrite,
    PublicPublication,
    PublicRoom,
    RoomPublicationStatus,
)
from app.services import public_profile
from app.services.project_import import MAX_IMPORT_BYTES, ProjectImportError
from app.services.project_publication import (
    PUBLIC_CODE_PATTERN,
    PublicationTooLarge,
    new_public_code,
    plan_copy,
    room_summary,
    snapshot_project,
)
from app.utils.rate_limit import (
    check_import_quota,
    check_profile_change_quota,
    check_publication_change_quota,
    enforce_social_read,
    record_profile_change,
    record_publication_change,
)

router = APIRouter(tags=["social"])

ROOM_PUBLICATION_PATH = "/api/v1/spaces/rooms/{room_id}/publication"
CATALOG_PATH = "/api/v1/social/publications"
AUTHORS_PATH = "/api/v1/social/authors"
PROFILES_PATH = "/api/v1/social/profiles"
MY_PROFILE_PATH = "/api/v1/social/me/profile"

RoomId = Path(gt=0)
PublicCode = Path(pattern=PUBLIC_CODE_PATTERN)

NOT_PUBLISHED = "Este proyecto no está publicado."
NO_SUCH_PUBLICATION = "No existe una publicación pública con ese código."
NO_SUCH_PROFILE = "Este perfil no existe."
TOO_LARGE = (
    f"El proyecto es demasiado grande para publicarlo: como archivo pasaría de {MAX_IMPORT_BYTES // 1024} KB."
)


def too_many_publications(limit):
    return (
        f"Ya tienes {limit} proyectos publicados, el máximo; deja de publicar alguno antes de publicar otro."
    )


def _as_utc(moment):
    """SQLite devuelve fechas sin zona; todas se guardan en UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def own_publication(row):
    return OwnPublication(
        code=row.code,
        title=row.title,
        description=row.description,
        published_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        copies=row.copies,
    )


def public_publication(row, viewer):
    """Tarjeta del catálogo, campo a campo desde la fila y su documento (nunca volcando la fila)."""
    summary = room_summary(row.document)
    return PublicPublication(
        code=row.code,
        title=row.title,
        description=row.description,
        published_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        copies=row.copies,
        mine=row.user_id == viewer,
        room=PublicRoom(
            vertices=summary["vertices"], height_m=summary["height_m"], area_m2=summary["area_m2"]
        ),
        item_count=summary["item_count"],
    )


async def _scalar(session, query):
    try:
        return await session.scalar(query)
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc


async def _commit(session):
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc


async def _ensure_profile(session, user_id):
    try:
        return await public_profile.ensure_profile(session, user_id)
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc


def profile_out(profile, publication_count, viewer):
    """Perfil público campo a campo (nunca el user_id ni el id interno)."""
    return ProfileOut(
        code=profile.code,
        display_name=profile.display_name,
        publication_count=publication_count,
        mine=profile.user_id == viewer,
    )


def author_card(row, profile, count, viewer):
    """Tarjeta del catálogo por autor: la publicación, su autor (None sin perfil) y cuántas tiene."""
    author = None
    if profile is not None:
        author = AuthorOut(
            code=profile.code, display_name=profile.display_name, mine=profile.user_id == viewer
        )
    card = public_publication(row, viewer)
    return AuthorCard(**dict(card), author=author, author_publication_count=count)


async def _public_count(session, user_id):
    try:
        counts = await public_profile.public_counts(session, [user_id])
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    return counts.get(user_id, 0)


async def _publication_of(session, room_id, user_id):
    query = select(ProjectPublication).where(
        ProjectPublication.room_id == room_id, ProjectPublication.user_id == user_id
    )
    return await _scalar(session, query)


@router.get(ROOM_PUBLICATION_PATH, response_model=RoomPublicationStatus)
async def get_room_publication(
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Si el proyecto de la cuenta está publicado y cómo.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala ajena o inexistente, 503/500 base de datos.
    """
    await load_owned_room(session, room_id, current_user)
    row = await _publication_of(session, room_id, current_user)
    if row is None:
        return RoomPublicationStatus(published=False, publication=None)
    return RoomPublicationStatus(published=True, publication=own_publication(row))


@router.put(
    ROOM_PUBLICATION_PATH,
    response_model=OwnPublication,
    responses={status.HTTP_201_CREATED: {"model": OwnPublication}},
)
async def publish_room(
    body: PublicationWrite,
    response: Response,
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Publica el proyecto (201) o actualiza su publicación (200) con una copia congelada nueva.

    La copia se arma desde la base y se valida con la importación de la FASE 6. Al actualizar se
    conservan el código, las copias y la fecha de publicación. No llama a la IA.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala ajena o inexistente, 429 cupo de cambios, 409 tope
            de publicaciones (solo si es nueva), 422 cuerpo inválido o proyecto no importable (mensaje fijo
            de la importación), 413 el documento pasa de 256 KB, 503/500 base de datos.
    """
    room = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    check_publication_change_quota(current_user)
    row = await _publication_of(session, room_id, current_user)
    if row is None:
        limit = get_settings().max_publications_per_user
        count_query = select(func.count()).where(ProjectPublication.user_id == current_user)
        if await _scalar(session, count_query) >= limit:
            raise HTTPException(status.HTTP_409_CONFLICT, too_many_publications(limit))
    try:
        document = snapshot_project(room, room.muebles, body.title)
    except ProjectImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None
    except PublicationTooLarge:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE) from None

    now = datetime.now(timezone.utc)
    if row is None:
        row = ProjectPublication(
            code=new_public_code(),
            user_id=current_user,
            room_id=room_id,
            public=True,
            title=body.title,
            description=body.description,
            document=document,
            copies=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        response.status_code = status.HTTP_201_CREATED
    else:
        row.public = True
        row.title = body.title
        row.description = body.description
        # Un dict NUEVO: SQLAlchemy solo detecta el cambio de una columna JSON al reasignarla.
        row.document = document
        row.updated_at = now
    # Quien publica tiene perfil (anónimo si aún no eligió nombre), en la misma transacción.
    await _ensure_profile(session, current_user)
    await _commit(session)
    record_publication_change(current_user)
    return own_publication(row)


@router.delete(ROOM_PUBLICATION_PATH, status_code=status.HTTP_204_NO_CONTENT)
async def unpublish_room(
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Deja de publicar el proyecto: BORRA la fila (no queda guardada la copia congelada).

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala ajena o inexistente o proyecto no publicado, 429
            cupo de cambios, 503/500 base de datos.
    """
    await load_owned_room(session, room_id, current_user)
    row = await _publication_of(session, room_id, current_user)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_PUBLISHED)
    check_publication_change_quota(current_user)
    await session.delete(row)
    await _commit(session)
    record_publication_change(current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(CATALOG_PATH, response_model=PublicationCatalog)
async def list_publications(
    page: int = Query(1, ge=1, le=MAX_CATALOG_PAGE),
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Catálogo público: 24 por página, de la publicación más nueva a la más vieja, solo las públicas.

    Raises:
        HTTPException: 401 sin sesión válida, 422 page fuera de 1..100, 429 cupo de lecturas, 503/500 base
            de datos.
    """
    enforce_social_read(current_user)
    query = (
        select(ProjectPublication)
        .where(ProjectPublication.public.is_(True))
        .order_by(ProjectPublication.id.desc())
        .offset((page - 1) * CATALOG_PAGE_SIZE)
        .limit(CATALOG_PAGE_SIZE + 1)
    )
    try:
        rows = list(await session.scalars(query))
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    return PublicationCatalog(
        items=[public_publication(row, current_user) for row in rows[:CATALOG_PAGE_SIZE]],
        page=page,
        has_more=len(rows) > CATALOG_PAGE_SIZE,
    )


@router.get(AUTHORS_PATH, response_model=AuthorCatalog)
async def list_authors(
    page: int = Query(1, ge=1, le=MAX_CATALOG_PAGE),
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Catálogo por autor: una tarjeta por cuenta con su publicación pública más reciente (id más alto).

    Autores de la publicación más nueva a la más vieja, 24 por página. Cada tarjeta lleva el autor (código
    público y nombre visible; None si no tiene perfil) y cuántas publicaciones públicas tiene. Tres
    consultas por página (tarjetas, perfiles y recuentos agrupados), sin N+1.

    Raises:
        HTTPException: 401 sin sesión válida, 422 page fuera de 1..100, 429 cupo de lecturas, 503/500 base
            de datos.
    """
    enforce_social_read(current_user)
    try:
        rows = await public_profile.featured_publications(session, page, CATALOG_PAGE_SIZE)
        shown = rows[:CATALOG_PAGE_SIZE]
        user_ids = [row.user_id for row in shown]
        profiles = await public_profile.profiles_of(session, user_ids)
        counts = await public_profile.public_counts(session, user_ids)
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    return AuthorCatalog(
        items=[
            author_card(row, profiles.get(row.user_id), counts.get(row.user_id, 0), current_user)
            for row in shown
        ],
        page=page,
        has_more=len(rows) > CATALOG_PAGE_SIZE,
    )


@router.get(f"{PROFILES_PATH}/{{code}}", response_model=ProfilePage)
async def get_profile(
    code: str = PublicCode,
    page: int = Query(1, ge=1, le=MAX_CATALOG_PAGE),
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Perfil público y sus publicaciones públicas, de la más nueva a la más vieja, 24 por página.

    Un perfil sin publicaciones públicas responde 200 con la lista vacía. Solo lectura, también para su
    dueño (`mine` dice si es suyo).

    Raises:
        HTTPException: 401 sin sesión válida, 422 código o page con otra forma, 429 cupo de lecturas, 404
            el código no existe, 503/500 base de datos.
    """
    enforce_social_read(current_user)
    try:
        profile = await public_profile.profile_by_code(session, code)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SUCH_PROFILE)
        rows = await public_profile.profile_publications(session, profile.user_id, page, CATALOG_PAGE_SIZE)
        counts = await public_profile.public_counts(session, [profile.user_id])
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    return ProfilePage(
        profile=profile_out(profile, counts.get(profile.user_id, 0), current_user),
        items=[public_publication(row, current_user) for row in rows[:CATALOG_PAGE_SIZE]],
        page=page,
        has_more=len(rows) > CATALOG_PAGE_SIZE,
    )


@router.get(MY_PROFILE_PATH, response_model=MyProfile)
async def get_my_profile(
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """El perfil de la cuenta, o `profile: null` si aún no tiene. No crea nada.

    Raises:
        HTTPException: 401 sin sesión válida, 503/500 base de datos.
    """
    try:
        profile = await public_profile.profile_of(session, current_user)
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    if profile is None:
        return MyProfile(profile=None)
    return MyProfile(profile=profile_out(profile, await _public_count(session, current_user), current_user))


@router.patch(MY_PROFILE_PATH, response_model=ProfileOut)
async def update_my_profile(
    body: ProfileWrite,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Cambia el nombre visible (null o vacío = anónimo); crea el perfil si la cuenta aún no tiene.

    El código público no cambia nunca. Solo los cambios guardados cuentan en PROFILE_CHANGES_PER_HOUR.

    Raises:
        HTTPException: 401 sin sesión válida, 422 cuerpo inválido (sin eco), 429 cupo de cambios, 503/500
            base de datos.
    """
    check_profile_change_quota(current_user)
    profile = await _ensure_profile(session, current_user)
    public_profile.set_display_name(profile, body.display_name)
    await _commit(session)
    record_profile_change(current_user)
    return profile_out(profile, await _public_count(session, current_user), current_user)


@router.post(
    f"{CATALOG_PATH}/{{code}}/copy",
    response_model=RoomImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def copy_publication(
    code: str = PublicCode,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Copia una publicación pública como proyecto NUEVO de la cuenta (la importación de la FASE 6).

    Ids nuevos, sin grupo ni descripción. Comparte cupo con POST /rooms/import y suma 1 a las copias de
    la publicación en la misma transacción. Se pueden copiar las publicaciones propias.

    Returns:
        201 con {room, items}, como POST /rooms/import.

    Raises:
        HTTPException: 401 sin sesión válida, 422 código con otra forma, 404 no existe o no es pública
            (mismo mensaje), 429 cupo de importar, 503/500 base de datos.
    """
    query = select(ProjectPublication).where(
        ProjectPublication.code == code, ProjectPublication.public.is_(True)
    )
    row = await _scalar(session, query)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SUCH_PUBLICATION)
    check_import_quota(current_user)
    try:
        plan = plan_copy(row.document)
    except ProjectImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None
    # UPDATE atómico (copias = copias + 1): dos copias a la vez no pierden ninguna. Va en la transacción
    # del proyecto nuevo, que save_imported_project confirma.
    increment = (
        update(ProjectPublication)
        .where(ProjectPublication.id == row.id)
        .values(copies=ProjectPublication.copies + 1)
    )
    try:
        await session.execute(increment)
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc
    return await save_imported_project(session, current_user, plan)
