"""Rooms as irregular, editable polygons: create, read and edit them, list and lay out their furniture.

Every endpoint needs a session token and only touches rooms of that account. A room of another account
answers 404, exactly like one that does not exist, so ids of other accounts cannot be probed.
"""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_session
from app.geometry.layout import LayoutItem, footprint_center, footprint_corner, run_layout
from app.geometry.room import Room, RoomError
from app.geometry.units import cm_to_m
from app.models.space import Habitacion, MuebleCatalogo, MuebleSugerido
from app.routers.auth import get_current_user
from app.routers.errors import DATABASE_ERRORS, database_error
from app.routers.groups import load_owned_group
from app.schemas.room import (
    MAX_LAYOUT_ITEMS,
    MAX_VERTICES,
    ItemCreate,
    ItemUpdate,
    LayoutRequest,
    LayoutResponse,
    PlacedItemResponse,
    RoomCreate,
    RoomDetail,
    RoomImportResponse,
    RoomItem,
    RoomItemsOut,
    RoomUndoResponse,
    RoomUpdate,
    Vertex,
    VertexInsert,
)
from app.services.project_import import ProjectImportError, load_project
from app.services.room_objects import RoomObjectError, delete_object, place_object, update_object
from app.services.room_undo import (
    NOTHING_TO_UNDO,
    commit_with_undo,
    get_room_undo_store,
    restore_room,
    snapshot_room,
)
from app.utils.rate_limit import check_import_quota, record_import

# Mensaje del 415 de POST /rooms/import.
IMPORT_NOT_JSON_TYPE = "El archivo debe enviarse como JSON."

router = APIRouter(prefix="/api/v1/spaces/rooms", tags=["rooms"])

RoomId = Path(gt=0)
VertexIndex = Path(ge=0)
ItemId = Path(gt=0)


def room_detail(row):
    """API view of a habitaciones row; rooms without vertices are their rectangle."""
    geometry = room_geometry(row)
    return RoomDetail(
        id=row.id,
        name=row.nombre,
        vertices=geometry.vertices_json(),
        height_m=geometry.height_m,
        area_m2=round(geometry.area_m2(), 2),
        description=row.description,
        group_id=row.group_id,
    )


def room_item(piece):
    """Vista de API de un muebles_sugeridos: la BD guarda el centro; la API da la esquina mínima."""
    x, y = footprint_corner(piece.posicion_x, piece.posicion_z, piece.ancho, piece.profundidad)
    return RoomItem(
        id=piece.id,
        name=piece.nombre,
        x=x,
        y=y,
        rotation=0,
        width_m=piece.ancho,
        depth_m=piece.profundidad,
        height_m=piece.alto,
        elevation_m=piece.posicion_y,
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
        raise database_error(exc) from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe una habitación con id {room_id}.")
    return row


async def _commit(session):
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc


def _invalid_room(exc):
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Habitación inválida: {exc}")


async def _edit_room(session, room_id, user_id, edit, undo_store):
    """Apply edit(Room) to a stored room and save it, or answer 422 leaving the room unchanged.

    Tras un guardado correcto, la copia de antes ("vertices") va a la pila de Deshacer de la sala.
    """
    row = await load_owned_room(session, room_id, user_id, selectinload(Habitacion.muebles))
    geometry = room_geometry(row)
    try:
        edit(geometry)
    except RoomError as exc:
        raise _invalid_room(exc) from exc
    before = snapshot_room(row, "vertices")
    # to_record builds a NEW vertices list: SQLAlchemy only notices a JSON change when the attribute
    # is reassigned, never when the stored list is mutated in place.
    for column, value in geometry.to_record().items():
        setattr(row, column, value)
    await commit_with_undo(session, row, undo_store, before)
    return room_detail(row)


@router.post("", response_model=RoomDetail, status_code=status.HTTP_201_CREATED)
async def create_room(
    body: RoomCreate,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Store a room drawn as a polygon (at least 3 vertices, meters) with its ceiling height.

    Optionally inside one of the account's groups (groupId) and with a description.

    Returns:
        201 with the room, its vertices and its floor area.

    Raises:
        HTTPException: 401 no valid session, 404 groupId of another account or missing, 422 the polygon
            is invalid (too small, self-intersecting, ...), 503 database failure.
    """
    try:
        geometry = Room([(v.x, v.y) for v in body.vertices], body.height_m)
    except RoomError as exc:
        raise _invalid_room(exc) from exc
    if body.group_id is not None:
        await load_owned_group(session, body.group_id, current_user)
    row = Habitacion(
        user_id=current_user,
        nombre=body.name,
        group_id=body.group_id,
        description=body.description,
        **geometry.to_record(),
    )
    session.add(row)
    await _commit(session)
    return room_detail(row)


@router.post("/import", response_model=RoomImportResponse, status_code=status.HTTP_201_CREATED)
async def import_room(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Crea un proyecto NUEVO de la cuenta a partir de un archivo exportado (CONTRATO-FASE6.md).

    El cuerpo es el archivo tal cual (application/json, máx. 256 KB: lo corta BodySizeLimitMiddleware
    antes de autenticar). Se lee crudo y lo valida app.services.project_import, no el modelo de cuerpo de
    FastAPI, cuyo 422 nombraría en loc los campos desconocidos (eco de la entrada). Ids nuevos, sin grupo
    ni descripción; no llama a la IA.

    Returns:
        201 con {room, items}.

    Raises:
        HTTPException: 401 sin sesión válida, 413 más de 256 KB, 415 no es application/json, 429 ya
            importó IMPORTS_PER_HOUR proyectos en la última hora, 422 archivo inválido (detail fijo en
            español, sin eco), 503/500 fallo de base de datos.
    """
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, IMPORT_NOT_JSON_TYPE)
    check_import_quota(current_user)
    try:
        plan = load_project(await request.body())
    except ProjectImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None

    row = Habitacion(user_id=current_user, nombre=plan.name, **plan.room)
    row.muebles = [MuebleSugerido(**piece) for piece in plan.pieces]
    session.add(row)
    await _commit(session)
    record_import(current_user)
    return RoomImportResponse(
        room=room_detail(row),
        items=[room_item(piece) for piece in sorted(row.muebles, key=lambda piece: piece.id)],
    )


@router.patch("/{room_id}", response_model=RoomDetail)
async def update_room(
    body: RoomUpdate,
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Renombra un proyecto, cambia su descripción o lo mueve de grupo (groupId null = sin grupo).

    No toca la forma, la altura ni los muebles.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala o groupId ajenos o inexistentes, 422 cuerpo vacío o
            campos inválidos, 503/500 fallo de base de datos.
    """
    row = await load_owned_room(session, room_id, current_user)
    fields = body.model_fields_set
    if "group_id" in fields and body.group_id is not None:
        await load_owned_group(session, body.group_id, current_user)
    if "name" in fields:
        row.nombre = body.name
    if "description" in fields:
        row.description = body.description
    if "group_id" in fields:
        row.group_id = body.group_id
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
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Move one vertex (drag-to-edit). The others stay where they are.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 index out of
            range or the polygon would become invalid (the room is not changed), 503 database failure.
    """
    return await _edit_room(
        session, room_id, current_user, lambda room: room.move_vertex(index, (body.x, body.y)), undo_store
    )


@router.post("/{room_id}/vertices", response_model=RoomDetail)
async def insert_vertex(
    body: VertexInsert,
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Insert a vertex after afterIndex, splitting that edge.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 afterIndex out of
            range, the room already has MAX_VERTICES vertices or the polygon would become invalid,
            503 database failure.
    """
    return await _edit_room(
        session,
        room_id,
        current_user,
        lambda room: _insert_vertex_within_limit(room, body.after_index, (body.x, body.y)),
        undo_store,
    )


def _insert_vertex_within_limit(room, after_index, point):
    """Inserta un vértice sin pasar del tope de POST /rooms (MAX_VERTICES).

    Así toda sala guardada cabe en RoomCreate y en el archivo de la importación.

    Raises:
        RoomError: la sala ya tiene MAX_VERTICES vértices, o los de Room.insert_vertex.
    """
    if len(room.vertices) >= MAX_VERTICES:
        raise RoomError(f"insert_vertex: a room can have at most {MAX_VERTICES} vertices")
    room.insert_vertex(after_index, point)


@router.delete("/{room_id}/vertices/{index}", response_model=RoomDetail)
async def remove_vertex(
    room_id: int = RoomId,
    index: int = VertexIndex,
    session: AsyncSession = Depends(get_session),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Remove a vertex. A room always keeps at least 3.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 index out of
            range, only 3 vertices left or the polygon would become invalid, 503 database failure.
    """
    return await _edit_room(
        session, room_id, current_user, lambda room: room.remove_vertex(index), undo_store
    )


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
    return RoomItemsOut(items=[room_item(piece) for piece in sorted(row.muebles, key=lambda piece: piece.id)])


def _placed(result):
    return PlacedItemResponse(item=room_item(result.piece), wall=result.wall, adjusted=result.adjusted)


async def _room_objects_call(operation):
    """Ejecuta una operación de app.services.room_objects y traduce RoomObjectError a su respuesta HTTP."""
    try:
        return await operation
    except RoomObjectError as exc:
        raise HTTPException(exc.status_code, exc.message) from None


@router.post("/{room_id}/items", response_model=PlacedItemResponse, status_code=status.HTTP_201_CREATED)
async def add_room_item(
    body: ItemCreate,
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Añade un objeto a la sala; el servidor calcula su posición (CONTRATO-FASE0.md).

    De pared: pegado a la pared pedida de una sala rectangular, centrado. De suelo: primer hueco libre.
    Sin IA ni cuota. Se puede deshacer con POST /rooms/{id}/undo.

    Returns:
        201 con {item, wall, adjusted}.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala ajena o inexistente, 409 sin sitio, más alto que la
            sala o sala con 200 objetos, 422 cuerpo inválido u objeto de pared en sala no rectangular,
            503/500 fallo de base de datos.
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    result = await _room_objects_call(
        place_object(
            session,
            row,
            name=body.name,
            kind=body.kind,
            wall=body.wall,
            width_cm=body.width_cm,
            depth_cm=body.depth_cm,
            height_cm=body.height_cm,
            elevation_cm=body.elevation_cm,
            undo_store=undo_store,
        )
    )
    return _placed(result)


@router.patch("/{room_id}/items/{item_id}", response_model=PlacedItemResponse)
async def update_room_item(
    body: ItemUpdate,
    room_id: int = RoomId,
    item_id: int = ItemId,
    session: AsyncSession = Depends(get_session),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Renombra, cambia medidas o mueve un objeto; el servidor revalida y devuelve la posición final.

    Si la posición pedida choca, pasa al hueco libre más cercano (adjusted=true). Se puede deshacer con
    POST /rooms/{id}/undo.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala ajena o inexistente u objeto que no es de esa sala,
            409 no cabe, 422 cuerpo inválido o pared en sala no rectangular, 503/500 fallo de base de datos.
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    result = await _room_objects_call(
        update_object(
            session,
            row,
            item_id,
            name=body.name,
            width_cm=body.width_cm,
            depth_cm=body.depth_cm,
            height_cm=body.height_cm,
            position=body.position,
            undo_store=undo_store,
        )
    )
    return _placed(result)


@router.delete("/{room_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room_item(
    room_id: int = RoomId,
    item_id: int = ItemId,
    session: AsyncSession = Depends(get_session),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Borra un objeto de la sala (204 sin cuerpo). Se puede deshacer con POST /rooms/{id}/undo.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala u objeto ajenos o inexistentes, 503/500 fallo de
            base de datos.
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    await _room_objects_call(delete_object(session, row, item_id, undo_store=undo_store))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _catalog_for_layout(session, user_id, item_ids):
    """Catalog items of the account to place: the requested ids in that order, or the whole catalog.

    The whole catalog is read with LIMIT MAX_LAYOUT_ITEMS + 1, so a huge catalog is refused without
    loading it: itemIds is capped at the same MAX_LAYOUT_ITEMS by the schema.

    Raises:
        HTTPException: 404 a requested id is not in the account's catalog, 422 nothing to place or
            more than MAX_LAYOUT_ITEMS pieces without itemIds, 503 database failure.
    """
    query = select(MuebleCatalogo).where(MuebleCatalogo.user_id == user_id).order_by(MuebleCatalogo.id)
    if item_ids is not None:
        item_ids = list(dict.fromkeys(item_ids))  # repeated ids are placed once, first order kept
        query = query.where(MuebleCatalogo.id.in_(item_ids))
    else:
        query = query.limit(MAX_LAYOUT_ITEMS + 1)
    try:
        catalog = (await session.scalars(query)).all()
    except DATABASE_ERRORS as exc:
        raise database_error(exc) from exc
    if item_ids is None and len(catalog) > MAX_LAYOUT_ITEMS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Tu catálogo tiene demasiados muebles para distribuirlos todos; envía itemIds, "
            f"máximo {MAX_LAYOUT_ITEMS}.",
        )
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
    undo_store=Depends(get_room_undo_store),
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
    # The gap search is CPU-bound (bounded by the budget in app.geometry.layout), so it runs in a worker
    # thread to keep the server responsive.
    result = await asyncio.to_thread(
        run_layout, room_geometry(row), pieces, body.constraints.min_distance_between_items_cm
    )

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
        # Copia para Deshacer, antes de reemplazar los muebles.
        before = snapshot_room(row, "layout")
        # delete-orphan cascade: the previous furniture of the room is deleted in the same commit.
        row.muebles = placed
        await commit_with_undo(session, row, undo_store, before)

    return LayoutResponse.model_validate(asdict(result))


# --- Deshacer (CONTRATO-FASE2.md, sección 1) ---------------------------------------------------------


@router.post("/{room_id}/undo", response_model=RoomUndoResponse)
@router.post("/{room_id}/photo-undo", response_model=RoomUndoResponse, deprecated=True)
async def undo_last_change(
    room_id: int = RoomId,
    session: AsyncSession = Depends(get_session),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Deshace el último cambio de la sala, sea cual sea: foto, chat, objetos, layout o vértices.

    La copia sale de la pila de la sala (LinkedStack, hasta 50) en memoria: se pierde si el servidor se
    reinicia. No llama a la IA ni gasta cuota. photo-undo es un alias obsoleto (misma pila).

    Returns:
        200 con {room, items, remaining, undone}; remaining = cambios que aún se pueden deshacer.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala inexistente o ajena (antes de mirar la pila),
            409 no hay nada que deshacer, 503/500 fallo de base de datos (la copia vuelve a la pila).
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    # Sin await entre leer la pila y quitar la copia.
    snapshot = undo_store.pop(current_user, room_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_TO_UNDO)
    restore_room(row, snapshot)
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        undo_store.push(current_user, room_id, snapshot)  # no se pierde la copia
        raise database_error(exc) from exc

    return RoomUndoResponse(
        room=room_detail(row),
        items=[room_item(piece) for piece in row.muebles],
        remaining=undo_store.depth(current_user, room_id),
        undone=snapshot.change,
    )
