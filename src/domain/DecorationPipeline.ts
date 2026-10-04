import type { Room } from "./Room";
import type { Furniture } from "./Furniture";
import { LayoutEngine, type LayoutConstraints, type LayoutResult } from "./LayoutEngine";
import { CollisionValidator, type ValidationResult } from "./CollisionValidator";
import { calculateClearPath, type ClearPathResult } from "./clearPath";
import { LightingSystem, type LightingParameters } from "./LightingSystem";

export interface PipelineResult {
  layout: LayoutResult;
  validation: ValidationResult;
  clearPath: ClearPathResult;
  lighting: LightingParameters;
  /** true only if layout, collisions and clear path are all valid — the render must not run otherwise. */
  readyToRender: boolean;
  errors: string[];
}

export interface AlternativeLayoutGenerator {
  (room: Room, items: Furniture[], constraints: LayoutConstraints): Promise<LayoutResult>;
}

/**
 * Orchestrates LayoutEngine, CollisionValidator and clear-path checking
 * as isolated steps: if one stage fails, it does not call the next one
 * with invalid data, and it always returns an explainable result
 * instead of throwing an unhandled exception.
 */
export class DecorationPipeline {
  private layoutEngine = new LayoutEngine();
  private collisionValidator = new CollisionValidator();
  private lightingSystem = new LightingSystem();

  async run(
    room: Room,
    items: Furniture[],
    constraints: LayoutConstraints,
    options?: {
      alternativeGenerator?: AlternativeLayoutGenerator; // AI integration
      lightingPhotos?: Uint8Array[];
      lightingEstimator?: (photos: Uint8Array[]) => Promise<LightingParameters>;
    }
  ): Promise<PipelineResult> {
    const errors: string[] = [];

    // 1) Layout
    let layout: LayoutResult;
    try {
      layout = options?.alternativeGenerator
        ? await options.alternativeGenerator(room, items, constraints)
        : this.layoutEngine.generate(room, items, constraints);
    } catch (err) {
      errors.push(`Layout generation failed: ${(err as Error).message}`);
      layout = { positions: [], warnings: [] };
    }

    // 2) Collision validation — ALWAYS runs on the layout, whether it
    //    came from the own engine or from the AI model (never trusted blindly).
    let validation: ValidationResult;
    try {
      validation = this.collisionValidator.validate(items, layout.positions);
    } catch (err) {
      errors.push(`Collision validation failed: ${(err as Error).message}`);
      validation = { valid: false, collisions: [] };
    }

    // 3) Clear path — only meaningful if there are no collisions.
    let clearPathResult: ClearPathResult = { valid: false, blockedDoors: [] };
    if (validation.valid) {
      try {
        clearPathResult = calculateClearPath(room, items, layout.positions);
      } catch (err) {
        errors.push(`Clear path calculation failed: ${(err as Error).message}`);
      }
    }

    // 4) Lighting — never throws, always returns a usable result.
    const lighting = await this.lightingSystem.estimateFromPhotos(
      options?.lightingPhotos ?? [],
      options?.lightingEstimator
    );

    const readyToRender = validation.valid && clearPathResult.valid && errors.length === 0;

    return { layout, validation, clearPath: clearPathResult, lighting, readyToRender, errors };
  }
}
