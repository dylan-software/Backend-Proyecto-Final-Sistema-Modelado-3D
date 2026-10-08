import { Pool } from "pg";
import type { FurnitureJSON } from "../domain/Furniture";

/**
 * PostgreSQL client for the furniture/objects catalog. Every function
 * handles connection/timeout errors with a clear message instead of
 * letting the raw `pg` driver stack trace leak to the caller.
 */
export class FurnitureRepository {
  constructor(private pool: Pool) {}

  private async run<T>(operation: string, fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (err) {
      const message = (err as Error).message ?? "unknown error";
      throw new Error(`FurnitureRepository.${operation}: the operation could not complete (${message})`);
    }
  }

  async create(item: FurnitureJSON & { createdManually?: boolean }): Promise<void> {
    await this.run("create", () =>
      this.pool.query(
        `INSERT INTO furniture (id, name, width_cm, height_cm, depth_cm, category, material, price, style, created_manually)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
        [
          item.id,
          item.name,
          item.widthCm,
          item.heightCm,
          item.depthCm,
          item.category,
          item.material,
          item.price,
          item.style,
          item.createdManually ?? false,
        ]
      )
    );
  }

  async list(filters: { category?: string } = {}): Promise<FurnitureJSON[]> {
    return this.run("list", async () => {
      const conditions: string[] = [];
      const values: unknown[] = [];
      if (filters.category) {
        values.push(filters.category);
        conditions.push(`category = $${values.length}`);
      }
      const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
      const { rows } = await this.pool.query(`SELECT * FROM furniture ${where}`, values);
      return rows.map(mapRow);
    });
  }

  async getById(id: string): Promise<FurnitureJSON | null> {
    return this.run("getById", async () => {
      const { rows } = await this.pool.query("SELECT * FROM furniture WHERE id = $1", [id]);
      return rows[0] ? mapRow(rows[0]) : null;
    });
  }
}

function mapRow(row: any): FurnitureJSON {
  return {
    id: row.id,
    name: row.name,
    widthCm: Number(row.width_cm),
    heightCm: Number(row.height_cm),
    depthCm: Number(row.depth_cm),
    category: row.category,
    material: row.material,
    price: Number(row.price),
    style: row.style,
  };
}
