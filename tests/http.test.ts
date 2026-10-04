import { describe, it, expect, vi, beforeAll } from "vitest";
import request from "supertest";

/**
 * Fake `pg.Pool` that understands just the handful of query shapes this
 * app issues, storing data in memory. It is NOT a SQL engine — it exists
 * so the full HTTP route stack (including PostGIS-backed queries) can be
 * exercised in CI without a real Postgres/Supabase instance. Fase 4 adds
 * a real-database version of these same checks.
 */
const { fakePool } = vi.hoisted(() => {
  class FakePool {
  projects = new Map<string, { id: string; userId: string; name: string }>();
  spaces = new Map<string, { geojson: any; heightM: number }>();
  furniture = new Map<string, any>();
  placedItems = new Map<string, { id: number; furnitureId: string; x: number; y: number; rotation: number }[]>();
  private nextItemId = 1;

  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (sql.startsWith("INSERT INTO projects")) {
      const [id, userId, name] = params as [string, string, string];
      this.projects.set(id, { id, userId, name });
      return { rows: [] };
    }

    if (sql.startsWith("INSERT INTO spaces")) {
      const [projectId, geojsonStr, heightM] = params as [string, string, number];
      this.spaces.set(projectId, { geojson: JSON.parse(geojsonStr), heightM });
      return { rows: [] };
    }

    if (sql.startsWith("SELECT ST_AsGeoJSON(shape)")) {
      const [projectId] = params as [string];
      const space = this.spaces.get(projectId);
      if (!space) return { rows: [] };
      return { rows: [{ shape_json: JSON.stringify(space.geojson), height_m: space.heightM }] };
    }

    if (sql.startsWith("INSERT INTO furniture")) {
      const [id, name, widthCm, heightCm, depthCm, category, material, price, style] = params as any[];
      this.furniture.set(id, { id, name, width_cm: widthCm, height_cm: heightCm, depth_cm: depthCm, category, material, price, style });
      return { rows: [] };
    }

    if (sql.startsWith("SELECT * FROM furniture")) {
      return { rows: [...this.furniture.values()] };
    }

    if (sql === "BEGIN" || sql === "COMMIT" || sql === "ROLLBACK") {
      return { rows: [] };
    }

    if (sql.startsWith("DELETE FROM placed_items")) {
      const [projectId] = params as [string];
      this.placedItems.set(projectId, []);
      return { rows: [] };
    }

    if (sql.startsWith("INSERT INTO placed_items")) {
      const [projectId, furnitureId, x, y, rotation] = params as [string, string, number, number, number];
      const list = this.placedItems.get(projectId) ?? [];
      list.push({ id: this.nextItemId++, furnitureId, x, y, rotation });
      this.placedItems.set(projectId, list);
      return { rows: [] };
    }

    if (sql.startsWith("SELECT pi.id, pi.furniture_id")) {
      const [projectId] = params as [string];
      const list = this.placedItems.get(projectId) ?? [];
      return {
        rows: list.map((i) => ({
          id: i.id,
          furniture_id: i.furnitureId,
          name: this.furniture.get(i.furnitureId)?.name ?? "unknown",
          x: i.x,
          y: i.y,
          rotation: i.rotation,
        })),
      };
    }

    throw new Error(`FakePool: unhandled query — ${sql}`);
  }

  async connect() {
    return {
      query: (text: string, params?: unknown[]) => this.query(text, params),
      release: () => {},
    };
  }
  }
  return { fakePool: new FakePool() };
});

vi.mock("pg", () => ({
  // Must be a real function (not an arrow fn) — `new Pool(...)` in pool.ts
  // requires a constructible value, and arrow functions can't be `new`'d.
  Pool: vi.fn(function PoolMock() {
    return fakePool;
  }),
}));

let app: import("express").Express;

beforeAll(async () => {
  process.env.DATABASE_URL = "postgresql://fake:fake@localhost:5432/fake";
  process.env.FRONTEND_URL = "http://localhost:3000";
  const { createApp } = await import("../src/server");
  app = createApp();
});

describe("GET /api/health", () => {
  it("responds ok without touching the database", async () => {
    const res = await request(app).get("/api/health");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });
});

describe("CORS", () => {
  it("allows the configured frontend origin", async () => {
    const res = await request(app).get("/api/health").set("Origin", "http://localhost:3000");
    expect(res.headers["access-control-allow-origin"]).toBe("http://localhost:3000");
  });

  it("rejects a non-whitelisted origin", async () => {
    const res = await request(app).get("/api/health").set("Origin", "https://evil-example.test");
    // cors() surfaces the rejection as a 500 from the error handler (no ACAO header granted).
    expect(res.headers["access-control-allow-origin"]).toBeUndefined();
  });
});

describe("CORS — distributed deployment mode (frontend and backend on different domains)", () => {
  /**
   * This simulates the real deployed topology: Vercel gives the
   * frontend both a stable production domain and a per-PR preview
   * domain. FRONTEND_URL supports a comma-separated list specifically
   * so both can be allowed at once without redeploying the backend
   * every time a preview URL changes.
   */
  it("allows every origin listed in a comma-separated FRONTEND_URL", async () => {
    process.env.FRONTEND_URL = "https://my-app.vercel.app,https://my-app-git-feature-x.vercel.app";
    const { createApp } = await import("../src/server");
    const distributedApp = createApp();

    const prod = await request(distributedApp).get("/api/health").set("Origin", "https://my-app.vercel.app");
    expect(prod.headers["access-control-allow-origin"]).toBe("https://my-app.vercel.app");

    const preview = await request(distributedApp)
      .get("/api/health")
      .set("Origin", "https://my-app-git-feature-x.vercel.app");
    expect(preview.headers["access-control-allow-origin"]).toBe("https://my-app-git-feature-x.vercel.app");

    const other = await request(distributedApp).get("/api/health").set("Origin", "https://some-other-site.test");
    expect(other.headers["access-control-allow-origin"]).toBeUndefined();

    process.env.FRONTEND_URL = "http://localhost:3000"; // restore for other tests
  });

  it("still works with no Origin header at all (server-to-server or curl, not a browser)", async () => {
    const res = await request(app).get("/api/health");
    expect(res.status).toBe(200);
  });
});

describe("Spaces — create, read, and EDIT the room surface", () => {
  const projectId = "proj-http-1";
  const irregularVertices = [
    { x: 0, y: 0 },
    { x: 4, y: 0 },
    { x: 4, y: 3 },
    { x: 2, y: 3 },
    { x: 2, y: 5 },
    { x: 0, y: 5 },
  ];

  it("rejects a polygon with fewer than 3 vertices (400, before touching the DB)", async () => {
    const res = await request(app)
      .post(`/api/spaces/${projectId}`)
      .send({ userId: "u1", name: "Living room", vertices: [{ x: 0, y: 0 }], heightM: 2.6 });
    expect(res.status).toBe(400);
  });

  it("creates an irregular (non-rectangular) space", async () => {
    const res = await request(app)
      .post(`/api/spaces/${projectId}`)
      .send({ userId: "u1", name: "Living room", vertices: irregularVertices, heightM: 2.6 });
    expect(res.status).toBe(200);
    expect(res.body.vertices).toHaveLength(6);
    expect(res.body.areaM2).toBeCloseTo(16, 1); // same L-shape area as the domain test
  });

  it("reads the space back", async () => {
    const res = await request(app).get(`/api/spaces/${projectId}`);
    expect(res.status).toBe(200);
    expect(res.body.vertices).toHaveLength(6);
  });

  it("EDITS the surface: moves a single vertex via PATCH", async () => {
    const res = await request(app).patch(`/api/spaces/${projectId}/vertices/0`).send({ x: -1, y: -1 });
    expect(res.status).toBe(200);
    expect(res.body.vertices[0]).toEqual({ x: -1, y: -1 });

    const readBack = await request(app).get(`/api/spaces/${projectId}`);
    expect(readBack.body.vertices[0]).toEqual({ x: -1, y: -1 });
  });

  it("rejects a vertex edit that would break the polygon (invalid index)", async () => {
    const res = await request(app).patch(`/api/spaces/${projectId}/vertices/99`).send({ x: 0, y: 0 });
    expect(res.status).toBe(400);
  });

  it("404s when reading a space that was never created", async () => {
    const res = await request(app).get("/api/spaces/does-not-exist");
    expect(res.status).toBe(404);
  });
});

describe("Furniture — manual object creation requires a name", () => {
  it("rejects an object with an empty name", async () => {
    const res = await request(app)
      .post("/api/furniture")
      .send({
        id: "f1",
        name: "",
        widthCm: 50,
        heightCm: 50,
        depthCm: 50,
        category: "chair",
        material: "wood",
        price: 20,
        style: "modern",
      });
    expect(res.status).toBe(400);
  });

  it("creates a manually-made object and it shows up in the catalog", async () => {
    const createRes = await request(app)
      .post("/api/furniture")
      .send({
        id: "f2",
        name: "My custom lamp",
        widthCm: 20,
        heightCm: 150,
        depthCm: 20,
        category: "lamp",
        material: "metal",
        price: 60,
        style: "industrial",
        createdManually: true,
      });
    expect(createRes.status).toBe(201);

    const listRes = await request(app).get("/api/furniture");
    expect(listRes.body.items.some((i: any) => i.id === "f2" && i.name === "My custom lamp")).toBe(true);
  });
});

describe("Layout — full pipeline through HTTP, persisted via the fake DB", () => {
  it("generates a layout and persists the placed items", async () => {
    const res = await request(app)
      .post("/api/layout/generate")
      .send({
        projectId: "proj-http-1",
        room: {
          vertices: [
            { x: 0, y: 0 },
            { x: 4, y: 0 },
            { x: 4, y: 5 },
            { x: 0, y: 5 },
          ],
          heightM: 2.6,
        },
        items: [
          {
            id: "f2",
            name: "My custom lamp",
            widthCm: 20,
            heightCm: 150,
            depthCm: 20,
            category: "lamp",
            material: "metal",
            price: 60,
            style: "industrial",
          },
        ],
        constraints: { minDistanceBetweenItemsCm: 15, minClearPathCm: 60 },
      });

    expect(res.status).toBe(200);
    expect(res.body.readyToRender).toBe(true);

    const itemsRes = await request(app).get("/api/spaces/proj-http-1/items");
    expect(itemsRes.body.items).toHaveLength(1);
    expect(itemsRes.body.items[0].name).toBe("My custom lamp");
  });
});
