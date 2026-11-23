import { describe, expect, it } from "vitest";
import {
  checklistTally,
  evidenceStepsOf,
  rootCauseRowsOf,
} from "../issueEvidence";

const reel = [
  { label: "User", text: "Asked" },
  { label: "Agent", text: "Looked up" },
  { label: "Tool", text: "Acted", isFailure: true },
  { label: "Agent", text: "Told" },
];

describe("issueEvidence", () => {
  it("reads the stored root cause as rows, sentence-cased", () => {
    const rows = rootCauseRowsOf({
      suggested_fix: { cause: "c", change: "Do x", wrongIf: "it still fails" },
    });
    expect(rows.map((r) => r.label)).toEqual([
      "Likely cause",
      "Suggested change",
      "Wrong if",
    ]);
    expect(rows[2].text).toBe("It still fails");
    expect(rootCauseRowsOf({})).toEqual([]);
  });

  it("derives starts / goes wrong / shows from the failing reel", () => {
    const steps = evidenceStepsOf({ evidence: { fail_reel: reel } });
    expect(steps.map((s) => [s.label, s.index])).toEqual([
      ["Starts", 1],
      ["Goes wrong", 2],
      ["Shows", 3],
    ]);
  });

  it("prefers stored step indexes", () => {
    const steps = evidenceStepsOf({
      evidence: { fail_reel: reel, origin_step: 0, symptom_step: 2 },
    });
    expect(steps.map((s) => s.index)).toEqual([0, 2, 2]);
  });

  it("tallies the checklist", () => {
    expect(
      checklistTally([
        { status: "met" },
        { status: "broken" },
        { status: "unclear" },
      ]),
    ).toBe("1 of 3 done · 1 broken · 1 unclear");
  });
});
