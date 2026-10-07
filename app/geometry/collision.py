import numpy as np
from scipy.spatial import cKDTree

from app.geometry.octree import OctreeNode


def check_bounding_box_collision(box_min, box_max, room_dims, obstacles=(), scan=None):
    """Return True if the box [box_min, box_max] collides with the room or its contents.

    The room spans [0, ancho] x [0, alto] x [0, profundidad] with y pointing up.
    obstacles: iterable of (min, max) corner pairs of other furniture.
    scan: optional OctreeNode with the scanned point cloud of the room.
    """
    raise NotImplementedError
