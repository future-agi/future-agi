// L2 G2 R4 (consistency #8): the footer's match count agrees with the
// banner's differences (milestone rows whose change is not `same`).
import { describe, expect, it } from "vitest";
import { stepsMatchText, trialMetrics } from "../workspaceModel";

const step = (n) => ({ type: "step", step: { n } });
const row = (id, change, kind = "milestone") => ({
  id,
  kind,
  change,
  cells: [step(1), step(1)],
});

describe("stepsMatchText", () => {
  it("never counts a differing row's steps as a match", () => {
    const align = {
      rows: [
        row("m1", "same"),
        row("m2", "same"),
        row("m3", "changed"),
        row("m4", "same"),
        row("m5", "changed"),
        row("m6", "changed"),
        row("m7", "same"),
        row("m8", "same"),
        row("m9", "same"),
      ],
    };
    expect(stepsMatchText(align)).toBe("12 of 18 steps match · 3 rows differ");
  });

  it("names folded one-run steps apart", () => {
    const align = {
      rows: [
        row("m1", "same"),
        { id: "g1", kind: "gap", cells: [{ type: "gap", count: 2 }, null] },
      ],
    };
    expect(stepsMatchText(align)).toBe(
      "2 of 4 steps match · 2 only in one run (folded)",
    );
  });
});

describe("trialMetrics", () => {
  it("N steps counts the terminal step", () => {
    const t = {
      steps: [
        { n: 1, type: "tool_call" },
        { n: 2, type: "llm" },
        { n: 3, type: "end_state" },
      ],
    };
    expect(trialMetrics(t).steps).toBe(3);
    expect(trialMetrics(t).toolCalls).toBe(1);
  });
});
