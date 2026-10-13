from collections.abc import Sequence
from typing import Generic

import numpy as np
from numpy.typing import ArrayLike

from app.utils.typing import SpatialEntry, T


class OctreeNode(Generic[T]):
    """Axis-aligned cubic cell of an octree that stores 3D points with optional payloads of type T."""

    def __init__(
        self,
        center: ArrayLike,
        half_size: float,
        depth: int = 0,
        capacity: int = 8,
        max_depth: int = 8,
    ) -> None:
        self.center = np.asarray(center, dtype=float)
        self.half_size = float(half_size)
        self.depth = depth
        self.capacity = capacity
        self.max_depth = max_depth
        self.entries: list[SpatialEntry[T]] = []
        self.children: list["OctreeNode[T]"] | None = None

    def is_leaf(self) -> bool:
        return self.children is None

    def contains(self, point: ArrayLike) -> bool:
        offset = np.abs(np.asarray(point, dtype=float) - self.center)
        return bool(np.all(offset <= self.half_size))

    def subdivide(self) -> None:
        """Split this cell into 8 child octants and hand its entries down to them."""
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
        entries, self.entries = self.entries, []
        for entry in entries:
            self._child_for(entry.point).entries.append(entry)

    def _child_for(self, point: ArrayLike) -> "OctreeNode[T]":
        # Child order matches the loops in subdivide: x is the slowest axis, z the fastest.
        index = 0
        if point[0] >= self.center[0]:
            index += 4
        if point[1] >= self.center[1]:
            index += 2
        if point[2] >= self.center[2]:
            index += 1
        return self.children[index]

    def insert(self, point: ArrayLike, payload: T | None = None) -> bool:
        """Insert a point, subdividing full leaves. Returns False if it lies outside the cell."""
        point = np.asarray(point, dtype=float)
        if not self.contains(point):
            return False
        if self.is_leaf():
            if len(self.entries) < self.capacity or self.depth >= self.max_depth:
                self.entries.append(SpatialEntry(point, payload))
                return True
            self.subdivide()
        return self._child_for(point).insert(point, payload)

    @classmethod
    def from_points(
        cls,
        points: ArrayLike,
        payloads: Sequence[T] | None = None,
        capacity: int = 8,
        max_depth: int = 8,
    ) -> "OctreeNode[T]":
        """Build a tree whose root cube encloses every point."""
        points = np.asarray(points, dtype=float).reshape(-1, 3)
        if payloads is not None and len(payloads) != len(points):
            raise ValueError("payloads must have one item per point")
        if len(points) == 0:
            return cls(np.zeros(3), 1.0, capacity=capacity, max_depth=max_depth)
        lo, hi = points.min(axis=0), points.max(axis=0)
        half_size = max(float((hi - lo).max()) / 2, 1e-6) * 1.001
        root = cls((lo + hi) / 2, half_size, capacity=capacity, max_depth=max_depth)
        for i, point in enumerate(points):
            root.insert(point, None if payloads is None else payloads[i])
        return root

    def query_box(self, box_min: ArrayLike, box_max: ArrayLike) -> list[SpatialEntry[T]]:
        """Return the stored entries whose point lies inside the axis-aligned box [box_min, box_max]."""
        box_min = np.asarray(box_min, dtype=float)
        box_max = np.asarray(box_max, dtype=float)
        if np.any(box_max < self.center - self.half_size) or np.any(box_min > self.center + self.half_size):
            return []
        if self.is_leaf():
            return [e for e in self.entries if np.all(e.point >= box_min) and np.all(e.point <= box_max)]
        found = []
        for child in self.children:
            found.extend(child.query_box(box_min, box_max))
        return found
