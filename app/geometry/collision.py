import numpy as np
from scipy.spatial import cKDTree

from app.utils.typing import SpatialIndex

# Floating-point slack in meters: smaller overlaps or overshoots are rounding error, not collisions.
EPSILON = 1e-9


def boxes_overlap(a_min, a_max, b_min, b_max):
    """Two axis-aligned boxes intersect when their intervals overlap on all three axes."""
    return bool(np.all(a_min < b_max - EPSILON) and np.all(b_min < a_max - EPSILON))


def check_bounding_box_collision(
    box_min, box_max, room_dims, obstacles=(), scan: SpatialIndex | None = None, min_scan_points=1
):
    """Return True if the box [box_min, box_max] collides with the room or its contents.

    The room spans [0, ancho] x [0, alto] x [0, profundidad] with y pointing up.
    obstacles: iterable of (min, max) corner pairs of other furniture.
    scan: optional SpatialIndex (e.g. an OctreeNode) holding the scanned point cloud of the room.
    min_scan_points: how many scanned points inside the box count as a collision.
    """
    box_min = np.asarray(box_min, dtype=float)
    box_max = np.asarray(box_max, dtype=float)
    room_dims = np.asarray(room_dims, dtype=float)

    if np.any(box_min < -EPSILON) or np.any(box_max > room_dims + EPSILON):
        return True

    obstacles = [(np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)) for lo, hi in obstacles]
    if obstacles:
        # Only obstacles whose centers are close enough can touch the box; a KD-tree finds them.
        centers = np.array([(lo + hi) / 2 for lo, hi in obstacles])
        radii = np.array([np.linalg.norm(hi - lo) / 2 for lo, hi in obstacles])
        reach = np.linalg.norm(box_max - box_min) / 2 + radii.max()
        candidates = cKDTree(centers).query_ball_point((box_min + box_max) / 2, reach)
        for i in candidates:
            if boxes_overlap(box_min, box_max, *obstacles[i]):
                return True

    if scan is not None and len(scan.query_box(box_min, box_max)) >= min_scan_points:
        return True

    return False
