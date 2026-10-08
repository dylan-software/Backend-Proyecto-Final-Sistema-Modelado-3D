import { validateAIResponse, SegmentationOutput, SegmentationInput } from "./contracts";
import { detectPlanesRANSAC } from "./ransacFallback";

const TIMEOUT_MS = 15_000;

export interface SegmentationServiceConfig {
  url: string;
  apiKey?: string;
}

/**
 * Calls the 3D semantic segmentation model. If the API does not
 * respond within 15s, falls back to local RANSAC plane detection
 * instead of failing the whole pipeline.
 */
export async function segmentSpace(
  points: [number, number, number][],
  config: SegmentationServiceConfig
): Promise<SegmentationOutput & { source: "ai" | "ransac_fallback" }> {
  const input = SegmentationInput.parse({ points });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(config.url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
      },
      body: JSON.stringify(input),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const json = await response.json();
    const output = validateAIResponse(SegmentationOutput, json, "segmentation service");
    return { ...output, source: "ai" };
  } catch {
    // Timeout, network failure, or invalid response: do not propagate
    // the error — use local RANSAC as the pipeline's safety net.
    const fallback = detectPlanesRANSAC(points);
    return { ...fallback, source: "ransac_fallback" };
  } finally {
    clearTimeout(timeout);
  }
}
