import { validateAIResponse, EmbeddingOutput } from "./contracts";
import type { EmbeddingsRepository } from "../db/EmbeddingsRepository";
import type { FurnitureRepository } from "../db/FurnitureRepository";

export interface EmbeddingsServiceConfig {
  url: string; // hosted CLIP API (e.g. a Hugging Face Space)
  apiKey?: string;
}

/** In-memory cache: avoids recomputing an item's embedding on every request. */
const embeddingsCache = new Map<string, number[]>();

async function generateEmbedding(furnitureId: string, photoUrl: string, config: EmbeddingsServiceConfig): Promise<number[]> {
  if (embeddingsCache.has(furnitureId)) {
    return embeddingsCache.get(furnitureId)!;
  }

  const response = await fetch(config.url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
    },
    body: JSON.stringify({ image_url: photoUrl }),
  });

  if (!response.ok) {
    throw new Error(`Embeddings service responded with HTTP ${response.status}`);
  }

  const json = await response.json();
  const output = validateAIResponse(EmbeddingOutput, json, "CLIP embeddings service");
  embeddingsCache.set(furnitureId, output.vector);
  return output.vector;
}

/**
 * Handler for GET /api/furniture/similar?furnitureId=...
 * Generates the item's embedding if it doesn't exist yet (CLIP
 * ViT-B/32) and searches for similar ones in pgvector.
 */
export async function handleSimilarFurniture(
  furnitureId: string,
  limit: number,
  furnitureRepo: FurnitureRepository,
  embeddingsRepo: EmbeddingsRepository,
  aiConfig: EmbeddingsServiceConfig
): Promise<{ id: string; name: string; distance: number }[]> {
  const item = await furnitureRepo.getById(furnitureId);
  if (!item) {
    throw new Error(`handleSimilarFurniture: item ${furnitureId} does not exist`);
  }

  // In a real setup the photo URL would come from the item's own
  // record (e.g. item.photoUrl); here it is assumed already resolved.
  const photoUrl = `furniture/${furnitureId}/photo-0.jpg`;
  const vector = await generateEmbedding(furnitureId, photoUrl, aiConfig);
  await embeddingsRepo.saveEmbedding(furnitureId, vector);

  return embeddingsRepo.findSimilar(vector, limit, furnitureId);
}
