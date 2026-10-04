import { describe, it, expect, vi, afterEach } from "vitest";
import { segmentSpace } from "../src/ai/segmentationService";
import { detectPlanesRANSAC } from "../src/ai/ransacFallback";
import { validateAIResponse, EmbeddingOutput } from "../src/ai/contracts";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("detectPlanesRANSAC", () => {
  it("detects the dominant plane (floor) of a point cloud", () => {
    const points: [number, number, number][] = [
      [0, 0, 0],
      [1, 0, 0],
      [0, 1, 0],
      [1, 1, 0],
      [0.5, 0.5, 0],
      [0, 0, 2.6], // a "ceiling" point to give some range in Z
    ];
    const result = detectPlanesRANSAC(points);
    expect(result.floorZ).toBeCloseTo(0, 1);
    expect(result.ceilingZ).toBeCloseTo(2.6, 1);
  });
});

function samplePointCloud(): [number, number, number][] {
  const points: [number, number, number][] = [];
  for (let x = 0; x < 10; x++) {
    for (let y = 0; y < 12; y++) {
      points.push([x * 0.3, y * 0.3, 0]);
    }
  }
  points.push([0, 0, 2.6]);
  return points;
}

describe("segmentSpace — fallback when the AI API fails", () => {
  it("uses RANSAC when the API responds with an HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }));

    const result = await segmentSpace(samplePointCloud(), { url: "https://invalid-ai-example.test/segment" });

    expect(result.source).toBe("ransac_fallback");
  });

  it("uses RANSAC when the API returns JSON that does not match the schema", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ wrongField: true }) }));

    const result = await segmentSpace(samplePointCloud(), { url: "https://invalid-ai-example.test/segment" });

    expect(result.source).toBe("ransac_fallback");
  });

  it("uses the AI response when it is valid", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ walls: [], floorZ: 0, ceilingZ: 2.6 }) })
    );

    const result = await segmentSpace(samplePointCloud(), { url: "https://invalid-ai-example.test/segment" });

    expect(result.source).toBe("ai");
    expect(result.ceilingZ).toBe(2.6);
  });
});

describe("validateAIResponse", () => {
  it("names exactly which field failed, not a generic error", () => {
    try {
      validateAIResponse(EmbeddingOutput, { vector: [1, 2, 3] }, "CLIP embeddings service");
      expect.unreachable("should have thrown for a wrong vector length");
    } catch (err) {
      expect((err as Error).message).toContain("vector");
      expect((err as Error).message).toContain("CLIP embeddings service");
    }
  });
});
