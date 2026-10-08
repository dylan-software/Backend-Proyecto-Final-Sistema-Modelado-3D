import { validateAIResponse, AILightingOutput } from "./contracts";
import type { LightingParameters } from "../domain/LightingSystem";

export interface LightingServiceConfig {
  url: string;
  apiKey?: string;
}

/**
 * Lighting estimator from photos, to pass as `estimator` to
 * LightingSystem.estimateFromPhotos(). If this service fails,
 * LightingSystem already falls back to the neutral preset — here we
 * only propagate the error, never silence it twice.
 */
export function createLightingEstimator(config: LightingServiceConfig) {
  return async function estimate(photos: Uint8Array[]): Promise<LightingParameters> {
    const photosBase64 = photos.map((f) => Buffer.from(f).toString("base64"));

    const response = await fetch(config.url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
      },
      body: JSON.stringify({ photos: photosBase64 }),
    });

    if (!response.ok) {
      throw new Error(`Lighting service responded with HTTP ${response.status}`);
    }

    const json = await response.json();
    const output = validateAIResponse(AILightingOutput, json, "lighting estimation service");

    return { ...output, source: "estimated" };
  };
}
