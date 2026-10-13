import numpy as np
from scipy.spatial import cKDTree

from app.utils.typing import SpatialIndex

# Floating-point slack in meters: smaller overlaps or overshoots are rounding error, not collisions.
# Positions are sums such as posicion_x + ancho / 2, so a piece placed flush against a wall can end
# up a few units in the last place past it (0.1 + 0.2 == 0.30000000000000004). 1e-9 m is far
# below any physical tolerance and far above double-precision error at room scale (~1e-15 m).
EPSILON = 1e-9


def boxes_overlap(a_min, a_max, b_min, b_max):
    """Two axis-aligned boxes intersect when their intervals overlap on all three axes.

    On one axis, [a_min, a_max] and [b_min, b_max] overlap iff a_min < b_max and b_min < a_max.
    The comparisons are strict and shifted by EPSILON, so boxes that only share a face (a chair
    pushed against a table) are not reported as colliding.
    """
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

    # 1. Containment: 0 <= box_min and box_max <= room_dims on every axis, up to EPSILON.
    if np.any(box_min < -EPSILON) or np.any(box_max > room_dims + EPSILON):
        return True

    obstacles = [(np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)) for lo, hi in obstacles]
    if obstacles:
        # 2. Other furniture. Every box fits inside the sphere around its center with radius equal to
        # its half-diagonal |max - min| / 2, and two boxes can only intersect if those spheres do,
        # i.e. if |c_box - c_obstacle| <= r_box + r_obstacle. Querying a KD-tree of obstacle centers
        # with r_box + max(r_obstacle) therefore returns a superset of the real collisions, and the
        # exact interval test only runs on those candidates.
        centers = np.array([(lo + hi) / 2 for lo, hi in obstacles])
        radii = np.array([np.linalg.norm(hi - lo) / 2 for lo, hi in obstacles])
        reach = np.linalg.norm(box_max - box_min) / 2 + radii.max()
        candidates = cKDTree(centers).query_ball_point((box_min + box_max) / 2, reach)
        for i in candidates:
            if boxes_overlap(box_min, box_max, *obstacles[i]):
                return True

    # 3. Scanned objects. A lone point inside the box is usually sensor noise, so callers can require
    # several hits before treating it as a real object (the analyze endpoint uses 5).
    if scan is not None and len(scan.query_box(box_min, box_max)) >= min_scan_points:
        return True

    return False
