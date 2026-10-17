"""Rooms as irregular, editable polygons: create, read and edit them, list and lay out their furniture.

Every endpoint needs a session token and only touches rooms of that account. A room of another account
answers 404, exactly like one that does not exist, so ids of other accounts cannot be probed.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_session
from app.geometry.layout import LayoutItem, footprint_center, footprint_corner, run_layout
from app.geometry.room import Room, RoomError
from app.geometry.units import cm_to_m
from app.models.space import Habitacion, MuebleCatalogo, MuebleSugerido
from app.routers.auth import get_current_user
from app.routers.errors import DATABASE_ERRORS, database_unavailable
from app.schemas.room import (
    LayoutRequest,
    LayoutResponse,
    RoomCreate,
    RoomDetail,
    RoomItem,
    RoomItemsOut,
    Vertex,
    VertexInsert,
)

router = APIRouter(prefix="/api/v1/spaces/rooms", tags=["rooms"])

RoomId = Path(gt=0)
VertexIndex = Path(ge=0)


def room_detail(row):
    """API view of a habitaciones row; rooms without vertices are their rectangle."""
    geometry = room_geometry(row)
    return RoomDetail(
        id=row.id,
        name=row.nombre,
        vertices=geometry.vertices_json(),
        height_m=geometry.height_m,
        area_m2=round(geometry.area_m2(), 2),
    )


def room_geometry(row):
    return Room.from_record(row.vertices, row.ancho, row.profundidad, row.alto)


async def load_owned_room(session, room_id, user_id, *options):
    """The room with that id if it belongs to user_id.

    Raises:
        HTTPException: 404 no such room for this account, 503 database failure.
    """
    query = (
        select(Habitacion).where(Habitacion.id == room_id, Habitacion.user_id == user_id).options(*options)
    )
    try:
        row = await session.scalar(query)
    except DATABASE_ERRORS as exc:
        raise database_unavailable() from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe una habitación con id {room_id}.")
    return row


async def _commit(session):
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_unavailable() from exc


def _invalid_room(exc):
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Habitación inválida: {exc}")


async def _edit_room(session, room_id, user_id, edit):
    """Apply edit(Room) to a stored room and save it, or answer 422 leaving the room unchanged."""
    row = await load_owned_room(session, room_id, user_id)
    geometry = room_geometry(row)
    try:
        edit(geometry)
    except RoomError as exc:
        raise _invalid_room(exc) from exc
    # to_record builds a NEW vertices list: SQLAlchemy only notices a JSON change when the attribute
    # is reassigned, never when the stored list is mutated in place.
    for column, value in geometry.to_record().items():
        setattr(row, column, value)
    await _commit(session)
    return room_detail(row)


@router.post("", response_model=RoomDetail, status_code=status.HTTP_201_CREATED)
async def create_room(
    body: RoomCreate,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Store a room drawn as a polygon (at least 3 vertices, meters) with its ceiling height.

    Returns:
        201 with the room, its vertices and its floor area.

    Raises:
        HTTPException: 401 no valid session, 422 the polygon is invalid (too small, self-intersecting,
            ...), 503 database failure.
    """
    try:
        geometry = Room([(v.x, v.y) for v in body.vertices], body.height_m)
    except RoomError as exc:
        raise _invalid_room(exc) from exc
    row = Habitacion(user_id=current_user, nombre=body.name, **geometry.to_record())
    session.add(row)
    await _commit(session)
    return room_detail(row)


@router.get("/{room_id}", response_model=RoomDetail)
async def get_room(
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """A room of the session's account. Rooms created by /analyze come back as their rectangle.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 503 database failure.
    """
    return room_detail(await load_owned_room(session, room_id, current_user))


@router.patch("/{room_id}/vertices/{index}", response_model=RoomDetail)
async def move_vertex(
    body: Vertex,
    room_id: int = RoomId,
    index: int = VertexIndex,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Move one vertex (drag-to-edit). The others stay where they are.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 index out of
            range or the polygon would become invalid (the room is not changed), 503 database failure.
    """
    return await _edit_room(
        session, room_id, current_user, lambda room: room.move_vertex(index, (body.x, body.y))
    )


@router.post("/{room_id}/vertices", response_model=RoomDetail)
async def insert_vertex(
    body: VertexInsert,
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Insert a vertex after afterIndex, splitting that edge.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 afterIndex out of
            range or the polygon would become invalid, 503 database failure.
    """
    return await _edit_room(
        session, room_id, current_user, lambda room: room.insert_vertex(body.after_index, (body.x, body.y))
    )


@router.delete("/{room_id}/vertices/{index}", response_model=RoomDetail)
async def remove_vertex(
    room_id: int = RoomId,
    index: int = VertexIndex,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Remove a vertex. A room always keeps at least 3.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 index out of
            range, only 3 vertices left or the polygon would become invalid, 503 database failure.
    """
    return await _edit_room(session, room_id, current_user, lambda room: room.remove_vertex(index))


# --- Placed furniture and generated layout ----------------------------------------------------------


@router.get("/{room_id}/items", response_model=RoomItemsOut)
async def list_room_items(
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Furniture placed in the room, by the generated layout or by /analyze.

    x/y are the minimum corner of each footprint in meters (the database stores the center).

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 503 database failure.
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    items = []
    for piece in sorted(row.muebles, key=lambda piece: piece.id):
        x, y = footprint_corner(piece.posicion_x, piece.posicion_z, piece.ancho, piece.profundidad)
        items.append(RoomItem(id=piece.id, name=piece.nombre, x=x, y=y, rotation=0))
    return RoomItemsOut(items=items)


async def _catalog_for_layout(session, user_id, item_ids):
    """Catalog items of the account to place: the requested ids in that order, or the whole catalog.

    Raises:
        HTTPException: 404 a requested id is not in the account's catalog, 422 nothing to place,
            503 database failure.
    """
    query = select(MuebleCatalogo).where(MuebleCatalogo.user_id == user_id).order_by(MuebleCatalogo.id)
    if item_ids is not None:
        item_ids = list(dict.fromkeys(item_ids))  # repeated ids are placed once, first order kept
        query = query.where(MuebleCatalogo.id.in_(item_ids))
    try:
        catalog = (await session.scalars(query)).all()
    except DATABASE_ERRORS as exc:
        raise database_unavailable() from exc
    if item_ids is not None:
        by_id = {item.id: item for item in catalog}
        missing = [item_id for item_id in item_ids if item_id not in by_id]
        if missing:
            # Items of other accounts are reported the same way as items that do not exist.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No existen en tu catálogo los muebles con id {', '.join(map(str, missing))}.",
            )
        catalog = [by_id[item_id] for item_id in item_ids]
    if not catalog:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Tu catálogo no tiene muebles para distribuir."
        )
    return catalog


@router.post("/{room_id}/layout", response_model=LayoutResponse)
async def generate_room_layout(
    body: LayoutRequest,
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Place catalog furniture inside the room's real polygon and store the result.

    The room and the catalog are always read from the database, never taken from the client. No AI is
    involved: the AI proposal is /analyze. When the layout is ready to render (no collisions), it
    replaces the furniture placed in the room before; otherwise nothing is stored. minClearPathCm is
    accepted but not used yet, because no doors are stored.

    Returns:
        200 with {layout: {positions, warnings}, validation, lighting, readyToRender, errors}.

    Raises:
        HTTPException: 401 no valid session, 404 no such room or catalog item for this account,
            422 empty catalog or invalid constraints, 503 database failure.
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    catalog = await _catalog_for_layout(session, current_user, body.item_ids)
    # The catalog is in centimeters; the layout works in meters (app.geometry.units).
    pieces = [LayoutItem(c.id, c.nombre, cm_to_m(c.ancho), cm_to_m(c.profundidad)) for c in catalog]
    result = run_layout(room_geometry(row), pieces, body.constraints.min_distance_between_items_cm)

    if result.ready_to_render:
        by_id = {c.id: c for c in catalog}
        placed = []
        for position in result.layout.positions:
            item = by_id[position.item_id]
            width, depth = cm_to_m(item.ancho), cm_to_m(item.profundidad)
            center_x, center_z = footprint_center(position.x, position.y, width, depth)
            placed.append(
                MuebleSugerido(
                    nombre=item.nombre,
                    ancho=width,
                    alto=cm_to_m(item.alto),
                    profundidad=depth,
                    posicion_x=center_x,
                    posicion_y=0.0,
                    posicion_z=center_z,
                )
            )
        # delete-orphan cascade: the previous furniture of the room is deleted in the same commit.
        row.muebles = placed
        await _commit(session)

    return LayoutResponse.model_validate(asdict(result))
