export interface Bounds3D {
  minX: number;
  minY: number;
  minZ: number;
  maxX: number;
  maxY: number;
  maxZ: number;
}

interface OctreeNode {
  bounds: Bounds3D;
  occupied: boolean;
  children: OctreeNode[] | null;
}

const MAX_DEPTH = 6;
/** Overlap percentage above which two regions are considered occupied. */
const OVERLAP_TOLERANCE = 0.01; // 1%

function volume(b: Bounds3D): number {
  return (b.maxX - b.minX) * (b.maxY - b.minY) * (b.maxZ - b.minZ);
}

function intersection(a: Bounds3D, b: Bounds3D): Bounds3D | null {
  const minX = Math.max(a.minX, b.minX);
  const minY = Math.max(a.minY, b.minY);
  const minZ = Math.max(a.minZ, b.minZ);
  const maxX = Math.min(a.maxX, b.maxX);
  const maxY = Math.min(a.maxY, b.maxY);
  const maxZ = Math.min(a.maxZ, b.maxZ);
  if (maxX <= minX || maxY <= minY || maxZ <= minZ) return null;
  return { minX, minY, minZ, maxX, maxY, maxZ };
}

/** Octree of free/occupied space inside the Room. */
export class Octree {
  private root: OctreeNode;

  constructor(bounds: Bounds3D) {
    this.root = { bounds, occupied: false, children: null };
  }

  markOccupied(bounds: Bounds3D): void {
    this.markNode(this.root, bounds, 0);
  }

  private markNode(node: OctreeNode, bounds: Bounds3D, depth: number): void {
    const inter = intersection(node.bounds, bounds);
    if (!inter) return;

    const overlap = volume(inter) / volume(node.bounds);
    if (overlap < OVERLAP_TOLERANCE) return;

    if (depth >= MAX_DEPTH) {
      node.occupied = true;
      return;
    }

    if (!node.children) {
      node.children = this.subdivide(node.bounds);
    }
    for (const child of node.children) {
      this.markNode(child, bounds, depth + 1);
    }
    node.occupied = node.children.every((c) => c.occupied);
  }

  private subdivide(b: Bounds3D): OctreeNode[] {
    const midX = (b.minX + b.maxX) / 2;
    const midY = (b.minY + b.maxY) / 2;
    const midZ = (b.minZ + b.maxZ) / 2;

    const rangesX: [number, number][] = [
      [b.minX, midX],
      [midX, b.maxX],
    ];
    const rangesY: [number, number][] = [
      [b.minY, midY],
      [midY, b.maxY],
    ];
    const rangesZ: [number, number][] = [
      [b.minZ, midZ],
      [midZ, b.maxZ],
    ];

    const combinations: Bounds3D[] = [];
    for (const [minX, maxX] of rangesX) {
      for (const [minY, maxY] of rangesY) {
        for (const [minZ, maxZ] of rangesZ) {
          combinations.push({ minX, maxX, minY, maxY, minZ, maxZ });
        }
      }
    }
    return combinations.map((bounds) => ({ bounds, occupied: false, children: null }));
  }

  /** true if the region does not overlap (>1%) with any region marked as occupied. */
  isFree(bounds: Bounds3D): boolean {
    return !this.queryOccupied(this.root, bounds, 0);
  }

  private queryOccupied(node: OctreeNode, bounds: Bounds3D, depth: number): boolean {
    const inter = intersection(node.bounds, bounds);
    if (!inter) return false;

    const overlap = volume(inter) / volume(bounds);
    if (node.occupied && !node.children) {
      return overlap >= OVERLAP_TOLERANCE;
    }
    if (!node.children) return false;
    if (depth >= MAX_DEPTH) return node.occupied;

    return node.children.some((child) => this.queryOccupied(child, bounds, depth + 1));
  }
}
