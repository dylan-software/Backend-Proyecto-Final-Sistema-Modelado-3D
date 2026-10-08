import { callOpenRouter, type ChatMessage, type ToolDefinition, type OpenRouterConfig } from "./openRouterChat";
import type { SpaceRepository } from "../db/SpaceRepository";

const MAX_TOOL_ROUNDS = 4;

const TOOLS: ToolDefinition[] = [
  {
    type: "function",
    function: {
      name: "get_space_shape",
      description:
        "Returns the shape of the user's room: its polygon vertices (in meters), ceiling height, and floor area. " +
        "Use this to understand the size and layout of the space before recommending furniture placement.",
      parameters: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_space_items",
      description:
        "Returns the list of objects/furniture currently placed in the user's space, with their names and (x,y) " +
        "positions in meters. Use this to know what the room already contains before giving organization advice.",
      parameters: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
    },
  },
];

const SYSTEM_PROMPT = `You are an interior-organization assistant embedded in a 3D room-decoration app.

Important and non-negotiable: you do NOT receive camera images or a rendered picture of the room. You can only
"see" the space through the two tools available to you, which return structured data (the room's polygon vertices
in meters, and the list of placed items with their positions). Never claim to have looked at a photo, image, or
render of the space — describe what you know strictly in terms of the structured data the tools returned.

When asked for organization recommendations, call get_space_shape and get_space_items first if you have not
already done so in this conversation, then reason about the room's real dimensions and what is already placed
before suggesting changes. Be concrete: reference actual measurements and item names from the tool results.`;

/**
 * Runs the OpenRouter chat loop for one project: the model can call
 * get_space_shape / get_space_items (resolved against the real database
 * through SpaceRepository) as many times as it needs, up to
 * MAX_TOOL_ROUNDS, before producing its final answer.
 */
export async function runSceneAssistant(
  config: OpenRouterConfig,
  projectId: string,
  spaceRepo: SpaceRepository,
  conversation: ChatMessage[]
): Promise<ChatMessage> {
  const messages: ChatMessage[] = [{ role: "system", content: SYSTEM_PROMPT }, ...conversation];

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const reply = await callOpenRouter(config, messages, TOOLS);
    messages.push(reply);

    if (!reply.tool_calls || reply.tool_calls.length === 0) {
      return reply;
    }

    for (const call of reply.tool_calls) {
      const result = await executeTool(call.function.name, projectId, spaceRepo);
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        name: call.function.name,
        content: JSON.stringify(result),
      });
    }
  }

  throw new Error(`Scene assistant exceeded ${MAX_TOOL_ROUNDS} tool-call rounds without a final answer`);
}

async function executeTool(name: string, projectId: string, spaceRepo: SpaceRepository): Promise<unknown> {
  if (name === "get_space_shape") {
    const room = await spaceRepo.getShape(projectId);
    if (!room) return { error: "No space has been scanned for this project yet." };
    return { vertices: room.getVertices(), heightM: room.getHeightM(), areaM2: room.getAreaM2() };
  }

  if (name === "get_space_items") {
    const items = await spaceRepo.listPlacedItems(projectId);
    return { items };
  }

  return { error: `Unknown tool: ${name}` };
}
