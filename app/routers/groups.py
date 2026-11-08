"""Grupos de proyectos (carpetas) de cada cuenta: listar, crear y editar (CONTRATO-GRUPOS.md).

Todos los endpoints piden token y solo tocan grupos de esa cuenta: uno ajeno responde 404, igual que uno
que no existe. No gastan cuota de IA. No hay borrado de grupos todavía.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import get_session
from app.models.space import Habitacion, ProjectGroup
from app.routers.auth import get_current_user
from app.routers.errors import DATABASE_ERRORS, database_error
from app.schemas.group import GroupCreate, GroupOut, GroupsOut, GroupUpdate
from app.services.project_tree import build_project_tree, groups_with_counts

router = APIRouter(prefix="/api/v1/spaces/groups", tags=["groups"])

GroupId = Path(gt=0)


def group_out(group, project_count):
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        project_count=project_count,
    )


async def load_owned_group(session, group_id, user_id):
    """El grupo con ese id si es de user_id.

    Raises:
        HTTPException: 404 no existe para esta cuenta, 503/500 fallo de base de datos.
    """
    query = select(ProjectGroup).where(ProjectGroup.id == group_id, ProjectGroup.user_id == user_id)
    try:
        group = await session.scalar(query)
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe un grupo con id {group_id}.")
    return group


async def _project_count(session, group_id, user_id):
    query = select(func.count()).where(Habitacion.group_id == group_id, Habitacion.user_id == user_id)
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


@router.get("", response_model=GroupsOut)
async def list_groups(
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Los grupos de la cuenta, del más antiguo al más nuevo, con su número de proyectos.

    Grupos y proyectos se arman como un árbol n-ario (cuenta -> grupos -> proyectos) y se listan y cuentan
    con un recorrido en profundidad (app/services/project_tree.py).

    Raises:
        HTTPException: 401 sin sesión válida, 503/500 fallo de base de datos.
    """
    groups_query = select(ProjectGroup).where(ProjectGroup.user_id == current_user).order_by(ProjectGroup.id)
    projects_query = (
        select(Habitacion.id, Habitacion.group_id)
        .where(Habitacion.user_id == current_user, Habitacion.group_id.is_not(None))
        .order_by(Habitacion.id)
    )
    try:
        groups = (await session.scalars(groups_query)).all()
        projects = (await session.execute(projects_query)).all()
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    tree = build_project_tree(groups, projects)
    return GroupsOut(groups=[group_out(group, count) for group, count in groups_with_counts(tree)])


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Crea un grupo vacío de la cuenta.

    Raises:
        HTTPException: 401 sin sesión válida, 409 la cuenta ya tiene MAX_GROUPS_PER_USER grupos, 422 nombre
            o descripción inválidos, 503/500 fallo de base de datos.
    """
    limit = get_settings().max_groups_per_user
    try:
        existing = await session.scalar(
            select(func.count()).select_from(ProjectGroup).where(ProjectGroup.user_id == current_user)
        )
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    if existing >= limit:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ya tienes {limit} grupos; usa uno de los que tienes.")
    group = ProjectGroup(user_id=current_user, name=body.name, description=body.description)
    session.add(group)
    await _commit(session)
    return group_out(group, 0)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    body: GroupUpdate,
    group_id: int = GroupId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Cambia el nombre o la descripción de un grupo (description null o "" la borra).

    Raises:
        HTTPException: 401 sin sesión válida, 404 grupo ajeno o inexistente, 422 cuerpo vacío o campos
            inválidos, 503/500 fallo de base de datos.
    """
    group = await load_owned_group(session, group_id, current_user)
    if "name" in body.model_fields_set:
        group.name = body.name
    if "description" in body.model_fields_set:
        group.description = body.description
    await _commit(session)
    return group_out(group, await _project_count(session, group.id, current_user))
