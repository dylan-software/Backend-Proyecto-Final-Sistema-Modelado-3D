import { Router } from "express";
import { z } from "zod";
import { Room } from "../domain/Room";
import { services } from "../db/services";

export const router = Router();

const SpaceBody = z.object({
  userId: z.string(),
  name: z.string().min(1),
  vertices: z.array(z.object({ x: z.number(), y: z.number() })).min(3, "A space needs at least 3 vertices"),
  heightM: z.number().positive(),
});

/**
 * POST /api/spaces/:projectId — saves the shape of the space (irregular
 * polygon traced in AR, not necessarily rectangular). Acts as a full
 * create-or-replace: calling it again on the same project overwrites
 * the whole shape (use PATCH below to move a single vertex instead).
 */
router.post("/:projectId", async (req, res) => {
  const { projectId } = req.params;
  const parsed = SpaceBody.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: "Invalid space data", details: parsed.error.issues });
  }

  try {
    const { userId, name, vertices, heightM } = parsed.data;
    const room = new Room(vertices, heightM);
    await services.spaces.createProject(projectId, userId, name);
    await services.spaces.saveShape(projectId, room);
    res.status(200).json({ ok: true, areaM2: Math.round(room.getAreaM2() * 100) / 100, vertices: room.getVertices() });
  } catch (err) {
    res.status(400).json({ error: (err as Error).message });
  }
});

/** GET /api/spaces/:projectId — retrieves the saved shape of the space. */
router.get("/:projectId", async (req, res) => {
  try {
    const room = await services.spaces.getShape(req.params.projectId);
    if (!room) return res.status(404).json({ error: "No space saved for that project" });
    res.status(200).json({
      vertices: room.getVertices(),
      heightM: room.getHeightM(),
      areaM2: Math.round(room.getAreaM2() * 100) / 100,
    });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

const UpdateVertexBody = z.object({ x: z.number(), y: z.number() });

/**
 * PATCH /api/spaces/:projectId/vertices/:index — moves a single vertex
 * of an already-saved space (drag-to-edit in the UI). This is the
 * endpoint that lets the user modify the room surface after the
 * initial scan without having to resend the whole polygon.
 */
router.patch("/:projectId/vertices/:index", async (req, res) => {
  const index = Number(req.params.index);
  const parsed = UpdateVertexBody.safeParse(req.body);
  if (!Number.isInteger(index) || !parsed.success) {
    return res.status(400).json({ error: "Invalid vertex index or point" });
  }

  try {
    const room = await services.spaces.updateVertex(req.params.projectId, index, parsed.data);
    res.status(200).json({ ok: true, areaM2: Math.round(room.getAreaM2() * 100) / 100, vertices: room.getVertices() });
  } catch (err) {
    res.status(400).json({ error: (err as Error).message });
  }
});

/**
 * GET /api/spaces/:projectId/items — lists the items a space has.
 * This is the route the AI chat panel will use (phase 3).
 */
router.get("/:projectId/items", async (req, res) => {
  try {
    const items = await services.spaces.listPlacedItems(req.params.projectId);
    res.status(200).json({ items });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});
