import { describe, expect, it } from "vitest";
import { barPercent, dayLabel, dayX, parseWindow, projectCost, stackAreas, toolLabel } from "./chart";

const day = (out: number, inn: number, emb: number) => ({ calls: 1, output_tokens: out, input_tokens: inn, embedding_tokens: emb });

describe("parseWindow", () => {
  it("accepts the three windows and falls back to 30", () => {
    expect(parseWindow("7")).toBe(7);
    expect(parseWindow("90")).toBe(90);
    expect(parseWindow("")).toBe(30);
    expect(parseWindow("14")).toBe(30);
    expect(parseWindow("abc")).toBe(30);
  });
});

describe("stackAreas", () => {
  it("has no layers for an empty window or all-zero days", () => {
    expect(stackAreas([], 100, 50).layers).toEqual([]);
    expect(stackAreas([day(0, 0, 0), day(0, 0, 0)], 100, 50)).toEqual({ layers: [], max: 0 });
  });

  it("stacks embeddings, args and context bottom to top with the largest day at the top", () => {
    const { layers, max } = stackAreas([day(60, 30, 10), day(20, 10, 10)], 100, 100);
    expect(max).toBe(100);
    expect(layers.map((l) => l.key)).toEqual(["embedding_tokens", "input_tokens", "output_tokens"]);
    // bottom layer: top edge at 10 and 10 of 100, base on the floor
    expect(layers[0]?.path).toBe("M0,90 L100,90 L100,100 L0,100 Z");
    // top layer: day 1 reaches the full 100
    expect(layers[2]?.path).toBe("M0,0 L100,60 L100,80 L0,60 Z");
  });

  it("keeps a single day drawable", () => {
    const { layers } = stackAreas([day(10, 0, 0)], 100, 50);
    expect(layers).toHaveLength(3);
    for (const l of layers) expect(l.path).toMatch(/^M[\d.,\- L]+Z$/);
    expect(dayX(0, 1, 100)).toBe(50);
  });
});

describe("helpers", () => {
  it("drops the context_ prefix", () => {
    expect(toolLabel("context_retrieve")).toBe("retrieve");
    expect(toolLabel("other")).toBe("other");
  });

  it("splits the embedding cost by embedding tokens", () => {
    const totals = { calls: 0, input_tokens: 0, output_tokens: 0, embedding_tokens: 1000, embedding_cost_usd: 0.02 };
    expect(projectCost(totals, 250)).toBeCloseTo(0.005);
    expect(projectCost({ ...totals, embedding_tokens: 0 }, 0)).toBe(0);
  });

  it("scales bars against the max with a visible floor", () => {
    expect(barPercent(50, 100)).toBe(50);
    expect(barPercent(0, 100)).toBe(0);
    expect(barPercent(1, 1000)).toBe(3);
    expect(barPercent(5, 0)).toBe(0);
  });

  it("labels days in UTC", () => {
    expect(dayLabel("2026-10-05")).toBe("Oct 5");
    expect(dayLabel("nope")).toBe("nope");
  });
});
