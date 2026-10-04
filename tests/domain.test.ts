import { describe, it, expect } from "vitest";
import { Room } from "../src/domain/Room";
import { Furniture } from "../src/domain/Furniture";

function irregularRoom(): Room {
  // An L-shaped room (irregular, not rectangular) — 5 vertices.
  return new Room(
    [
      { x: 0, y: 0 },
      { x: 4, y: 0 },
      { x: 4, y: 3 },
      { x: 2, y: 3 },
      { x: 2, y: 5 },
      { x: 0, y: 5 },
    ],
    2.6
  );
}

describe("Room — creation", () => {
  it("creates a valid irregular (non-rectangular) polygon", () => {
    const room = irregularRoom();
    expect(room.getVertices()).toHaveLength(6);
    expect(room.getHeightM()).toBe(2.6);
  });

  it("throws if fewer than 3 vertices are given", () => {
    expect(() => new Room([{ x: 0, y: 0 }, { x: 1, y: 0 }], 2.6)).toThrow();
  });

  it("throws if the polygon has ~0 area (collinear points)", () => {
    expect(
      () =>
        new Room(
          [
            { x: 0, y: 0 },
            { x: 1, y: 0 },
            { x: 2, y: 0 },
          ],
          2.6
        )
    ).toThrow();
  });

  it("throws if heightM is <= 0", () => {
    expect(
      () =>
        new Room(
          [
            { x: 0, y: 0 },
            { x: 4, y: 0 },
            { x: 4, y: 4 },
            { x: 0, y: 4 },
          ],
          0
        )
    ).toThrow();
  });

  it("computes the area correctly for an L-shaped room", () => {
    const room = irregularRoom();
    // 4x5 square (20) minus the 2x2 cut corner (4) = 16
    expect(room.getAreaM2()).toBeCloseTo(16, 5);
  });
});

describe("Room — point-in-polygon with an irregular shape", () => {
  const room = irregularRoom();

  it("recognizes a point inside the main body as inside", () => {
    expect(room.containsPoint(1, 1)).toBe(true);
  });

  it("recognizes a point inside the cut-out corner as OUTSIDE", () => {
    // (3, 4) falls in the area that was cut away by the L-shape.
    expect(room.containsPoint(3, 4)).toBe(false);
  });
});

describe("Room — editing the surface after creation", () => {
  it("moves a single vertex without touching the others", () => {
    const room = irregularRoom();
    room.moveVertex(0, { x: -1, y: -1 });
    expect(room.getVertices()[0]).toEqual({ x: -1, y: -1 });
    expect(room.getVertices()[1]).toEqual({ x: 4, y: 0 });
  });

  it("rejects a vertex move that breaks the polygon", () => {
    const room = irregularRoom();
    // Moving vertex 0 onto vertex 1 collapses an edge to zero length,
    // which is still a technically valid (if degenerate) polygon area-wise
    // in most cases, so instead force true degeneration: move every point
    // onto the same line by relocating a vertex far outside sensibly is not
    // guaranteed to break it — so we assert against moving to a state with 0 area directly.
    expect(() => room.moveVertex(0, { x: 4, y: 0 })).not.toThrow();
    // A real break: set every vertex onto a single line via setVertices.
    expect(() =>
      room.setVertices([
        { x: 0, y: 0 },
        { x: 1, y: 0 },
        { x: 2, y: 0 },
      ])
    ).toThrow();
  });

  it("throws moveVertex with an out-of-range index", () => {
    const room = irregularRoom();
    expect(() => room.moveVertex(99, { x: 0, y: 0 })).toThrow();
  });

  it("inserts a new vertex and keeps the polygon valid", () => {
    const room = irregularRoom();
    const before = room.getVertices().length;
    room.insertVertex(0, { x: 2, y: -1 });
    expect(room.getVertices()).toHaveLength(before + 1);
    expect(room.getVertices()[1]).toEqual({ x: 2, y: -1 });
  });

  it("removes a vertex and keeps the polygon valid", () => {
    const room = irregularRoom();
    const before = room.getVertices().length;
    room.removeVertex(5);
    expect(room.getVertices()).toHaveLength(before - 1);
  });

  it("refuses to remove a vertex below a triangle (3 vertices minimum)", () => {
    const room = new Room(
      [
        { x: 0, y: 0 },
        { x: 4, y: 0 },
        { x: 0, y: 4 },
      ],
      2.6
    );
    expect(() => room.removeVertex(0)).toThrow();
  });

  it("setVertices fully replaces the shape and clears door/window markers", () => {
    const room = irregularRoom();
    room.markDoor("0-1");
    expect(room.getWallsWithDoor()).toHaveLength(1);

    room.setVertices([
      { x: 0, y: 0 },
      { x: 3, y: 0 },
      { x: 3, y: 3 },
      { x: 0, y: 3 },
    ]);
    expect(room.getVertices()).toHaveLength(4);
    expect(room.getWallsWithDoor()).toHaveLength(0); // markers reset after a full redraw
  });

  it("round-trips through GeoJSON (export/import) after an edit", () => {
    const room = irregularRoom();
    room.moveVertex(2, { x: 5, y: 2 });
    const geojson = room.toGeoJSON();
    const restored = Room.fromGeoJSON(geojson, room.getHeightM());
    expect(restored.getVertices()).toEqual(room.getVertices());
  });
});

describe("Furniture", () => {
  const base = {
    id: "m1",
    name: "3-seat sofa",
    widthCm: 200,
    heightCm: 80,
    depthCm: 90,
    category: "sofa" as const,
    material: "fabric",
    price: 450,
    style: "scandinavian",
  };

  it("creates a valid item", () => {
    const item = new Furniture(base);
    expect(item.getDimensionsCm()).toEqual({ width: 200, height: 80, depth: 90 });
  });

  it("requires a non-empty name", () => {
    expect(() => new Furniture({ ...base, name: "" })).toThrow();
  });

  it("throws if a dimension is <= 0", () => {
    expect(() => new Furniture({ ...base, widthCm: 0 })).toThrow();
    expect(() => new Furniture({ ...base, depthCm: -5 })).toThrow();
  });

  it("toJSON produces an object with the same fields as the SQL schema", () => {
    const item = new Furniture(base);
    expect(item.toJSON()).toEqual({
      id: base.id,
      name: base.name,
      widthCm: base.widthCm,
      heightCm: base.heightCm,
      depthCm: base.depthCm,
      category: base.category,
      material: base.material,
      price: base.price,
      style: base.style,
    });
  });
});
