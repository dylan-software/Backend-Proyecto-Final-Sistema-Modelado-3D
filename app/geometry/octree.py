import numpy as np


class OctreeNode:
    """Axis-aligned cubic cell of an octree that stores 3D points."""

    def __init__(self, center, half_size, depth=0, capacity=8, max_depth=8):
        self.center = np.asarray(center, dtype=float)
        self.half_size = float(half_size)
        self.depth = depth
        self.capacity = capacity
        self.max_depth = max_depth
        self.points = []
        self.children = None

    def is_leaf(self):
        return self.children is None

    def contains(self, point):
        offset = np.abs(np.asarray(point, dtype=float) - self.center)
        return bool(np.all(offset <= self.half_size))
