// W1-9: the tree's eval chips read the scorer's verdict; verdicts with no
// score (skipped, N/A, score error, not reached) are neither pass nor fail.
import { describe, expect, it } from "vitest";
import { collectSubtreeEvals, evalPassOf, evalStatusWord } from "../evalScores";

describe("evalPassOf / evalStatusWord", () => {
  it("a boolean result wins over the 50 cut (a 65 under a 0.7 threshold fails)", () => {
    expect(evalPassOf({ score: 65, result: false })).toBe(false);
    expect(evalPassOf({ score: 40, result: true })).toBe(true);
  });
  it("without a result, score >= 50 passes", () => {
    expect(evalPassOf({ score: 50 })).toBe(true);
    expect(evalPassOf({ score: 49 })).toBe(false);
  });
  it("no verdict: null, and the word says why", () => {
    expect(evalPassOf({ score: null, result: null, status: "na" })).toBeNull();
    expect(evalStatusWord({ status: "na" })).toBe("N/A");
    expect(evalStatusWord({ error: true })).toBe("Score error");
    expect(evalStatusWord({ status: "not_reached" })).toBe("Not reached");
    expect(
      evalStatusWord({
        status: "skipped",
        skipped_reason: "no expected value",
      }),
    ).toBe("Skipped");
    expect(evalStatusWord({})).toBe("—");
  });
});

describe("collectSubtreeEvals (review S12)", () => {
  it("counts verdicts only: 2 failures beside 4 never-ran checks read 0/2", () => {
    const tree = {
      observation_span: { id: "root" },
      eval_scores: [{ eval_name: "Task success", score: 0, result: false }],
      children: [
        {
          observation_span: { id: "s1" },
          eval_scores: [
            { eval_name: "refund_amount", score: 0, result: false },
            ...[1, 2, 3, 4].map((i) => ({
              eval_name: `c${i}`,
              score: null,
              result: null,
              status: "not_reached",
            })),
          ],
          children: [],
        },
      ],
    };
    expect(collectSubtreeEvals(tree)).toEqual({ pass: 0, fail: 2, total: 2 });
  });
});
