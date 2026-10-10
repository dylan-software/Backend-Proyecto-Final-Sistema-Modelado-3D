from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike, NDArray

T = TypeVar("T")

# A 3-component float vector (x, y, z) in meters.
Vector3 = NDArray[np.float64]


@runtime_checkable
class SpatialIndex(Protocol):
    """Contract the collision validator relies on: any structure that stores points and returns
    the ones inside an axis-aligned box (octree, k-d tree, uniform grid...)."""

    def insert(self, point: ArrayLike) -> bool: ...

    def query_box(self, box_min: ArrayLike, box_max: ArrayLike) -> Sequence[object]: ...


@dataclass(frozen=True, slots=True)
class SpatialEntry(Generic[T]):
    """A point stored in a spatial index together with an optional payload, e.g. a furniture id."""

    point: Vector3
    payload: T | None = None
