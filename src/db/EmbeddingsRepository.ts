import { Pool } from "pg";

/**
 * Style embeddings insert/search using pgvector. Uses the `<=>`
 * (cosine) operator, matching the `vector_cosine_ops` HNSW index in
 * the schema — querying with a different operator would skip the index.
 */
export class EmbeddingsRepository {
  constructor(private pool: Pool) {}

  async saveEmbedding(furnitureId: string, vector: number[]): Promise<void> {
    if (vector.length !== 512) {
      throw new Error(
        `EmbeddingsRepository.saveEmbedding: expected a 512-dimension vector (CLIP ViT-B/32), received ${vector.length}`
      );
    }
    await this.pool.query("UPDATE furniture SET style_embedding = $1 WHERE id = $2", [
      `[${vector.join(",")}]`,
      furnitureId,
    ]);
  }

  async findSimilar(
    vector: number[],
    limit: number,
    excludeFurnitureId?: string
  ): Promise<{ id: string; name: string; distance: number }[]> {
    const excludeClause = excludeFurnitureId ? "WHERE id != $3" : "";
    const values: unknown[] = [`[${vector.join(",")}]`, limit];
    if (excludeFurnitureId) values.push(excludeFurnitureId);

    const { rows } = await this.pool.query(
      `SELECT id, name, style_embedding <=> $1 AS distance
       FROM furniture
       ${excludeClause}
       ORDER BY style_embedding <=> $1 ASC
       LIMIT $2`,
      values
    );
    return rows.map((r) => ({ id: r.id, name: r.name, distance: Number(r.distance) }));
  }
}
