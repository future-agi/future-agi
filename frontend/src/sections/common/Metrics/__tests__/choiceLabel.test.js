// A choice evaluator's row value reads as its label. The server stores a
// choice answer as its score (`Pass` -> 1 with choice_scores
// {Pass: 1, Fail: 0}); the results grid, matrix and drawer showed `1`.
// A row's value is the mean of its scored tries, so a middle score names a
// label only for a single try; aggregates always keep their number.
import { describe, expect, it } from "vitest";
import { choiceLabel, choiceRowText, formatValue } from "../metricFormat";

const passFail = {
  key: "metric-judge",
  name: "judge",
  kind: "eval",
  output_type: "choice",
  higher_is_better: null,
  choice_scores: { Pass: 1, Fail: 0 },
};
const graded = {
  ...passFail,
  choice_scores: { Good: 1, Partial: 0.5, Bad: 0 },
};

describe("a choice evaluator's row value", () => {
  it("reads its label, not its stored score", () => {
    expect(formatValue(passFail, 1, { agg: "value" })).toBe("Pass");
    expect(formatValue(passFail, 0, { agg: "value" })).toBe("Fail");
    expect(choiceRowText(passFail, { value: 1, n: 1 })).toBe("Pass");
    expect(choiceRowText(passFail, { value: 0, n: 3 })).toBe("Fail");
  });

  it("names a middle choice for one try only", () => {
    expect(choiceRowText(graded, { value: 0.5, n: 1 })).toBe("Partial");
    // Two tries averaging 0.5 may be Good + Bad: keep the number.
    expect(choiceRowText(graded, { value: 0.5, n: 2 })).toBe("0.50");
    // An unknown try count is treated the same way.
    expect(formatValue(graded, 0.5, { agg: "value" })).toBe("0.50");
    expect(choiceLabel(graded, 0.5)).toBeNull();
    // Every try gave the top or bottom choice.
    expect(choiceRowText(graded, { value: 1, n: 2 })).toBe("Good");
    expect(choiceRowText(graded, { value: 0, n: 4 })).toBe("Bad");
  });

  it("keeps the number when no single choice owns the value", () => {
    expect(choiceRowText(passFail, { value: 0.5, n: 2 })).toBe("0.50");
    const shared = { ...passFail, choice_scores: { Yes: 1, Sure: 1, No: 0 } };
    expect(choiceRowText(shared, { value: 1, n: 1 })).toBe("1");
    expect(choiceRowText(shared, { value: 0, n: 1 })).toBe("No");
  });

  it("keeps aggregates and other evaluators as they were", () => {
    expect(formatValue(passFail, 1)).toBe("1");
    expect(formatValue(passFail, 1, { agg: "avg" })).toBe("1");
    expect(formatValue(passFail, 0.5, { agg: "avg" })).toBe("0.50");
    // No map (the server could not prove which scores the run used).
    const noMap = { ...passFail, choice_scores: null };
    expect(formatValue(noMap, 1, { agg: "value" })).toBe("1");
    expect(choiceRowText(noMap, { value: 1, n: 1 })).toBe("1");
    // A score evaluator is not a choice evaluator.
    const score = { ...passFail, output_type: "score" };
    expect(formatValue(score, 1, { agg: "value" })).toBe("1.00");
    expect(choiceRowText(score, { value: 1, n: 1 })).toBeUndefined();
    expect(choiceRowText(passFail, { value: null, n: 0 })).toBeUndefined();
  });
});
