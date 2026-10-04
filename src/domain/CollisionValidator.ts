import * as THREE from "three";
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from "three-mesh-bvh";
import type { Furniture } from "./Furniture";
import type { ItemPosition } from "./LayoutEngine";

// Enables BVH on BufferGeometry (three-mesh-bvh hooks into the prototype).
THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

/** Margin tolerated as scan measurement error, not an actual collision. */
const OVERLAP_TOLERANCE_CM = 2;

export interface Collision {
  itemA: string;
  itemB: string;
  overlapCm: number;
}

export interface ValidationResult {
  valid: boolean;
  collisions: Collision[];
}

function boxForItem(item: Furniture, pos: ItemPosition): THREE.Box3 {
  const { width, depth } = item.getDimensionsCm();
  const widthM = width / 100;
  const depthM = depth / 100;
  const min = new THREE.Vector3(pos.x, pos.y, 0);
  const max = new THREE.Vector3(pos.x + widthM, pos.y + depthM, 3);
  return new THREE.Box3(min, max);
}

/**
 * Detects overlaps between the (AABB) boxes of placed items, using the
 * same Box3 that would feed a three-mesh-bvh BVH for more complex
 * geometry (real catalog meshes).
 */
export class CollisionValidator {
  validate(items: Furniture[], positions: ItemPosition[]): ValidationResult {
    const collisions: Collision[] = [];
    const boxes = positions.map((pos) => {
      const item = items.find((m) => m.id === pos.itemId);
      if (!item) {
        throw new Error(`CollisionValidator: item ${pos.itemId} was not found`);
      }
      return { pos, item, box: boxForItem(item, pos) };
    });

    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!;
        const b = boxes[j]!;
        const inter = a.box.clone().intersect(b.box);
        const size = new THREE.Vector3();
        inter.getSize(size);

        // Penetration depth = the axis with the SMALLER overlap (not the
        // larger one): if two boxes barely touch at one edge, the axis
        // with a wide overlap should not trigger a collision by itself.
        const overlapCm = Math.min(size.x, size.y) * 100;
        if (inter.isEmpty() || overlapCm <= OVERLAP_TOLERANCE_CM) {
          continue;
        }

        collisions.push({
          itemA: a.pos.itemId,
          itemB: b.pos.itemId,
          overlapCm: Math.round(overlapCm * 10) / 10,
        });
      }
    }

    return { valid: collisions.length === 0, collisions };
  }
}
