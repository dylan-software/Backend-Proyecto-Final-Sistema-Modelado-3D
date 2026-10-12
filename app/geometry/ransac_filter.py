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


def _fit_axis_plane(cloud, axis, residual_threshold):
    """Fit coord[axis] = a*u + b*v + c with RANSAC, where u and v are the other two axes."""
    others = [i for i in range(3) if i != axis]
    ransac = RANSACRegressor(
        LinearRegression(),
        min_samples=3,
        residual_threshold=residual_threshold,
        random_state=0,
    )
    ransac.fit(cloud[:, others], cloud[:, axis])
    a, b = ransac.estimator_.coef_
    c = ransac.estimator_.intercept_
    normal = np.zeros(3)
    normal[axis] = 1.0
    normal[others[0]] = -a
    normal[others[1]] = -b
    norm = np.linalg.norm(normal)
    return ransac.inlier_mask_, normal / norm, c / norm


def _classify(normal):
    vertical = abs(normal[1])
    if vertical > 0.9:
        return "horizontal"  # floor or ceiling
    if vertical < 0.1:
        return "pared"
    return "inclinada"


def _fit_planes(cloud, residual_threshold, max_planes, min_inliers):
    remaining = cloud
    planes = []
    kept = []
    while len(planes) < max_planes and len(remaining) >= max(min_inliers, 3):
        # Walls cannot be written as y = f(x, z), so try each axis as the dependent one
        # and keep the plane that explains the most points.
        mask, normal, offset = max(
            (_fit_axis_plane(remaining, axis, residual_threshold) for axis in range(3)),
            key=lambda fit: fit[0].sum(),
        )
        if mask.sum() < min_inliers:
            break
        planes.append(
            {
                "tipo": _classify(normal),
                "normal": normal.tolist(),
                "offset": float(offset),
                "puntos": int(mask.sum()),
            }
        )
        kept.append(remaining[mask])
        remaining = remaining[~mask]

    clean = np.vstack(kept) if kept else np.empty((0, 3))
    return {"planos": planes, "puntos_limpios": clean.tolist(), "ruido": int(len(remaining))}
