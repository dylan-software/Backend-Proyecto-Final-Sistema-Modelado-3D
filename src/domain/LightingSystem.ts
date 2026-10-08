export interface LightingParameters {
  position: { x: number; y: number; z: number };
  intensity: number; // 0-1
  color: string; // hex, e.g. "#fff2e0"
  source: "estimated" | "neutral_preset";
}

const NEUTRAL_PRESET: LightingParameters = {
  position: { x: 0, y: 3, z: 0 },
  intensity: 0.8,
  color: "#ffffff",
  source: "neutral_preset",
};

/**
 * Derives light parameters consumable by the render from photos of the
 * space, with a neutral preset as fallback when no photos are
 * available or the estimator service fails — never throws.
 */
export class LightingSystem {
  async estimateFromPhotos(
    photos: Uint8Array[],
    estimator?: (photos: Uint8Array[]) => Promise<LightingParameters>
  ): Promise<LightingParameters> {
    if (photos.length === 0 || !estimator) {
      return NEUTRAL_PRESET;
    }
    try {
      return await estimator(photos);
    } catch {
      // If the AI service fails, it must not break the pipeline.
      return NEUTRAL_PRESET;
    }
  }
}
