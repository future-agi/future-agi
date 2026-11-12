import { describe, expect, it } from "vitest";
import {
  deltaInfo,
  passesEveryEvalText,
  reliableText,
  successCi,
  successText,
} from "../allRunsModel";
import { displayCost, displayMs, shortRunName } from "../verdictItems";

const B = {
  success_rate: 59 / 90,
  success_pass: 59,
  success_n: 90,
  success_tasks: 30,
  trials: 3,
  reliable_rate: 17 / 30,
  reliable_pass: 17,
  reliable_n: 30,
  pass_rate: 25 / 30,
  pass_rate_pass: 25,
  pass_rate_n: 30,
  pass_rate_evals: 2,
};

describe("allRunsModel", () => {
  it("deltas are rounded once from the unrounded values, glyph + tone", () => {
    // L2 G2 R4 (consistency #2): 81/89 − 59/90 = 25.46 pts → +25.5.
    expect(deltaInfo("pct", 81 / 89, 59 / 90)).toMatchObject({
      text: "+25.5 pts",
      dir: "up",
      glyph: "▲",
      tone: "good",
    });
    expect(deltaInfo("pct", 0.6561, 0.656)).toMatchObject({
      text: "±0.0 pts",
      dir: "flat",
      glyph: "",
      tone: "neutral",
    });
    expect(
      deltaInfo("cost", 0.21005, 0.011, { higherIsBetter: false }),
    ).toMatchObject({ text: "+$0.19899999999999998", tone: "bad", glyph: "▲" });
    expect(
      deltaInfo("cost", 0.0111, 0.011, { higherIsBetter: false }),
    ).toMatchObject({ text: "same", tone: "neutral" });
    expect(
      deltaInfo("ms", 6093.7, 9833.7, { higherIsBetter: false }),
    ).toMatchObject({ text: "−3.7 s", tone: "good", glyph: "▼" });
    // L2 G2 R4 (consistency #3): rounded once from the raw p50s (64.5 s),
    // the same number as the sentence's `65 s slower`.
    expect(deltaInfo("ms", 74346, 9833.7)).toMatchObject({ text: "+65 s" });
    expect(deltaInfo("ms", 9900, 9800)).toMatchObject({ text: "same" });
    expect(deltaInfo("pct", null, 0.5)).toBeNull();
  });

  it("cell text + tooltips state the formula with this run's numbers", () => {
    expect(successText(B)).toEqual({
      text: "65.6%",
      tip: "65.6% (59/90): 59 of 90 trials passed across 30 tasks. Errored trials count as fails; trials that didn't run are not counted.",
    });
    expect(reliableText(B).text).toBe("56.7%");
    expect(reliableText(B).tip).toBe(
      "17 of 30 tasks passed on all 3 tries = 56.7%.",
    );
    expect(reliableText({ trials: 1 }).text).toBe("—");
    expect(passesEveryEvalText(B).tip).toContain(
      "25 of 30 scored rows pass all 2 evals = 83.3%",
    );
    expect(successText({ status: "Running" })).toEqual({
      text: "—",
      tip: "Not scored yet: the run is still going.",
    });
    expect(successCi(B)).toBe("55.3–74.6%");
  });

  it("display rounding matches fmt", () => {
    expect(displayMs(9833.7)).toBe(9800);
    expect(displayMs(6093.7)).toBe(6100);
    expect(displayMs(842.4)).toBe(842);
    expect(displayCost(0.21005)).toBe(0.21);
    expect(displayCost(0.004002)).toBe(0.004);
    expect(displayCost(6.304)).toBe(6.3);
    expect(shortRunName("Claude Code × claude-sonnet-4-5")).toBe("Claude Code");
  });
});
