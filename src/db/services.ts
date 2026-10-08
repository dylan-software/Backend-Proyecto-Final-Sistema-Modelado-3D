import { pool } from "./pool";
import { FurnitureRepository } from "./FurnitureRepository";
import { SpaceRepository } from "./SpaceRepository";
import { EmbeddingsRepository } from "./EmbeddingsRepository";
import { StorageClient } from "./StorageClient";
import { S3Client } from "@aws-sdk/client-s3";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing environment variable ${name} (see .env.example)`);
  return value;
}

/**
 * Single place where backend + database + AI are "linked": every
 * Express route imports `services` instead of instantiating loose
 * clients, so the whole app shares the same connection pool (pointing
 * at Supabase in production, or the local Postgres in development —
 * decided purely by DATABASE_URL).
 */
export const services = {
  furniture: new FurnitureRepository(pool),
  spaces: new SpaceRepository(pool),
  embeddings: new EmbeddingsRepository(pool),

  get storage(): StorageClient {
    const s3 = new S3Client({
      region: process.env.S3_REGION ?? "auto",
      endpoint: process.env.S3_ENDPOINT, // needed for R2; omit for AWS S3
      credentials: {
        accessKeyId: required("S3_ACCESS_KEY_ID"),
        secretAccessKey: required("S3_SECRET_ACCESS_KEY"),
      },
    });
    return new StorageClient(s3, required("S3_BUCKET"));
  },

  aiConfig: {
    segmentation: { url: process.env.AI_SEGMENTATION_URL ?? "", apiKey: process.env.AI_API_KEY },
    layout: { url: process.env.AI_LAYOUT_URL ?? "", apiKey: process.env.AI_API_KEY },
    embeddings: { url: process.env.AI_EMBEDDINGS_URL ?? "", apiKey: process.env.AI_API_KEY },
    lighting: { url: process.env.AI_LIGHTING_URL ?? "", apiKey: process.env.AI_API_KEY },
  },

  /** OpenRouter — chat panel (phase 3). Added here now to avoid reopening this file later. */
  openRouterConfig: {
    apiKey: process.env.OPENROUTER_API_KEY ?? "",
    model: process.env.OPENROUTER_MODEL ?? "openai/gpt-4o-mini",
    url: "https://openrouter.ai/api/v1/chat/completions",
  },
};
