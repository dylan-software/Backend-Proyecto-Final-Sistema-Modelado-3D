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

    def subdivide(self):
        """Split this cell into 8 child octants and hand its points down to them."""
        quarter = self.half_size / 2
        self.children = []
        for dx in (-1, 1):
            for dy in (-1, 1):
                for dz in (-1, 1):
                    offset = np.array([dx, dy, dz], dtype=float) * quarter
                    self.children.append(
                        OctreeNode(
                            self.center + offset,
                            quarter,
                            self.depth + 1,
                            self.capacity,
                            self.max_depth,
                        )
                    )
        points, self.points = self.points, []
        for point in points:
            self._child_for(point).points.append(point)

    def _child_for(self, point):
        # Child order matches the loops in subdivide: x is the slowest axis, z the fastest.
        index = 0
        if point[0] >= self.center[0]:
            index += 4
        if point[1] >= self.center[1]:
            index += 2
        if point[2] >= self.center[2]:
            index += 1
        return self.children[index]
