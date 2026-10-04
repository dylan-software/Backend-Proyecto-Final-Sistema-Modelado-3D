import { z } from "zod";

/**
 * Input/output schemas for the 4 AI components of the pipeline. Every
 * AI service response is validated against these schemas before use;
 * a validation failure names exactly which field did not comply,
 * instead of a generic error.
 */

// 1) Space segmentation
export const SegmentationInput = z.object({
  points: z.array(z.tuple([z.number(), z.number(), z.number()])).min(100),
});
export const SegmentationOutput = z.object({
  walls: z.array(
    z.object({ x1: z.number(), y1: z.number(), x2: z.number(), y2: z.number(), hasDoor: z.boolean(), hasWindow: z.boolean() })
  ),
  floorZ: z.number(),
  ceilingZ: z.number(),
});
export type SegmentationOutput = z.infer<typeof SegmentationOutput>;

// 2) Generative layout
export const LayoutConstraintsSchema = z.object({
  minDistanceBetweenItemsCm: z.number().positive(),
  minClearPathCm: z.number().positive(),
  orientationConstraints: z.array(z.object({ item: z.string(), faceTowards: z.string() })).optional(),
});
export const AILayoutOutput = z.object({
  positions: z.array(z.object({ itemId: z.string(), x: z.number(), y: z.number(), rotation: z.number() })),
});
export type AILayoutOutput = z.infer<typeof AILayoutOutput>;

// 3) Style embeddings
export const EmbeddingOutput = z.object({
  vector: z.array(z.number()).length(512), // CLIP ViT-B/32
});
export type EmbeddingOutput = z.infer<typeof EmbeddingOutput>;

// 4) Lighting estimation
export const AILightingOutput = z.object({
  position: z.object({ x: z.number(), y: z.number(), z: z.number() }),
  intensity: z.number().min(0).max(1),
  color: z.string().regex(/^#[0-9a-fA-F]{6}$/),
});
export type AILightingOutput = z.infer<typeof AILightingOutput>;

/** Validates an AI response against its schema; throws naming the exact field that failed. */
export function validateAIResponse<T>(schema: z.ZodType<T>, response: unknown, componentName: string): T {
  const result = schema.safeParse(response);
  if (!result.success) {
    const firstError = result.error.issues[0];
    throw new Error(`Invalid response from ${componentName}: field "${firstError?.path.join(".")}" — ${firstError?.message}`);
  }
  return result.data;
}
