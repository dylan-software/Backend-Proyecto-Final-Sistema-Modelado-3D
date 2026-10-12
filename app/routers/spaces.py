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

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])

def get_layout_suggester():
    return suggest_layout


@router.post("/analyze")
async def analyze_space(
    body: AnalyzeRequest,
    session: AsyncSession = Depends(get_session),
    suggester=Depends(get_layout_suggester),
):
    # 1. Isolate floor, walls and ceiling from the scan; whatever is left are objects or noise.
    ransac = await clean_point_cloud(body.puntos)
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

    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Análisis en construcción")
