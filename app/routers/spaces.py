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
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Análisis en construcción")
