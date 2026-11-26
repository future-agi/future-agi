import { describe, it, expect } from "vitest";
import { generatedPool, getRows } from "../scenarioPool";
import { MOCK_WORLD } from "../world";

describe("scenarioPool", () => {
  it("length is rules + traps + adversarial + edge + core", () => {
    const env = MOCK_WORLD;
    const perKind = ["rules", "traps", "adversarial", "edge", "core"].reduce(
      (sum, kind) => sum + getRows(`${env.id}::${kind}`, env).length,
      0,
    );
    expect(generatedPool(env).length).toBe(perKind);
  });

  it("has the concrete count the fixture produces (81 for MOCK_WORLD)", () => {
    // 12 tools × 4 core + 5 rules × 3 + 4 traps × 3 + 3 adversarial + 3 edge.
    expect(generatedPool(MOCK_WORLD).length).toBe(81);
  });

  it("gives every row a unique id", () => {
    const ids = generatedPool(MOCK_WORLD).map((r) => r.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("gives every row a persona, useCase and expected", () => {
    generatedPool(MOCK_WORLD).forEach((row) => {
      expect(row.persona).toBeTruthy();
      expect(typeof row.useCase).toBe("string");
      expect(row.useCase.length).toBeGreaterThan(0);
      expect(typeof row.expected).toBe("string");
      expect(row.expected.length).toBeGreaterThan(0);
    });
  });

  it("is deterministic — same input gives the same output", () => {
    expect(generatedPool(MOCK_WORLD)).toEqual(generatedPool(MOCK_WORLD));
  });

  it("MOCK_WORLD tools each carry a string description", () => {
    expect(MOCK_WORLD.tools.length).toBeGreaterThan(0);
    MOCK_WORLD.tools.forEach((tool) => {
      expect(typeof tool.desc).toBe("string");
      expect(tool.desc.length).toBeGreaterThan(0);
    });
  });

  it("returns an empty pool for a missing env", () => {
    expect(generatedPool(null)).toEqual([]);
  });
});
