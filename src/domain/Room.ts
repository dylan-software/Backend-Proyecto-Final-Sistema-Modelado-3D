export interface Point2D {
  x: number;
  y: number;
}

export interface Wall {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  hasDoor: boolean;
  hasWindow: boolean;
}

/**
 * Represents the physical scanned space as an IRREGULAR POLYGON (not
 * necessarily rectangular), at real metric scale. Each vertex is
 * captured during the AR scan (taps on the real corners).
 *
 * "Walls" are not a separate concept: they are the segments between
 * consecutive polygon vertices. Doors/windows are marked on those
 * segments by id (`${i}-${i+1}`).
 *
 * The shape is MUTABLE on purpose: the user can edit the room surface
 * after the initial scan (move, add, or remove vertices), so every
 * mutation re-validates the polygon instead of assuming it stays valid.
 */
export class Room {
  private vertices: Point2D[];
  private heightM: number;
  private doors: Set<string> = new Set();
  private windows: Set<string> = new Set();

  constructor(vertices: Point2D[], heightM: number) {
    this.validatePolygon(vertices);
    this.validateDimension(heightM, "heightM");
    this.vertices = vertices;
    this.heightM = heightM;
  }

  private validateDimension(value: number, field: string): void {
    if (value <= 0) {
      throw new Error(`Room: ${field} must be greater than 0 (received: ${value})`);
    }
  }

  private validatePolygon(vertices: Point2D[]): void {
    if (vertices.length < 3) {
      throw new Error(`Room: a space needs at least 3 vertices to form a polygon (received: ${vertices.length})`);
    }
    const area = this.computeArea(vertices);
    if (Math.abs(area) < 0.01) {
      throw new Error("Room: the given vertices do not form a valid polygon (area ≈ 0)");
    }
  }

  private computeArea(vertices: Point2D[]): number {
    let area = 0;
    for (let i = 0; i < vertices.length; i++) {
      const a = vertices[i]!;
      const b = vertices[(i + 1) % vertices.length]!;
      area += a.x * b.y - b.x * a.y;
    }
    return area / 2;
  }

  getVertices(): ReadonlyArray<Point2D> {
    return this.vertices;
  }

  getAreaM2(): number {
    return Math.abs(this.computeArea(this.vertices));
  }

  getHeightM(): number {
    return this.heightM;
  }

  // --- Mutations: the room surface can be edited by the user ---------

  /** Replaces the whole shape at once (e.g. re-scanning or a full manual redraw). */
  setVertices(vertices: Point2D[]): void {
    this.validatePolygon(vertices);
    this.vertices = vertices;
    // Door/window markers reference segment ids like "i-(i+1)"; a full
    // reshape invalidates those positions, so they are cleared — the
    // caller is expected to re-mark doors/windows after a full redraw.
    this.doors.clear();
    this.windows.clear();
  }

  /** Moves a single vertex (drag-to-edit in the UI) without touching the others. */
  moveVertex(index: number, point: Point2D): void {
    if (index < 0 || index >= this.vertices.length) {
      throw new Error(`Room.moveVertex: index ${index} is out of range (0-${this.vertices.length - 1})`);
    }
    const next = [...this.vertices];
    next[index] = point;
    this.validatePolygon(next);
    this.vertices = next;
  }

  /** Inserts a new vertex between `afterIndex` and the next one (splits an edge). */
  insertVertex(afterIndex: number, point: Point2D): void {
    if (afterIndex < 0 || afterIndex >= this.vertices.length) {
      throw new Error(`Room.insertVertex: index ${afterIndex} is out of range`);
    }
    const next = [...this.vertices];
    next.splice(afterIndex + 1, 0, point);
    this.validatePolygon(next);
    this.vertices = next;
    this.doors.clear();
    this.windows.clear();
  }

  /** Removes a vertex (simplifies the shape); refuses to go below a triangle. */
  removeVertex(index: number): void {
    if (this.vertices.length <= 3) {
      throw new Error("Room.removeVertex: a room must keep at least 3 vertices");
    }
    if (index < 0 || index >= this.vertices.length) {
      throw new Error(`Room.removeVertex: index ${index} is out of range`);
    }
    const next = this.vertices.filter((_, i) => i !== index);
    this.validatePolygon(next);
    this.vertices = next;
    this.doors.clear();
    this.windows.clear();
  }

  /**
   * Point-in-polygon (ray casting). Lets LayoutEngine and
   * CollisionValidator respect the real shape of the space, not just
   * its rectangular bounding box.
   */
  containsPoint(x: number, y: number): boolean {
    let inside = false;
    const v = this.vertices;
    for (let i = 0, j = v.length - 1; i < v.length; j = i++) {
      const vi = v[i]!;
      const vj = v[j]!;
      const crosses = vi.y > y !== vj.y > y && x < ((vj.x - vi.x) * (y - vi.y)) / (vj.y - vi.y) + vi.x;
      if (crosses) inside = !inside;
    }
    return inside;
  }

  /** true if all 4 corners of a rectangle fall inside the polygon (good enough approximation for a piece of furniture). */
  containsRectangle(x: number, y: number, width: number, depth: number): boolean {
    return (
      this.containsPoint(x, y) &&
      this.containsPoint(x + width, y) &&
      this.containsPoint(x, y + depth) &&
      this.containsPoint(x + width, y + depth)
    );
  }

  /** Bounding box — useful for the Octree, which does need rectangular bounds. */
  getBoundingBox(): { minX: number; minY: number; maxX: number; maxY: number } {
    const xs = this.vertices.map((v) => v.x);
    const ys = this.vertices.map((v) => v.y);
    return { minX: Math.min(...xs), minY: Math.min(...ys), maxX: Math.max(...xs), maxY: Math.max(...ys) };
  }

  /** Lists the segments (vertex_i -> vertex_i+1) as Wall objects. */
  getWalls(): Wall[] {
    const walls: Wall[] = [];
    for (let i = 0; i < this.vertices.length; i++) {
      const a = this.vertices[i]!;
      const b = this.vertices[(i + 1) % this.vertices.length]!;
      const id = `${i}-${(i + 1) % this.vertices.length}`;
      walls.push({
        id,
        x1: a.x,
        y1: a.y,
        x2: b.x,
        y2: b.y,
        hasDoor: this.doors.has(id),
        hasWindow: this.windows.has(id),
      });
    }
    return walls;
  }

  getWallsWithDoor(): Wall[] {
    return this.getWalls().filter((w) => w.hasDoor);
  }

  markDoor(segmentId: string): void {
    this.doors.add(segmentId);
  }

  markWindow(segmentId: string): void {
    this.windows.add(segmentId);
  }

  /** Exports as a GeoJSON Polygon — the format SpaceRepository stores in PostGIS. */
  toGeoJSON(): { type: "Polygon"; coordinates: number[][][] } {
    const ring = [...this.vertices.map((v) => [v.x, v.y]), [this.vertices[0]!.x, this.vertices[0]!.y]];
    return { type: "Polygon", coordinates: [ring] };
  }

  static fromGeoJSON(geojson: { coordinates: number[][][] }, heightM: number): Room {
    const ring = geojson.coordinates[0]!;
    // GeoJSON closes the ring by repeating the first point as the last — drop it.
    const vertices = ring.slice(0, -1).map(([x, y]) => ({ x: x!, y: y! }));
    return new Room(vertices, heightM);
  }
}
