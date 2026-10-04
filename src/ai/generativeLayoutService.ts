import type { Room } from "../domain/Room";
import type { Furniture } from "../domain/Furniture";
import type { LayoutConstraints, LayoutResult } from "../domain/LayoutEngine";
import { validateAIResponse, AILayoutOutput } from "./contracts";

export interface AILayoutServiceConfig {
  url: string;
  apiKey?: string;
}

/**
 * Alternative implementation of LayoutEngine.generate() using the
 * generative AI model, selectable via a flag in DecorationPipeline
 * (options.alternativeGenerator).
 *
 * Important: the response is validated against the schema, but
 * DecorationPipeline still runs it through CollisionValidator
 * afterwards — this function's output must NEVER be treated as
 * already physically validated.
 */
export function createAILayoutGenerator(config: AILayoutServiceConfig) {
  return async function generateWithAI(_room: Room, items: Furniture[], constraints: LayoutConstraints): Promise<LayoutResult> {
    const response = await fetch(config.url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
      },
      body: JSON.stringify({ items: items.map((m) => m.toJSON()), constraints }),
    });

    if (!response.ok) {
      throw new Error(`AI layout service responded with HTTP ${response.status}`);
    }

    const json = await response.json();
    const output = validateAIResponse(AILayoutOutput, json, "generative layout service");

    return { positions: output.positions, warnings: [] };
  };
}
