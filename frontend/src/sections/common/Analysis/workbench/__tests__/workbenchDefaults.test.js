import { describe, expect, it } from "vitest";
import { defaultRowsAxis, sampleText } from "../workbenchModel";

const row = (category, difficulty) => ({
  group_values: { category, difficulty },
});

describe("defaultRowsAxis", () => {
  it("groups by Category when it splits the tasks", () => {
    const matrix = {
      rows: [
        row("refund", "easy"),
        row("refund", "hard"),
        row("order", "easy"),
      ],
    };
    expect(defaultRowsAxis(matrix)).toBe("meta:category");
  });
  it("falls back to Difficulty, then None", () => {
    expect(
      defaultRowsAxis({
        rows: [row("x", "easy"), row("x", "hard"), row("x", "easy")],
      }),
    ).toBe("meta:difficulty");
    expect(defaultRowsAxis({ rows: [row("x"), row("x")] })).toBe("none");
    expect(defaultRowsAxis(undefined)).toBe("none");
  });
});

describe("sampleText", () => {
  it("says rows, and trials when they differ", () => {
    expect(sampleText({ rows: 30, n: 90 })).toBe("30 rows · 90 trials");
    expect(sampleText({ rows: 30, n: 30 })).toBe("30 rows");
    expect(sampleText({ rows: 1, n: 1 })).toBe("1 row");
    expect(sampleText({ rows: 248, n: 1240 })).toBe("248 rows · 1,240 trials");
  });
});
