from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike, NDArray

T = TypeVar("T")

# A 3-component float vector (x, y, z) in meters.
Vector3 = NDArray[np.float64]
