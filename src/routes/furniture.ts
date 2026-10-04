import { Router } from "express";
import { z } from "zod";
import { services } from "../db/services";
import { handleSimilarFurniture } from "../ai/embeddingsService";

export const router = Router();

/** GET /api/furniture — lists the catalog (optional ?category= filter) */
router.get("/", async (req, res) => {
  try {
    const category = typeof req.query.category === "string" ? req.query.category : undefined;
    const items = await services.furniture.list({ category });
    res.status(200).json({ items });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

const CreateFurnitureBody = z.object({
  id: z.string(),
  name: z.string().min(1, "The object name is required"),
  widthCm: z.number().positive(),
  heightCm: z.number().positive(),
  depthCm: z.number().positive(),
  category: z.string(),
  material: z.string(),
  price: z.number().nonnegative(),
  style: z.string(),
  createdManually: z.boolean().optional(),
});

/**
 * POST /api/furniture — creates a new catalog item.
 * Used both by the "official" catalog and by MANUALLY created objects
 * from the app (requires `name`, as requested).
 */
router.post("/", async (req, res) => {
  const parsed = CreateFurnitureBody.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: "Invalid object data", details: parsed.error.issues });
  }
  try {
    await services.furniture.create({ ...parsed.data, category: parsed.data.category as any });
    res.status(201).json({ ok: true, item: parsed.data });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

/** GET /api/furniture/similar?furnitureId=...&limit=5 */
router.get("/similar", async (req, res) => {
  const furnitureId = typeof req.query.furnitureId === "string" ? req.query.furnitureId : undefined;
  if (!furnitureId) return res.status(400).json({ error: "Missing furnitureId parameter" });
  const limit = Number(req.query.limit ?? 5);

  try {
    const similar = await handleSimilarFurniture(
      furnitureId,
      limit,
      services.furniture,
      services.embeddings,
      services.aiConfig.embeddings
    );
    res.status(200).json({ similar });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});
