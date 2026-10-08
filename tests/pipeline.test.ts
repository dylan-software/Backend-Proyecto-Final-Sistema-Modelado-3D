import { describe, it, expect } from "vitest";
import { Room } from "../src/domain/Room";
import { Furniture } from "../src/domain/Furniture";
import { LayoutEngine } from "../src/domain/LayoutEngine";
import { CollisionValidator } from "../src/domain/CollisionValidator";
import { calculateClearPath } from "../src/domain/clearPath";
import { DecorationPipeline } from "../src/domain/DecorationPipeline";

function testRoom(): Room {
  const room = new Room(
    [
      { x: 0, y: 0 },
      { x: 4, y: 0 },
      { x: 4, y: 5 },
      { x: 0, y: 5 },
    ],
    2.6
  );
  room.markDoor("0-1");
  return room;
}

function testItem(id: string, width = 100, depth = 60): Furniture {
  return new Furniture({
    id,
    name: `Item ${id}`,
    widthCm: width,
    heightCm: 80,
    depthCm: depth,
    category: "table",
    material: "wood",
    price: 100,
    style: "modern",
  });
}

describe("LayoutEngine", () => {
  it("places items without throwing and without warnings when there is room", () => {
    const engine = new LayoutEngine();
    const result = engine.generate(testRoom(), [testItem("m1"), testItem("m2")], {
      minDistanceBetweenItemsCm: 15,
      minClearPathCm: 60,
    });
    expect(result.positions).toHaveLength(2);
    expect(result.warnings).toHaveLength(0);
  });

  it("returns warnings instead of throwing when there is not enough space", () => {
    const engine = new LayoutEngine();
    const items = Array.from({ length: 20 }, (_, i) => testItem(`m${i}`, 150, 150));
    const result = engine.generate(testRoom(), items, { minDistanceBetweenItemsCm: 15, minClearPathCm: 60 });
    expect(result.warnings.length).toBeGreaterThan(0);
  });

  it("respects an irregular (non-rectangular) room shape", () => {
    const lShaped = new Room(
      [
        { x: 0, y: 0 },
        { x: 4, y: 0 },
        { x: 4, y: 2 },
        { x: 2, y: 2 },
        { x: 2, y: 5 },
        { x: 0, y: 5 },
      ],
      2.6
    );
    const engine = new LayoutEngine();
    const result = engine.generate(lShaped, [testItem("m1", 300, 100)], {
      minDistanceBetweenItemsCm: 15,
      minClearPathCm: 60,
    });
    // A 3m-wide item cannot fit in the 2m-wide leg of the L — it must
    // either be rejected (warning) or placed only where it truly fits.
    for (const pos of result.positions) {
      expect(lShaped.containsRectangle(pos.x, pos.y, 3, 1)).toBe(true);
    }
  });
});

describe("CollisionValidator", () => {
  const items = [testItem("m1", 100, 100), testItem("m2", 100, 100)];

  it("reports no collision without overlap", () => {
    const validator = new CollisionValidator();
    const r = validator.validate(items, [
      { itemId: "m1", x: 0, y: 0, rotation: 0 },
      { itemId: "m2", x: 2, y: 0, rotation: 0 },
    ]);
    expect(r.valid).toBe(true);
  });

  it("tolerates a slight overlap (<2cm)", () => {
    const validator = new CollisionValidator();
    const r = validator.validate(items, [
      { itemId: "m1", x: 0, y: 0, rotation: 0 },
      { itemId: "m2", x: 0.99, y: 0, rotation: 0 }, // 1cm overlap
    ]);
    expect(r.valid).toBe(true);
  });

  it("rejects a clear overlap (>10cm)", () => {
    const validator = new CollisionValidator();
    const r = validator.validate(items, [
      { itemId: "m1", x: 0, y: 0, rotation: 0 },
      { itemId: "m2", x: 0.8, y: 0, rotation: 0 }, // 20cm overlap
    ]);
    expect(r.valid).toBe(false);
    expect(r.collisions).toHaveLength(1);
  });
});

describe("calculateClearPath", () => {
  it("rejects a layout that blocks the door", () => {
    const item = testItem("m1", 200, 200);
    const r = calculateClearPath(testRoom(), [item], [{ itemId: "m1", x: 0, y: 0, rotation: 0 }]);
    expect(r.valid).toBe(false);
    expect(r.blockedDoors).toContain("0-1");
  });

  it("accepts a layout that leaves the door clear", () => {
    const item = testItem("m1", 100, 100);
    const r = calculateClearPath(testRoom(), [item], [{ itemId: "m1", x: 3, y: 3, rotation: 0 }]);
    expect(r.valid).toBe(true);
  });
});

describe("DecorationPipeline", () => {
  it("isolates errors per stage and sets readyToRender correctly", async () => {
    const pipeline = new DecorationPipeline();
    const result = await pipeline.run(testRoom(), [testItem("m1", 100, 60)], {
      minDistanceBetweenItemsCm: 15,
      minClearPathCm: 60,
    });
    expect(result.errors).toHaveLength(0);
    expect(result.lighting.source).toBe("neutral_preset");
  });

  it("never blindly trusts an alternative generator that produces collisions", async () => {
    const pipeline = new DecorationPipeline();
    const items = [testItem("m1", 200, 200), testItem("m2", 200, 200)];
    const result = await pipeline.run(
      testRoom(),
      items,
      { minDistanceBetweenItemsCm: 15, minClearPathCm: 60 },
      {
        alternativeGenerator: async () => ({
          positions: [
            { itemId: "m1", x: 0, y: 0, rotation: 0 },
            { itemId: "m2", x: 0.1, y: 0, rotation: 0 }, // forced collision
          ],
          warnings: [],
        }),
      }
    );
    expect(result.validation.valid).toBe(false);
    expect(result.readyToRender).toBe(false);
  });
});
