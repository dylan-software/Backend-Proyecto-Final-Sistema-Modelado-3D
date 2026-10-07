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

    def insert(self, point):
        """Insert a point, subdividing full leaves. Returns False if it lies outside the cell."""
        point = np.asarray(point, dtype=float)
        if not self.contains(point):
            return False
        if self.is_leaf():
            if len(self.points) < self.capacity or self.depth >= self.max_depth:
                self.points.append(point)
                return True
            self.subdivide()
        return self._child_for(point).insert(point)

    @classmethod
    def from_points(cls, points, capacity=8, max_depth=8):
        """Build a tree whose root cube encloses every point."""
        points = np.asarray(points, dtype=float).reshape(-1, 3)
        if len(points) == 0:
            return cls(np.zeros(3), 1.0, capacity=capacity, max_depth=max_depth)
        lo, hi = points.min(axis=0), points.max(axis=0)
        half_size = max(float((hi - lo).max()) / 2, 1e-6) * 1.001
        root = cls((lo + hi) / 2, half_size, capacity=capacity, max_depth=max_depth)
        for point in points:
            root.insert(point)
        return root

    def query_box(self, box_min, box_max):
        """Return the stored points inside the axis-aligned box [box_min, box_max]."""
        box_min = np.asarray(box_min, dtype=float)
        box_max = np.asarray(box_max, dtype=float)
        if np.any(box_max < self.center - self.half_size) or np.any(box_min > self.center + self.half_size):
            return []
        if self.is_leaf():
            return [p for p in self.points if np.all(p >= box_min) and np.all(p <= box_max)]
        found = []
        for child in self.children:
            found.extend(child.query_box(box_min, box_max))
        return found
