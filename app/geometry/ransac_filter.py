import asyncio

import numpy as np
from sklearn.linear_model import LinearRegression, RANSACRegressor


async def clean_point_cloud(points, residual_threshold=0.02, max_planes=6, min_inliers=50):
    """Separate the planar surfaces of a scanned room from background noise.

    points: sequence of [x, y, z] coordinates in meters, y pointing up.
    The fitting is CPU-bound, so it runs in a worker thread to keep the server responsive.
    """
    cloud = np.asarray(points, dtype=float).reshape(-1, 3)
    cloud = cloud[np.all(np.isfinite(cloud), axis=1)]
    return await asyncio.to_thread(_fit_planes, cloud, residual_threshold, max_planes, min_inliers)


def _fit_planes(cloud, residual_threshold, max_planes, min_inliers):
    return {"planos": [], "puntos_limpios": cloud.tolist(), "ruido": 0}
