// W1-8 · trial reducers, intervals and flakiness (pure).
import { describe, expect, it } from "vitest";
import {
  aggOfReducer,
  flakinessOf,
  meanInterval,
  parseReducer,
  rateInterval,
  reduceRow,
  reduceRun,
  reducedText,
  reducerLabel,
} from "../trialReducers";
import { passHatK, wilson } from "../runStats";

describe("W1-8 · reducers of one row's tries", () => {
  const row = [1, 0, 1];
  it("mean, median, mode, max, pass@k, pass^k, at least n", () => {
    expect(reduceRow(row, { kind: "mean" })).toBeCloseTo(2 / 3, 12);
    expect(reduceRow(row, { kind: "median" })).toBe(1);
    expect(reduceRow(row, { kind: "mode" })).toBe(1);
    expect(reduceRow(row, { kind: "max" })).toBe(1);
    expect(reduceRow(row, { kind: "pass_at_k", k: 3 })).toBe(1);
    expect(reduceRow(row, { kind: "pass_hat_k", k: 3 })).toBe(0);
    expect(reduceRow(row, { kind: "at_least", n: 2 })).toBe(1);
    expect(reduceRow(row, { kind: "at_least", n: 3 })).toBe(0);
  });
  it("a tie is a fail under mode (owner decision 6); an errored try is a failed try", () => {
    expect(reduceRow([1, 0], { kind: "mode" })).toBe(0);
    expect(reduceRow([1, "error", 1], { kind: "pass_hat_k", k: 3 })).toBe(0);
    expect(reduceRow([1, "error", 1], { kind: "mean" })).toBeCloseTo(2 / 3, 12);
    // unscored tries are left out; pass^k needs k scored tries
    expect(reduceRow([1, null, 1], { kind: "pass_hat_k", k: 3 })).toBeNull();
    expect(reduceRow([null, "infra"], { kind: "mean" })).toBeNull();
  });
  it("pass@k reads the first k scored tries", () => {
    expect(reduceRow([0, 0, 1], { kind: "pass_at_k", k: 2 })).toBe(0);
    expect(reduceRow([0, 0, 1], { kind: "pass_at_k", k: 3 })).toBe(1);
  });
  it("score evals: median, max and mode of values, pass@k at the bar", () => {
    const s = [0.4, 0.9, 0.4];
    expect(reduceRow(s, { kind: "median" })).toBe(0.4);
    expect(reduceRow(s, { kind: "max" })).toBe(0.9);
    expect(reduceRow(s, { kind: "mode", passFail: false })).toBe(0.4);
    expect(reduceRow(s, { kind: "pass_at_k", k: 3, threshold: 0.7 })).toBe(1);
    expect(reduceRow(s, { kind: "pass_hat_k", k: 3, threshold: 0.7 })).toBe(0);
  });
});

describe("W1-8 · a run's number and its interval", () => {
  const rows = [
    [1, 1, 1],
    [1, 0, 1],
    [0, 0, 0],
    [1, 1, 0],
  ];
  it("pass@3 and pass^3 differ; pass^3 equals runStats.passHatK", () => {
    const at = reduceRun(rows, { kind: "pass_at_k", k: 3 });
    const hat = reduceRun(rows, { kind: "pass_hat_k", k: 3 });
    expect(at.value).toBe(3 / 4);
    expect(hat.value).toBe(1 / 4);
    expect(hat.value).toBe(passHatK(rows, 3).rate);
    // a rate carries the Wilson 95% interval over its rows
    const w = wilson(1 / 4, 4);
    expect(hat.ci95).toEqual([w.low, w.high]);
  });
  it("a score mean carries mean ± 1.96·sd/√n", () => {
    const res = reduceRun(
      [
        [0.2, 0.4],
        [0.8, 0.6],
        [0.5, 0.5],
      ],
      { kind: "mean", passFail: false },
    );
    expect(res.value).toBeCloseTo(0.5, 12);
    const ci = meanInterval([0.3, 0.7, 0.5]);
    expect(res.ci95[0]).toBeCloseTo(ci[0], 12);
    expect(res.ci95[1]).toBeCloseTo(ci[1], 12);
    expect(rateInterval(0.5, 0)).toBeNull();
    expect(meanInterval([1])).toBeNull();
  });
  it("labels and text", () => {
    expect(reducerLabel("pass_hat_k", 3)).toBe("pass^3");
    expect(reducerLabel("pass_at_k", 3)).toBe("pass@3");
    expect(reducerLabel({ kind: "at_least", n: 2 }, 3)).toBe("At least 2 of 3");
    expect(reducerLabel("mode")).toBe("Most common");
    expect(parseReducer("at_least:2")).toEqual({ kind: "at_least", n: 2 });
    expect(parseReducer("avg")).toEqual({ kind: "mean" });
    expect(aggOfReducer({ kind: "at_least", n: 2 })).toBe("at_least:2");
    const hat = reduceRun(rows, { kind: "pass_hat_k", k: 3 });
    expect(reducedText(hat, { k: 3 })).toMatch(/^pass\^3 · 25% \(\d+%–\d+%\)$/);
  });
});

describe("W1-8 · flakiness per row", () => {
  it("1 − |2p − 1|: 0 when every try agrees, 1 on an even split", () => {
    expect(flakinessOf([1, 1, 1]).score).toBe(0);
    expect(flakinessOf([0, 0, 0]).score).toBe(0);
    expect(flakinessOf([1, 0]).score).toBe(1);
    expect(flakinessOf([1, 0, 1]).score).toBeCloseTo(0.667, 3);
    expect(flakinessOf([1])).toBeNull();
    expect(flakinessOf([0.9, 0.1], { threshold: 0.5 }).sd).toBeCloseTo(
      0.566,
      3,
    );
  });
});

describe("W1-8 · interval of a summary (hover cards)", () => {
  it("Wilson for a pass rate; mean ± 1.96·sd/√n for a score", async () => {
    const { intervalOfSummary } = await import("../trialReducers");
    const w = wilson(0.64, 238);
    expect(intervalOfSummary({ avg: 0.64, n: 238 }, { rate: true })).toEqual([
      w.low,
      w.high,
    ]);
    const s = intervalOfSummary({ avg: 0.7, std: 0.2, n: 100 });
    expect(s[0]).toBeCloseTo(0.7 - 0.0392, 9);
    expect(s[1]).toBeCloseTo(0.7 + 0.0392, 9);
    expect(intervalOfSummary({ avg: 0.7, std: 0.2, n: 0 })).toBeNull();
    expect(intervalOfSummary({ avg: 0.7, n: 1 })).toBeNull();
  });
});
