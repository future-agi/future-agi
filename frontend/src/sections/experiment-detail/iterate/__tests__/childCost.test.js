import { describe, expect, it } from "vitest";
import { cappedPerTry, toolModeFactors, toolsWithModes } from "../childCost";
import { previewOf } from "../iterateModel";

const tools = (mode, n = 6) =>
  Array.from({ length: n }, (_, i) => ({ name: `t${i}`, mode }));

describe("childCost (L3 G9 fix 9)", () => {
  it("Mock -> Live doubles the time and adds a third to the cost", () => {
    const f = toolModeFactors(tools("mock"), tools("live"));
    expect(f.latency).toBeCloseTo(2, 5);
    expect(f.cost).toBeCloseTo(4 / 3, 5);
  });
  it("Recorded takes time but costs nothing; Live -> Mock reverses", () => {
    const r = toolModeFactors(tools("mock"), tools("recorded"));
    expect(r.latency).toBeCloseTo(2, 5);
    expect(r.cost).toBe(1);
    const back = toolModeFactors(tools("live"), tools("mock"));
    expect(back.latency).toBeCloseTo(0.5, 5);
    expect(back.cost).toBeCloseTo(0.75, 5);
  });
  it("no change and mismatched lists change nothing", () => {
    expect(toolModeFactors(tools("mock"), tools("mock"))).toEqual({
      latency: 1,
      cost: 1,
    });
    expect(toolModeFactors(tools("mock", 2), tools("live", 3))).toEqual({
      latency: 1,
      cost: 1,
    });
  });
  it("toolsWithModes applies the draft's modes by name", () => {
    expect(
      toolsWithModes(tools("mock", 2), { t1: "live" }).map((t) => t.mode),
    ).toEqual(["mock", "live"]);
  });
  it("a cap bounds the per-try cost", () => {
    expect(cappedPerTry(0.2, 0.08)).toBe(0.08);
    expect(cappedPerTry(0.05, 0.08)).toBe(0.05);
    expect(cappedPerTry(0.05, null)).toBe(0.05);
  });
  it("a prompt / agent estimate ignores tool modes (the engine prices tokens)", () => {
    const base = {
      rows: 10,
      trials: 2,
      kind: "prompt",
      costPerAttemptUsd: 0.03,
    };
    const mock = previewOf({
      ...base,
      fromTools: tools("live"),
      toTools: tools("mock"),
    });
    const live = previewOf({
      ...base,
      fromTools: tools("live"),
      toTools: tools("live"),
    });
    expect(mock.usd).toBe(live.usd);
  });
  it("the dock's estimate moves with tool modes and the cap", () => {
    const base = {
      rows: 10,
      trials: 2,
      kind: "harness",
      costPerAttemptUsd: 0.03,
    };
    const mock = previewOf({
      ...base,
      fromTools: tools("mock"),
      toTools: tools("mock"),
    });
    const live = previewOf({
      ...base,
      fromTools: tools("mock"),
      toTools: tools("live"),
    });
    expect(live.usd).toBeGreaterThan(mock.usd);
    const capped = previewOf({ ...base, capUsd: 0.01 });
    expect(capped.usd).toBeLessThan(mock.usd);
  });
});
