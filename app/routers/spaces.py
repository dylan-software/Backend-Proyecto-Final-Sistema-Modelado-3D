import asyncio

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_session
from app.geometry.collision import check_bounding_box_collision
from app.geometry.octree import OctreeNode
from app.geometry.ransac_filter import clean_point_cloud
from app.models.space import Habitacion, MuebleSugerido
from app.schemas.space import (
    AnalyzeRequest,
    AnalyzeResponse,
    DiscardedFurniture,
    FurnitureOut,
    FurnitureProposal,
    RoomOut,
)
from app.services.omniroute import OmniRouteError, suggest_layout
from app.utils.helpers import ImageDecodeError, load_image_matrix, summarize_image

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])

# A box must contain at least this many leftover scan points to count as hitting a real object;
# fewer are treated as sensor noise.
MIN_OBSTACLE_POINTS = 5


def get_layout_suggester():
    """FastAPI dependency returning the layout generator; tests override it with a fake."""
    return suggest_layout


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
):
    """Analyze a scanned room and store a validated furniture layout.

    Pipeline: RANSAC detects floor, walls and ceiling (and, in parallel, OpenCV reads the optional
    photo); OmniRoute proposes furniture; each piece is checked against the room bounds, the
    furniture already accepted and the leftover scan points in an octree; the room and the
    accepted pieces are saved in one transaction.

    Returns:
        201 with the stored room, accepted and discarded furniture, surfaces and visual context.

    Raises:
        HTTPException: 422 invalid photo or undeducible dimensions, 502 OmniRoute failure,
            503 database failure.
    """
    # 1. Isolate floor, walls and ceiling from the scan; whatever is left are objects or noise.
    # The photo is decoded while RANSAC runs; both are CPU-bound and run in worker threads.
    try:
        ransac, visual = await asyncio.gather(clean_point_cloud(body.puntos), _visual_context(body.imagen))
    except ImageDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Imagen inválida: {exc}") from exc
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
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No se pudieron deducir las dimensiones de la habitación; envía ancho, alto y profundidad.",
        )

    # Move the leftover points into room coordinates (origin at the corner of the detected surfaces).
    residual = np.asarray(ransac["puntos_residuales"], dtype=float).reshape(-1, 3) - origin
    residual = residual[np.all((residual >= 0) & (residual <= dims), axis=1)]
    scan = OctreeNode.from_points(residual) if len(residual) else None

    # 2. Ask OmniRoute for a design proposal.
    try:
        proposal = await suggester(
            tuple(dims.tolist()),
            body.estilo,
            body.instrucciones,
            ransac["planos"],
            contexto_visual=visual,
        )
    except OmniRouteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OmniRoute: {exc}") from exc

    # 3. Keep only furniture that fits in the room without hitting walls, objects or other furniture.
    accepted, discarded, placed = [], [], []
    for raw in proposal["muebles"]:
        try:
            item = FurnitureProposal.model_validate(raw)
        except ValidationError:
            name = raw.get("nombre") if isinstance(raw, dict) else None
            discarded.append(DiscardedFurniture(nombre=str(name or "desconocido"), motivo="datos incompletos"))
            continue
        box_min, box_max = _furniture_box(item)
        if check_bounding_box_collision(
            box_min, box_max, dims, obstacles=placed, scan=scan, min_scan_points=MIN_OBSTACLE_POINTS
        ):
            discarded.append(DiscardedFurniture(nombre=item.nombre, motivo="colisión"))
            continue
        placed.append((box_min, box_max))
        accepted.append(item)

    # 4. Store the room together with its validated layout.
    room = Habitacion(
        nombre=body.nombre,
        ancho=float(dims[0]),
        alto=float(dims[1]),
        profundidad=float(dims[2]),
    )
    room.muebles = [MuebleSugerido(**item.model_dump()) for item in accepted]
    try:
        session.add(room)
        await session.commit()
    except (SQLAlchemyError, OSError) as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo guardar en la base de datos.") from exc

    return AnalyzeResponse(
        habitacion=RoomOut(
            id=room.id,
            nombre=room.nombre,
            ancho=room.ancho,
            alto=room.alto,
            profundidad=room.profundidad,
        ),
        descripcion=proposal.get("descripcion", ""),
        muebles=[FurnitureOut(id=row.id, **item.model_dump()) for row, item in zip(room.muebles, accepted)],
        descartados=discarded,
        superficies=ransac["planos"],
        origen=tuple(origin.tolist()),
        ruido=ransac["ruido"],
        contexto_visual=visual,
    )
