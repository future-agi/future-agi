/** options-truth L2 G9: the Rerun dialog's harness limits (pure parts). */
import { describe, expect, it } from "vitest";
import { budgetChangeLine, budgetOf } from "../RerunDialogParts";

describe("budgetOf", () => {
  it("reads the recipe budget first, then the flat harness limits", () => {
    expect(
      budgetOf({ budget: { max_turns: 40 }, max_turns: 9, timeout_s: 600 }),
    ).toEqual({ max_turns: 40, max_cost_usd: null, timeout_s: 600 });
    expect(budgetOf(null)).toEqual({
      max_turns: null,
      max_cost_usd: null,
      timeout_s: null,
    });
  });
});

describe("budgetChangeLine", () => {
  it("lists only the limits that moved, with units", () => {
    const before = { max_turns: 40, max_cost_usd: 0.5, timeout_s: 600 };
    expect(budgetChangeLine(before, { ...before })).toBe("");
    expect(
      budgetChangeLine(before, {
        max_turns: 12,
        max_cost_usd: 0.2,
        timeout_s: 600,
      }),
    ).toBe("Max turns 40 → 12 · Max cost per try $0.5 → $0.2");
    expect(budgetChangeLine({ timeout_s: null }, { timeout_s: 120 })).toBe(
      "Time limit none → 120 s",
    );
  });
});
