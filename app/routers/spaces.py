import asyncio
import logging
import os

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import func, make_url, select
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database.connection import get_session
from app.geometry.collision import check_bounding_box_collision
from app.geometry.layout import footprint_corner
from app.geometry.octree import OctreeNode
from app.geometry.ransac_filter import clean_point_cloud
from app.geometry.room import Room
from app.models.space import Categoria, Habitacion, MuebleCatalogo, MuebleSugerido
from app.routers.auth import ensure_same_user, get_current_user
from app.routers.errors import DATABASE_ERRORS, database_unavailable
from app.routers.rooms import load_owned_room, room_detail, room_geometry
from app.schemas.room import AnalyzePhotoResponse, ColorShareOut, DiscardedItem, RoomItem, VisualContextOut
from app.schemas.space import (
    AnalyzeRequest,
    AnalyzeResponse,
    CatalogFurnitureIn,
    CatalogFurnitureOut,
    CatalogItemOut,
    DiscardedFurniture,
    FurnitureOut,
    FurnitureProposal,
    RoomOut,
    SpaceOut,
    UserSpacesOut,
)
from app.services.omniroute import OmniRouteError, suggest_layout
from app.services.semantic_filter import validate_furniture_coherence
from app.utils.helpers import ImageDecodeError, decode_image, load_image_matrix, summarize_image

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])

# Only included by app.main when ENABLE_DEBUG_ROUTES=true.
debug_router = APIRouter(prefix="/api/v1/spaces", tags=["debug"])

logger = logging.getLogger(__name__)

# A box must contain at least this many leftover scan points to count as hitting a real object;
# fewer are treated as sensor noise.
MIN_OBSTACLE_POINTS = 5

# Category the web interface sends when the user types a category of their own.
OTHERS_CATEGORY = "others"

# Room photos accepted by POST /rooms/{id}/analyze-photo, with the first bytes every file of that
# format starts with. The declared type alone is just a header the client chose.
PHOTO_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
# Whole multipart body: the photo plus boundaries, part headers, estilo and instrucciones. app.main
# enforces it before the body is parsed; the endpoint then checks the photo itself against 5 MB.
MAX_PHOTO_REQUEST_BYTES = MAX_PHOTO_BYTES + 64 * 1024
PHOTO_UPLOAD_PATH = r"^/api/v1/spaces/rooms/[^/]+/analyze-photo/?$"


def get_layout_suggester():
    """FastAPI dependency returning the layout generator; tests override it with a fake."""
    return suggest_layout


def get_coherence_validator():
    """FastAPI dependency returning the furniture name validator; tests override it with a fake."""
    return validate_furniture_coherence


def _furniture_box(item):
    """Axis-aligned bounding box of a proposed piece of furniture.

    posicion_x/z is the center of the footprint and posicion_y the height of its base.

    Returns:
        (box_min, box_max): opposite corners in room coordinates, as NumPy arrays.
    """
    half_w, half_d = item.ancho / 2, item.profundidad / 2
    box_min = np.array([item.posicion_x - half_w, item.posicion_y, item.posicion_z - half_d])
    box_max = np.array([item.posicion_x + half_w, item.posicion_y + item.alto, item.posicion_z + half_d])
    return box_min, box_max


async def _request_proposal(suggester, dims, estilo, instrucciones, superficies, **extra):
    """Furniture proposal from OmniRoute for a room of dims (ancho, alto, profundidad) in meters.

    extra is passed on to the suggester as keyword arguments (contexto_visual, planta).

    Raises:
        HTTPException: 502 OmniRoute is not configured, failed or returned an unusable answer.
    """
    try:
        return await suggester(tuple(dims.tolist()), estilo, instrucciones, superficies, **extra)
    except OmniRouteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OmniRoute: {exc}") from exc


def _filter_proposal(raw_items, dims, scan=None, floor=None):
    """Split proposed furniture into the pieces that can be placed and the discarded ones.

    A piece is kept when its data is complete, its box fits in [0, dims] without hitting the pieces
    kept before it or the scanned objects, and, when floor (an app.geometry.room.Room in the same
    coordinates) is given, its footprint lies inside that polygon.

    Returns:
        (accepted, discarded): FurnitureProposal and DiscardedFurniture lists.
    """
    accepted, discarded, placed = [], [], []
    for raw in raw_items:
        try:
            item = FurnitureProposal.model_validate(raw)
        except ValidationError:
            name = raw.get("nombre") if isinstance(raw, dict) else None
            discarded.append(
                DiscardedFurniture(nombre=str(name or "desconocido"), motivo="datos incompletos")
            )
            continue
        box_min, box_max = _furniture_box(item)
        if check_bounding_box_collision(
            box_min, box_max, dims, obstacles=placed, scan=scan, min_scan_points=MIN_OBSTACLE_POINTS
        ):
            discarded.append(DiscardedFurniture(nombre=item.nombre, motivo="colisión"))
            continue
        if floor is not None and not floor.contains_rectangle(
            box_min[0], box_min[2], item.ancho, item.profundidad
        ):
            discarded.append(DiscardedFurniture(nombre=item.nombre, motivo="fuera de la habitación"))
            continue
        placed.append((box_min, box_max))
        accepted.append(item)
    return accepted, discarded


def _category_name(body):
    """Name of the category a catalog item belongs to.

    "Others" means the user typed a new category in custom_category; any other value is used as is.
    Extra whitespace is collapsed so "  Mesas   auxiliares " and "Mesas auxiliares" are the same.

    Raises:
        HTTPException: 422 "Others" was selected without a custom_category.
    """
    selected = " ".join(body.categoria.split())
    if selected.casefold() != OTHERS_CATEGORY:
        return selected
    custom = " ".join((body.custom_category or "").split())
    if not custom:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Con la categoría 'Others' debes indicar el nombre en custom_category.",
        )
    return custom


async def _visual_context(encoded):
    """Brightness and palette of the optional room photo.

    Args:
        encoded: base64 image or data URL, or None.

    Returns:
        {"brillo": float, "colores": [...]}, or None when no photo was sent.

    Raises:
        ImageDecodeError: the data is not valid base64 or not a readable image.
    """
    if not encoded:
        return None
    matrix = await load_image_matrix(encoded)
    return await asyncio.to_thread(summarize_image, matrix)


@router.post("/analyze", response_model=AnalyzeResponse, status_code=status.HTTP_201_CREATED)
async def analyze_space(
    body: AnalyzeRequest,
    session: AsyncSession = Depends(get_session),
    suggester=Depends(get_layout_suggester),
    current_user: str = Depends(get_current_user),
):
    """Analyze a scanned room and store a validated furniture layout.

    Pipeline: RANSAC detects floor, walls and ceiling (and, in parallel, OpenCV reads the optional
    photo); OmniRoute proposes furniture; each piece is checked against the room bounds, the
    furniture already accepted and the leftover scan points in an octree; the room and the
    accepted pieces are saved in one transaction.

    Returns:
        201 with the stored room, accepted and discarded furniture, surfaces and visual context.

    Raises:
        HTTPException: 401 no valid session, 403 user_id of another account, 422 invalid photo or
            undeducible dimensions, 502 OmniRoute failure, 503 database failure.
    """
    owner = ensure_same_user(body.user_id, current_user)
    # 1. Isolate floor, walls and ceiling from the scan; whatever is left are objects or noise.
    # The photo is decoded while RANSAC runs; both are CPU-bound and run in worker threads.
    try:
        ransac, visual = await asyncio.gather(clean_point_cloud(body.puntos), _visual_context(body.imagen))
    except ImageDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Imagen inválida: {exc}") from exc
    surfaces = np.asarray(ransac["puntos_limpios"], dtype=float).reshape(-1, 3)
    if len(surfaces) == 0:
        surfaces = np.asarray(body.puntos, dtype=float)
    origin = surfaces.min(axis=0)
    extent = surfaces.max(axis=0) - origin
    dims = np.array(
        [body.ancho or extent[0], body.alto or extent[1], body.profundidad or extent[2]],
        dtype=float,
    )
    if np.any(dims <= 0):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "No se pudieron deducir las dimensiones de la habitación; envía ancho, alto y profundidad.",
        )

    # Move the leftover points into room coordinates (origin at the corner of the detected surfaces).
    residual = np.asarray(ransac["puntos_residuales"], dtype=float).reshape(-1, 3) - origin
    residual = residual[np.all((residual >= 0) & (residual <= dims), axis=1)]
    scan = OctreeNode.from_points(residual) if len(residual) else None

    # 2. Ask OmniRoute for a design proposal.
    proposal = await _request_proposal(
        suggester, dims, body.estilo, body.instrucciones, ransac["planos"], contexto_visual=visual
    )

    # 3. Keep only furniture that fits in the room without hitting walls, objects or other furniture.
    accepted, discarded = _filter_proposal(proposal["muebles"], dims, scan=scan)

    # 4. Store the room together with its validated layout.
    room = Habitacion(
        user_id=owner,
        nombre=body.nombre,
        ancho=float(dims[0]),
        alto=float(dims[1]),
        profundidad=float(dims[2]),
    )
    room.muebles = [MuebleSugerido(**item.model_dump()) for item in accepted]
    try:
        session.add(room)
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_unavailable() from exc

    return AnalyzeResponse(
        habitacion=RoomOut(
            id=room.id,
            nombre=room.nombre,
            ancho=room.ancho,
            alto=room.alto,
            profundidad=room.profundidad,
        ),
        descripcion=proposal.get("descripcion", ""),
        muebles=[
            FurnitureOut(id=row.id, **item.model_dump())
            for row, item in zip(room.muebles, accepted, strict=True)
        ],
        descartados=discarded,
        superficies=ransac["planos"],
        origen=tuple(origin.tolist()),
        ruido=ransac["ruido"],
        contexto_visual=visual,
    )


@router.post("/rooms/{room_id}/analyze-photo", response_model=AnalyzePhotoResponse)
async def analyze_room_photo(
    room_id: int = Path(gt=0),
    file: UploadFile = File(),
    estilo: str = Form("moderno", min_length=1, max_length=60),
    instrucciones: str | None = Form(None, max_length=1000),
    session: AsyncSession = Depends(get_session),
    suggester=Depends(get_layout_suggester),
    current_user: str = Depends(get_current_user),
):
    """Furnish a saved room from a photo of it (multipart: file, and optional estilo and instrucciones).

    The room's shape comes from the database, never from the client; the photo only feeds the colors
    and brightness that guide OmniRoute, as in /analyze. Proposed pieces that do not fit inside the
    room's real polygon, or that overlap each other, are discarded. The accepted ones replace the
    furniture placed in the room before.

    Returns:
        200 with the room, the AI description, the placed items (minimum corner of each footprint,
        meters), the discarded pieces and the photo's visual context.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 413 photo larger than
            5 MB, 415 not a JPEG or PNG (declared type or first bytes), 422 the photo cannot be
            decoded, 502 OmniRoute failure, 503 database failure. A body far above 5 MB is refused
            with 413 by BodySizeLimitMiddleware before it is parsed.
    """
    row = await load_owned_room(session, room_id, current_user, selectinload(Habitacion.muebles))
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in PHOTO_SIGNATURES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Formato no soportado: envía una foto JPEG o PNG."
        )
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "La foto supera el máximo de 5 MB.")
    if not data.startswith(PHOTO_SIGNATURES[content_type]):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"El archivo no es una foto {content_type.split('/')[1].upper()} aunque se declaró así.",
        )
    try:
        matrix = await asyncio.to_thread(decode_image, data)
    except ImageDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Imagen inválida: {exc}") from exc
    visual = await asyncio.to_thread(summarize_image, matrix)

    # The AI and the collision check work in [0, ancho] x [0, profundidad]; the stored polygon may
    # start anywhere, so it is shifted to its bounding box's corner and the results are shifted back.
    geometry = room_geometry(row)
    box = geometry.bounding_box()
    floor = Room([(v.x - box.min_x, v.y - box.min_y) for v in geometry.vertices], geometry.height_m)
    dims = np.array([box.max_x - box.min_x, geometry.height_m, box.max_y - box.min_y], dtype=float)
    extra = {"contexto_visual": visual}
    if row.vertices is not None:
        extra["planta"] = floor.vertices_json()
    proposal = await _request_proposal(suggester, dims, estilo, instrucciones, [], **extra)
    accepted, discarded = _filter_proposal(proposal["muebles"], dims, floor=floor)

    pieces = []
    for item in accepted:
        values = item.model_dump()
        values["posicion_x"] += box.min_x
        values["posicion_z"] += box.min_y
        pieces.append(MuebleSugerido(**values))
    # delete-orphan cascade: the previous furniture of the room is deleted in the same commit.
    row.muebles = pieces
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_unavailable() from exc

    items = []
    for piece in pieces:
        x, y = footprint_corner(piece.posicion_x, piece.posicion_z, piece.ancho, piece.profundidad)
        items.append(RoomItem(id=piece.id, name=piece.nombre, x=x, y=y, rotation=0))
    return AnalyzePhotoResponse(
        room=room_detail(row),
        description=proposal.get("descripcion", ""),
        items=items,
        discarded=[DiscardedItem(name=d.nombre, reason=d.motivo) for d in discarded],
        visual_context=VisualContextOut(
            brightness=visual["brillo"],
            colors=[ColorShareOut(hex=c["hex"], share=c["proporcion"]) for c in visual["colores"]],
        ),
    )


async def _get_or_create_category(session, name):
    """Find a category by name (case-insensitive) or add a new one to the session.

    Returns:
        (categoria, created): the row and whether it was created now.
    """
    query = select(Categoria).where(func.lower(Categoria.nombre) == name.lower())
    categoria = await session.scalar(query)
    if categoria is not None:
        return categoria, False
    try:
        # Savepoint: if another request creates the same category first, only this insert is undone.
        async with session.begin_nested():
            categoria = Categoria(nombre=name)
            session.add(categoria)
        return categoria, True
    except IntegrityError:
        return await session.scalar(query), False


@router.post("/catalog", response_model=CatalogFurnitureOut, status_code=status.HTTP_201_CREATED)
async def add_catalog_furniture(
    body: CatalogFurnitureIn,
    session: AsyncSession = Depends(get_session),
    validator=Depends(get_coherence_validator),
    current_user: str = Depends(get_current_user),
):
    """Add a piece of furniture to the catalog, creating its category when it is new.

    With categoria "Others" the category is the one typed in custom_category; it is created the first
    time and reused afterwards. Before anything is stored, OmniRoute checks that the name is a
    plausible piece of furniture or room accessory.

    Returns:
        201 with the stored item, its category and whether that category was just created.

    Raises:
        HTTPException: 401 no valid session, 403 user_id of another account, 409 the account's
            catalog is full (MAX_CATALOG_ITEMS_PER_USER), 422 "Others" without custom_category or
            incoherent name, 502 OmniRoute failure, 503 database failure.
    """
    owner = ensure_same_user(body.user_id, current_user)
    category_name = _category_name(body)
    # Checked before OmniRoute, so a full catalog never costs a model call. Two requests at the same
    # time may both pass and leave the catalog one or two pieces over the limit, which is harmless.
    limit = get_settings().max_catalog_items_per_user
    try:
        stored = await session.scalar(
            select(func.count()).select_from(MuebleCatalogo).where(MuebleCatalogo.user_id == owner)
        )
    except DATABASE_ERRORS as exc:
        raise database_unavailable() from exc
    if stored >= limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Tu catálogo ya tiene el máximo de {limit} muebles; borra alguno para añadir otro.",
        )
    try:
        coherent = await validator(body.nombre)
    except OmniRouteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OmniRoute: {exc}") from exc
    if not coherent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"'{body.nombre}' no es un mueble ni un accesorio válido para una habitación.",
        )
    try:
        categoria, created = await _get_or_create_category(session, category_name)
        item = MuebleCatalogo(
            user_id=owner,
            categoria_id=categoria.id,
            nombre=body.nombre,
            ancho=body.ancho,
            alto=body.alto,
            profundidad=body.profundidad,
        )
        session.add(item)
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_unavailable() from exc

    return CatalogFurnitureOut(
        id=item.id,
        nombre=item.nombre,
        categoria_id=categoria.id,
        categoria=categoria.nombre,
        categoria_nueva=created,
        ancho=item.ancho,
        alto=item.alto,
        profundidad=item.profundidad,
    )


@router.delete("/furniture/{furniture_id}")
async def delete_catalog_furniture(
    furniture_id: int = Path(gt=0),
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Remove one piece of furniture from the session's catalog. Its category is kept for other items.

    Returns:
        200 with {"success": true, "message": ...}.

    Raises:
        HTTPException: 401 no valid session, 404 the session's account has no catalog item with
            that id (items of other accounts are reported the same way), 503 database failure.
    """
    query = select(MuebleCatalogo).where(
        MuebleCatalogo.id == furniture_id, MuebleCatalogo.user_id == current_user
    )
    try:
        furniture = await session.scalar(query)
    except DATABASE_ERRORS as exc:
        raise database_unavailable() from exc
    if furniture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe un mueble con id {furniture_id}.")
    try:
        await session.delete(furniture)
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_unavailable() from exc
    return {"success": True, "message": "Furniture item successfully removed"}


@router.get("", response_model=UserSpacesOut)
async def list_user_spaces(
    user_id: str | None = Query(None, min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Rooms (with their furniture) and catalog items of the session's account.

    The account comes from the token; user_id is optional and only accepted if it matches it.

    Returns:
        200 with the account's rooms and catalog, both possibly empty.

    Raises:
        HTTPException: 401 no valid session, 403 user_id of another account, 503 database failure.
    """
    user_id = ensure_same_user(user_id, current_user)
    rooms_query = (
        select(Habitacion)
        .where(Habitacion.user_id == user_id)
        .options(selectinload(Habitacion.muebles))
        .order_by(Habitacion.id)
    )
    catalog_query = (
        select(MuebleCatalogo)
        .where(MuebleCatalogo.user_id == user_id)
        .options(selectinload(MuebleCatalogo.categoria))
        .order_by(MuebleCatalogo.id)
    )
    try:
        rooms = (await session.scalars(rooms_query)).all()
        catalog = (await session.scalars(catalog_query)).all()
    except DATABASE_ERRORS as exc:
        raise database_unavailable() from exc
    return UserSpacesOut(
        user_id=user_id,
        habitaciones=[SpaceOut.model_validate(room, from_attributes=True) for room in rooms],
        catalogo=[
            CatalogItemOut(
                id=item.id,
                nombre=item.nombre,
                categoria_id=item.categoria_id,
                categoria=item.categoria.nombre,
                ancho=item.ancho,
                alto=item.alto,
                profundidad=item.profundidad,
            )
            for item in catalog
        ],
    )


@debug_router.get("/debug-vars")
async def debug_environment():
    """Whether the cloud environment variables are being read, without revealing any of their values.

    Each check is a boolean; it is written to the server log (visible in the Render dashboard) and
    returned. Connection strings, passwords and keys never leave the process.
    """
    settings = get_settings()
    try:
        host = make_url(settings.database_url).host or ""
    except ArgumentError:
        host = ""
    checks = {
        "database_url_env": "DATABASE_URL" in os.environ,
        "database_url_is_default": settings.database_url == Settings.model_fields["database_url"].default,
        "database_host_is_supabase": host.endswith(".supabase.com") or host.endswith(".supabase.co"),
        "omniroute_api_key_set": bool(settings.omniroute_api_key),
        "omniroute_model_set": bool(settings.omniroute_model),
        "jwt_secret_env": "JWT_SECRET" in os.environ,
    }
    logger.info("Environment check: %s", " ".join(f"{name}={value}" for name, value in checks.items()))
    return checks
