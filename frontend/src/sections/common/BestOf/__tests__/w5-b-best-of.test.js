// W5 B9: Best of N view words and the cost line (one estimator).
import { describe, expect, it } from "vitest";
import { estimateRun } from "src/sections/common/Analysis/runStats";
import { judgeCallUsd } from "src/sections/develop-detail/Experiment/estimateInputs";
import {
  bestOfCost,
  costLine,
  summaryLine,
  judgedVsStored,
  orderText,
  pickOptions,
  pickValueOf,
  rowsOf,
  scoreText,
  shareText,
  storedNote,
  verdictText,
} from "../bestOfModel";

const L = { a: "#1A", b: "#2B", c: "#3C" };
const labelOf = (id) => L[id];
const judgeEval = {
  key: "eval:tone",
  name: "Tone",
  judge: true,
  judge_model: "gpt-4o-mini",
};
const check = {
  key: "check:x",
  name: "Status",
  judge: false,
  judge_model: null,
};

describe("best-of model", () => {
  it("offers only a highest-stored-score option per eval (one scorer)", () => {
    expect(pickOptions([judgeEval, check]).map((o) => o.label)).toEqual([
      "Highest score on Tone",
      "Highest score on Status",
    ]);
  });
  it("prices judging through runStats.estimateRun at the judge's own price", () => {
    const want = estimateRun({
      rows: 714,
      trials: 1,
      targets: [{ kind: "code" }],
      evals: [{ type: "judge", cost_per_call: judgeCallUsd("gpt-4o-mini") }],
    }).judge_cost_usd;
    expect(bestOfCost(judgeEval, 714)).toBe(want);
    expect(want).toBeGreaterThan(0);
    expect(bestOfCost(check, 714)).toBe(0);
    expect(
      costLine({ mode: "judge", evalOpt: judgeEval, rows: 238, outputs: 714 }),
    ).toMatch(/^Judge 238 rows \(714 outputs\) with gpt-4o-mini · about \$/);
    expect(
      costLine({ mode: "max_score", evalOpt: judgeEval, rows: 1, outputs: 3 }),
    ).toBe("Compare the stored Tone scores of 1 row · no judging, free");
  });
  it("words rows, shares and scores", () => {
    expect(
      verdictText({ winner: "b", tied: [], skipped: false }, labelOf),
    ).toBe("#2B");
    expect(
      verdictText({ winner: "tie", tied: ["a", "c"], skipped: false }, labelOf),
    ).toBe("Tie · #1A, #3C");
    expect(
      verdictText({ winner: null, tied: [], skipped: true }, labelOf),
    ).toMatch(/^Skipped/);
    expect(orderText(["c", "a", "b"], labelOf)).toBe("#3C → #1A → #2B");
    expect(shareText({ share: 0.5, ci: { low: 0.25, high: 0.75 } })).toBe(
      "50.0% (25.0–75.0%)",
    );
    expect(scoreText(1, "pass_fail")).toBe("Pass");
    expect(scoreText(0.817, "score")).toBe("0.82");
    expect(scoreText(null, "score")).toBe("—");
  });
  it("filters rows by winner, ties and skipped", () => {
    const rows = [
      { winner: "a", tied: [] },
      { winner: "tie", tied: ["a", "b"] },
      { winner: null, skipped: true, tied: [] },
    ];
    expect(rowsOf(rows, "a")).toHaveLength(1);
    expect(rowsOf(rows, "tie")).toHaveLength(1);
    expect(rowsOf(rows, "skipped")).toHaveLength(1);
    expect(rowsOf(rows, "all")).toHaveLength(3);
  });
});

// Review M5: a judged value that differs from the output's stored score is
// shown with it, never silently.
describe("the summary line names every run with the most wins (M7)", () => {
  const rec = (shares) => ({
    n: 20,
    ties: 0,
    skipped: 0,
    mode: "max_score",
    shares,
  });
  it("one leader is named alone", () => {
    expect(
      summaryLine(
        rec([
          { run_id: "a", share: 0.6 },
          { run_id: "b", share: 0.4 },
        ]),
        labelOf,
      ),
    ).toContain("most wins: #1A");
  });
  it("equal top shares are named together, never as one winner", () => {
    const line = summaryLine(
      rec([
        { run_id: "a", share: 0.5 },
        { run_id: "b", share: 0.5 },
      ]),
      labelOf,
    );
    expect(line).toContain("even: #1A, #2B share the most wins");
    expect(line).not.toContain("most wins: #1A");
  });
});

describe("a stored result in the picker (M7)", () => {
  it("names the option of the result's own eval and mode", () => {
    const opts = pickOptions([judgeEval, check]).map((o) => o.value);
    const judged = pickValueOf({ mode: "judge", eval_key: "eval:tone" });
    const max = pickValueOf({ mode: "max_score", eval_key: "check:x" });
    expect(judged).toBe("judge:eval:tone");
    expect(max).toBe("max_score:check:x");
    // L7c g6: judge mode is not offered (one scorer); a stored judge
    // result keeps its value but has no picker option.
    expect(opts).not.toContain(judged);
    expect(opts).toContain(max);
    expect(pickValueOf(null)).toBeNull();
    expect(pickValueOf({ mode: "judge" })).toBeNull();
  });
});

describe("judged vs stored", () => {
  const row = {
    scores: { a: 1, b: 0 },
    stored: { a: 0, b: 0 },
    differs: ["a"],
  };
  it("names both values only where they differ", () => {
    expect(judgedVsStored(row, "a", "pass_fail")).toBe(
      "judged now Pass · stored Fail",
    );
    expect(judgedVsStored(row, "b", "pass_fail")).toBeNull();
    expect(judgedVsStored({ scores: { a: 1 } }, "a", "pass_fail")).toBeNull();
    expect(
      judgedVsStored(
        { scores: { a: 0.82 }, stored: { a: 0.4 }, differs: ["a"] },
        "a",
        "score",
      ),
    ).toBe("judged now 0.82 · stored 0.40");
  });
  it("the result note counts the outputs that differ", () => {
    expect(
      storedNote({ mode: "judge", stored_differs: 170, stored_compared: 712 }),
    ).toMatch(/^On 170 of 712 outputs the judge's value now differs/);
    expect(
      storedNote({ mode: "judge", stored_differs: 0, stored_compared: 712 }),
    ).toBeNull();
    expect(storedNote({ mode: "max_score" })).toBeNull();
  });
});
