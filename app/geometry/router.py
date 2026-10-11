from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.geometry.collision import check_bounding_box_collision
from app.geometry.octree import OctreeNode
from app.geometry.ransac_filter import clean_point_cloud

router = APIRouter(prefix="/geometry", tags=["geometry"])

Point = tuple[float, float, float]


class PointCloudRequest(BaseModel):
    points: list[Point]
    residual_threshold: float = Field(0.02, gt=0)


class Box(BaseModel):
    min: Point
    max: Point


class RoomDims(BaseModel):
    ancho: float = Field(gt=0)
    alto: float = Field(gt=0)
    profundidad: float = Field(gt=0)


class CollisionRequest(BaseModel):
    habitacion: RoomDims
    mueble: Box
    obstaculos: list[Box] = []
    puntos_escaneo: list[Point] = []


@router.post("/point-cloud")
async def process_point_cloud(body: PointCloudRequest):
    """Run only the RANSAC filter: detected planes, clean surface points and noise count."""
    return await clean_point_cloud(body.points, residual_threshold=body.residual_threshold)


@router.post("/collision")
async def check_collision(body: CollisionRequest):
    """Check one furniture box against the room bounds, other boxes and optional scan points."""
    room = body.habitacion
    scan = OctreeNode.from_points(body.puntos_escaneo) if body.puntos_escaneo else None
    collides = check_bounding_box_collision(
        body.mueble.min,
        body.mueble.max,
        (room.ancho, room.alto, room.profundidad),
        obstacles=[(o.min, o.max) for o in body.obstaculos],
        scan=scan,
    )
    return {"colision": collides}
