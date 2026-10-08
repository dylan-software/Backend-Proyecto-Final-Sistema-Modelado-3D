import type { SegmentationOutput } from "./contracts";

/** 2cm threshold, tolerant to mobile-photogrammetry noise. */
const DISTANCE_THRESHOLD_M = 0.02;
const NUM_ITERATIONS = 200;

type Point = [number, number, number];

function distanceToPlane(p: Point, plane: [number, number, number, number]): number {
  const [a, b, c, d] = plane;
  const [x, y, z] = p;
  const norm = Math.sqrt(a * a + b * b + c * c);
  return Math.abs(a * x + b * y + c * z + d) / (norm || 1);
}

function planeFrom3Points(p1: Point, p2: Point, p3: Point): [number, number, number, number] | null {
  const v1: Point = [p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]];
  const v2: Point = [p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]];
  const a = v1[1] * v2[2] - v1[2] * v2[1];
  const b = v1[2] * v2[0] - v1[0] * v2[2];
  const c = v1[0] * v2[1] - v1[1] * v2[0];
  if (a === 0 && b === 0 && c === 0) return null; // collinear points
  const d = -(a * p1[0] + b * p1[1] + c * p1[2]);
  return [a, b, c, d];
}

/**
 * Minimal RANSAC implementation that segments the dominant plane
 * (floor) of a point cloud. Used as a local fallback for the
 * segmentation AI service when its API does not respond in time.
 */
export function detectPlanesRANSAC(points: Point[]): SegmentationOutput {
  if (points.length < 3) {
    return { walls: [], floorZ: 0, ceilingZ: 0 };
  }

  let bestPlane: [number, number, number, number] | null = null;
  let bestCount = -1;

  for (let i = 0; i < NUM_ITERATIONS; i++) {
    const idx = [0, 0, 0].map(() => Math.floor(Math.random() * points.length));
    const p1 = points[idx[0]!]!;
    const p2 = points[idx[1]!]!;
    const p3 = points[idx[2]!]!;
    const plane = planeFrom3Points(p1, p2, p3);
    if (!plane) continue;

    const inliers = points.filter((p) => distanceToPlane(p, plane) <= DISTANCE_THRESHOLD_M).length;
    if (inliers > bestCount) {
      bestCount = inliers;
      bestPlane = plane;
    }
  }

  // Simple approximation: the dominant plane found is assumed to be the
  // floor; ceiling = max observed Z. This minimal fallback does not
  // detect vertical walls (unlike the full AI model).
  const zs = points.map((p) => p[2]);
  const floorZ = bestPlane ? -bestPlane[3] / (bestPlane[2] || 1) : Math.min(...zs);
  const ceilingZ = Math.max(...zs);

  return { walls: [], floorZ, ceilingZ };
}
