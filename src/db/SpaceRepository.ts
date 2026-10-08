import { Pool } from "pg";
import { Room } from "../domain/Room";
import type { ItemPosition } from "../domain/LayoutEngine";

export interface PlacedItem {
  id: number;
  furnitureId: string;
  name: string;
  x: number;
  y: number;
  rotation: number;
}

/**
 * Persistence for projects, the room's irregular shape (polygon in
 * meters) and the items placed inside each one.
 *
 * The shape is editable after the initial scan: `saveShape` is an
 * upsert (full replace), and `updateVertex` lets the caller move a
 * single vertex without resending the whole polygon — both paths keep
 * going through the Room domain class so the result is always
 * re-validated as a proper polygon before it reaches the database.
 */
export class SpaceRepository {
  constructor(private pool: Pool) {}

  async createProject(id: string, userId: string, name: string): Promise<void> {
    await this.pool.query(
      `INSERT INTO projects (id, user_id, name) VALUES ($1,$2,$3)
       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name`,
      [id, userId, name]
    );
  }

  /** Full replace of the room shape — used by the initial scan or a full manual redraw. */
  async saveShape(projectId: string, room: Room): Promise<void> {
    const geojson = room.toGeoJSON();
    await this.pool.query(
      `INSERT INTO spaces (project_id, shape, height_m)
       VALUES ($1, ST_GeomFromGeoJSON($2), $3)
       ON CONFLICT (project_id) DO UPDATE SET shape = EXCLUDED.shape, height_m = EXCLUDED.height_m, updated_at = now()`,
      [projectId, JSON.stringify(geojson), room.getHeightM()]
    );
  }

  async getShape(projectId: string): Promise<Room | null> {
    const { rows } = await this.pool.query(
      `SELECT ST_AsGeoJSON(shape) AS shape_json, height_m FROM spaces WHERE project_id = $1`,
      [projectId]
    );
    if (rows.length === 0) return null;
    const geojson = JSON.parse(rows[0].shape_json);
    return Room.fromGeoJSON(geojson, Number(rows[0].height_m));
  }

  /**
   * Moves a single vertex of an existing room (drag-to-edit from the
   * UI). Loads the current shape, applies the mutation through the Room
   * domain class (which re-validates the polygon), and persists the
   * updated shape — so an edit that would break the polygon is rejected
   * before it ever reaches the database.
   */
  async updateVertex(projectId: string, vertexIndex: number, point: { x: number; y: number }): Promise<Room> {
    const room = await this.getShape(projectId);
    if (!room) {
      throw new Error(`SpaceRepository.updateVertex: no space found for project ${projectId}`);
    }
    room.moveVertex(vertexIndex, point);
    await this.saveShape(projectId, room);
    return room;
  }

  /** Replaces all placed items of a project with the given layout. */
  async savePlacedItems(projectId: string, positions: ItemPosition[]): Promise<void> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("DELETE FROM placed_items WHERE project_id = $1", [projectId]);
      for (const pos of positions) {
        await client.query(
          `INSERT INTO placed_items (project_id, furniture_id, x, y, rotation) VALUES ($1,$2,$3,$4,$5)`,
          [projectId, pos.itemId, pos.x, pos.y, pos.rotation]
        );
      }
      await client.query("COMMIT");
    } catch (err) {
      await client.query("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }

  /**
   * Lists the items a space has — this is the query the AI chat panel
   * uses to "know" what is in the project before giving recommendations.
   */
  async listPlacedItems(projectId: string): Promise<PlacedItem[]> {
    const { rows } = await this.pool.query(
      `SELECT pi.id, pi.furniture_id, f.name, pi.x, pi.y, pi.rotation
       FROM placed_items pi
       JOIN furniture f ON f.id = pi.furniture_id
       WHERE pi.project_id = $1
       ORDER BY pi.added_at ASC`,
      [projectId]
    );
    return rows.map((r) => ({
      id: Number(r.id),
      furnitureId: r.furniture_id,
      name: r.name,
      x: Number(r.x),
      y: Number(r.y),
      rotation: Number(r.rotation),
    }));
  }
}
