import { Router } from "express";
import { z } from "zod";
import { services } from "../db/services";
import { runSceneAssistant } from "../ai/sceneAssistant";

export const router = Router();

const ChatBody = z.object({
  projectId: z.string(),
  messages: z
    .array(
      z.object({
        role: z.enum(["user", "assistant"]),
        content: z.string(),
      })
    )
    .min(1),
});

/**
 * POST /api/chat
 * Runs the AI organization assistant (OpenRouter) for a given project,
 * with real access to the database (room shape + placed items) through
 * tool calls — see src/ai/sceneAssistant.ts for the exact contract.
 */
router.post("/", async (req, res) => {
  const parsed = ChatBody.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: "Invalid chat request", details: parsed.error.issues });
  }

  try {
    const reply = await runSceneAssistant(
      services.openRouterConfig,
      parsed.data.projectId,
      services.spaces,
      parsed.data.messages.map((m) => ({ role: m.role, content: m.content }))
    );
    res.status(200).json({ role: "assistant", content: reply.content ?? "" });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});
