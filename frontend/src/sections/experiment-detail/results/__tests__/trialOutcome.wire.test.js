import { describe, expect, it } from "vitest";
import { trialOutcome } from "../resultsSimple";
import cases from "./trialOutcome.fixture.json";

// The Python projection test checks these same score objects against both
// full-row and compact grid responses built through result_cell.
describe("outcome badges from canonical aggregate scores", () => {
  it.each(cases)("renders $name", ({ score, outcome }) => {
    expect(trialOutcome({ scores: { verdict: score } }, "verdict")).toEqual(
      outcome,
    );
  });

  it("normalizes verdicts without collapsing unscored positions", () => {
    const result = {
      scores: {
        verdict: {
          trials: [0, null, "Pass", 0.5, "Fail", undefined, 1, "pending"],
        },
      },
    };
    expect(trialOutcome(result, "verdict")).toEqual({
      pass: 2,
      n: 4,
      tone: "flaky",
      label: "2/4 trials",
      dots: [false, true, false, true],
      verdicts: [false, null, true, null, false, null, true, null],
    });
  });

  it.each([0, false, "fail", "Fail"])(
    "retains a scalar failing verdict from %s",
    (value) => {
      expect(
        trialOutcome({ scores: { verdict: { value } } }, "verdict"),
      ).toEqual({
        pass: 0,
        n: 1,
        tone: "fail",
        label: "Fail",
        dots: [false],
        verdicts: [false],
      });
    },
  );

  it("does not invent verdicts when the metric is absent", () => {
    expect(trialOutcome({}, "verdict")).toEqual({
      pass: 0,
      n: 0,
      tone: "none",
      label: "Not scored",
      dots: [],
      verdicts: [],
    });
  });
});
