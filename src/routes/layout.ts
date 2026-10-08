import { Router } from "express";
import { z } from "zod";
import { Room } from "../domain/Room";
import { Furniture } from "../domain/Furniture";
import { DecorationPipeline } from "../domain/DecorationPipeline";
import { LayoutConstraintsSchema } from "../ai/contracts";
import { services } from "../db/services";
import { createAILayoutGenerator } from "../ai/generativeLayoutService";

export const router = Router();

const RequestBody = z.object({
  projectId: z.string(),
  room: z.object({
    vertices: z.array(z.object({ x: z.number(), y: z.number() })).min(3),
    heightM: z.number().positive(),
  }),
  items: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      widthCm: z.number().positive(),
      heightCm: z.number().positive(),
      depthCm: z.number().positive(),
      category: z.string(),
      material: z.string(),
      price: z.number().nonnegative(),
      style: z.string(),
    })
  ),
  constraints: LayoutConstraintsSchema,
  useAI: z.boolean().optional().default(false),
  save: z.boolean().optional().default(true),
});

/**
 * POST /api/layout/generate
 * Runs DecorationPipeline (LayoutEngine or AI -> CollisionValidator ->
 * clear path -> lighting) and, if it ends up ready to render, persists
 * the result as the project's placed items.
 */
router.post("/generate", async (req, res) => {
  const parsed = RequestBody.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: "Invalid body", details: parsed.error.issues });
  }
  const { projectId, room: roomData, items: itemsData, constraints, useAI, save } = parsed.data;

  try {
    const room = new Room(roomData.vertices, roomData.heightM);
    const items = itemsData.map((m) => new Furniture({ ...m, category: m.category as any }));

    const pipeline = new DecorationPipeline();
    const result = await pipeline.run(room, items, constraints, {
      alternativeGenerator: useAI && services.aiConfig.layout.url ? createAILayoutGenerator(services.aiConfig.layout) : undefined,
    });

    if (result.readyToRender && save) {
      await services.spaces.savePlacedItems(projectId, result.layout.positions);
    }

    res.status(200).json(result);
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});
