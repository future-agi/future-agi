import { describe, expect, it } from "vitest";
import { hash32, mulberry32, shuffled } from "../seededRandom";

describe("deterministic sampling", () => {
  it("preserves hash vectors used by existing sample selections", () => {
    expect(["", "a", "row|57", "row|58"].map(hash32)).toEqual([
      2872998923, 444641715, 1356583532, 3162187244,
    ]);
  });
  it("preserves the seeded generator sequence", () => {
    expect(Array.from({ length: 5 }, mulberry32(42))).toEqual([
      0.6011037519201636, 0.44829055899754167, 0.8524657934904099,
      0.6697340414393693, 0.17481389874592423,
    ]);
  });
  it("shuffles a copy with the same deterministic order", () => {
    const values = [1, 2, 3, 4, 5];
    expect(shuffled(values, "sample")).toEqual([2, 5, 3, 4, 1]);
    expect(values).toEqual([1, 2, 3, 4, 5]);
    expect(shuffled([], "sample")).toEqual([]);
    expect(shuffled([1], "sample")).toEqual([1]);
  });
});
