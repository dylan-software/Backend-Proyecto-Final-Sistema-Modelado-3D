import type { Room } from "./Room";
import type { Furniture } from "./Furniture";

export interface ItemPosition {
  itemId: string;
  x: number;
  y: number;
  rotation: number; // degrees
}

export interface LayoutConstraints {
  minDistanceBetweenItemsCm: number;
  minClearPathCm: number;
  orientationConstraints?: { item: string; faceTowards: string }[];
}

export interface LayoutResult {
  positions: ItemPosition[];
  warnings: string[];
}

/**
 * Generates an initial furniture layout respecting basic functional
 * constraints (minimum distance between items) and the real shape of
 * the room (not just its bounding box — see Room.containsRectangle).
 *
 * This is a deterministic "grid packing" implementation — not the
 * generative AI model (that one plugs in as an alternative generator
 * in DecorationPipeline). It never throws on an unsatisfiable layout;
 * it returns a partial result with warnings instead.
 */
export class LayoutEngine {
  generate(room: Room, items: Furniture[], constraints: LayoutConstraints): LayoutResult {
    const warnings: string[] = [];
    const box = room.getBoundingBox();
    const minDistanceM = constraints.minDistanceBetweenItemsCm / 100;

    const positions: ItemPosition[] = [];
    let cursorX = box.minX + minDistanceM;
    let cursorY = box.minY + minDistanceM;
    let maxRowHeight = 0;

    for (const item of items) {
      const { width, depth } = item.getDimensionsCm();
      const widthM = width / 100;
      const depthM = depth / 100;

      if (cursorX + widthM > box.maxX - minDistanceM) {
        cursorX = box.minX + minDistanceM;
        cursorY += maxRowHeight + minDistanceM;
        maxRowHeight = 0;
      }

      // Irregular space: even if it fits inside the bounding box, it may
      // fall outside the real polygon (e.g. a cut corner). Try shifting
      // along X within the same row before giving up on this item.
      let placed = false;
      let tryX = cursorX;
      while (tryX + widthM <= box.maxX - minDistanceM) {
        if (room.containsRectangle(tryX, cursorY, widthM, depthM)) {
          positions.push({ itemId: item.id, x: tryX, y: cursorY, rotation: 0 });
          cursorX = tryX + widthM + minDistanceM;
          maxRowHeight = Math.max(maxRowHeight, depthM);
          placed = true;
          break;
        }
        tryX += 0.1; // shift 10cm and retry
      }

      if (!placed) {
        if (cursorY + depthM > box.maxY - minDistanceM) {
          warnings.push(
            `Not enough space to place item ${item.id} while respecting the minimum distance of ${constraints.minDistanceBetweenItemsCm}cm.`
          );
        } else {
          warnings.push(`Item ${item.id} did not fit inside the irregular shape of the room in the current row.`);
        }
      }
    }

    return { positions, warnings };
  }
}
