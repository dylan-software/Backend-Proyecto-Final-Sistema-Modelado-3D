import { describe, it, expect, vi, afterEach } from "vitest";
import { runSceneAssistant } from "../src/ai/sceneAssistant";
import { Room } from "../src/domain/Room";
import type { SpaceRepository } from "../src/db/SpaceRepository";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const config = { apiKey: "test-key", model: "openai/gpt-4o-mini", url: "https://openrouter.test/chat" };

function fakeSpaceRepo(overrides: Partial<SpaceRepository> = {}): SpaceRepository {
  return {
    getShape: vi.fn(async () => new Room([{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 5 }, { x: 0, y: 5 }], 2.6)),
    listPlacedItems: vi.fn(async () => [{ id: 1, furnitureId: "f1", name: "Sofa", x: 1, y: 1, rotation: 0 }]),
    ...overrides,
  } as unknown as SpaceRepository;
}

describe("runSceneAssistant", () => {
  it("calls get_space_shape and get_space_items when the model requests them, then returns the final answer", async () => {
    const repo = fakeSpaceRepo();
    let call = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call++;
        if (call === 1) {
          return {
            ok: true,
            json: async () => ({
              choices: [
                {
                  message: {
                    role: "assistant",
                    content: null,
                    tool_calls: [
                      { id: "call_1", type: "function", function: { name: "get_space_shape", arguments: "{}" } },
                      { id: "call_2", type: "function", function: { name: "get_space_items", arguments: "{}" } },
                    ],
                  },
                },
              ],
            }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            choices: [{ message: { role: "assistant", content: "Your 4x5m room has a sofa near the corner." } }],
          }),
        };
      })
    );

    const reply = await runSceneAssistant(config, "proj-1", repo, [{ role: "user", content: "How should I organize my room?" }]);

    expect(repo.getShape).toHaveBeenCalledWith("proj-1");
    expect(repo.listPlacedItems).toHaveBeenCalledWith("proj-1");
    expect(reply.content).toContain("4x5m room");
  });

  it("never lets the model see images — the system prompt explicitly denies that capability", async () => {
    const repo = fakeSpaceRepo();
    let capturedMessages: any[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: any) => {
        capturedMessages = JSON.parse(init.body).messages;
        return { ok: true, json: async () => ({ choices: [{ message: { role: "assistant", content: "ok" } }] }) };
      })
    );

    await runSceneAssistant(config, "proj-1", repo, [{ role: "user", content: "hi" }]);

    const systemMessage = capturedMessages.find((m) => m.role === "system");
    expect(systemMessage.content).toMatch(/do NOT receive camera images/i);
  });

  it("surfaces a clear error when OPENROUTER_API_KEY is missing, instead of crashing", async () => {
    const repo = fakeSpaceRepo();
    await expect(
      runSceneAssistant({ ...config, apiKey: "" }, "proj-1", repo, [{ role: "user", content: "hi" }])
    ).rejects.toThrow(/OPENROUTER_API_KEY/);
  });

  it("stops after MAX_TOOL_ROUNDS instead of looping forever if the model never stops calling tools", async () => {
    const repo = fakeSpaceRepo();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          choices: [
            {
              message: {
                role: "assistant",
                content: null,
                tool_calls: [{ id: "call_x", type: "function", function: { name: "get_space_items", arguments: "{}" } }],
              },
            },
          ],
        }),
      }))
    );

    await expect(
      runSceneAssistant(config, "proj-1", repo, [{ role: "user", content: "hi" }])
    ).rejects.toThrow(/exceeded/i);
  });
});
