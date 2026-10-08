import * as THREE from "three";
import type { Room } from "./Room";
import type { Furniture } from "./Furniture";
import type { ItemPosition } from "./LayoutEngine";

const MIN_CLEAR_WIDTH_M = 0.6; // 60cm

export interface ClearPathResult {
  valid: boolean;
  blockedDoors: string[]; // door segment ids
}

function boxForItem(item: Furniture, pos: ItemPosition): THREE.Box2 {
  const { width, depth } = item.getDimensionsCm();
  return new THREE.Box2(
    new THREE.Vector2(pos.x, pos.y),
    new THREE.Vector2(pos.x + width / 100, pos.y + depth / 100)
  );
}

/**
 * Checks that there is at least a 60cm clear path in front of every
 * door in the Room, given the final furniture layout.
 */
export function calculateClearPath(room: Room, items: Furniture[], positions: ItemPosition[]): ClearPathResult {
  const itemBoxes = positions.map((pos) => {
    const item = items.find((m) => m.id === pos.itemId)!;
    return boxForItem(item, pos);
  });

  const blockedDoors: string[] = [];

  for (const wall of room.getWallsWithDoor()) {
    const dx = wall.x2 - wall.x1;
    const dy = wall.y2 - wall.y1;
    const normal = new THREE.Vector2(-dy, dx).normalize().multiplyScalar(MIN_CLEAR_WIDTH_M);

    const center = new THREE.Vector2((wall.x1 + wall.x2) / 2, (wall.y1 + wall.y2) / 2);
    const pathZone = new THREE.Box2(
      new THREE.Vector2(Math.min(center.x, center.x + normal.x) - 0.1, Math.min(center.y, center.y + normal.y) - 0.1),
      new THREE.Vector2(Math.max(center.x, center.x + normal.x) + 0.1, Math.max(center.y, center.y + normal.y) + 0.1)
    );

    const blocked = itemBoxes.some((box) => box.intersectsBox(pathZone));
    if (blocked) {
      blockedDoors.push(wall.id);
    }
  }

  return { valid: blockedDoors.length === 0, blockedDoors };
}
