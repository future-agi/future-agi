import { describe, expect, it } from "vitest";
import {
  failedEvalKeys,
  fmtCost,
  fmtDuration,
  percentile,
  scoreFails,
  summarize,
  summaryLine,
  traceStatusOf,
} from "../tracesModel";

const DEFS = {
  "eval:correctness": { output_type: "pass_fail", higher_is_better: true },
  "eval:groundedness": {
    output_type: "score",
    higher_is_better: true,
    pass_threshold: 0.7,
  },
  "eval:toxicity": {
    output_type: "score",
    higher_is_better: false,
    pass_threshold: 0.2,
  },
  "eval:length": {
    output_type: "score",
    higher_is_better: true,
    pass_threshold: null,
  },
};

describe("w5-c traces model", () => {
  it("status from the stored try", () => {
    expect(traceStatusOf({ status: "completed" })).toBe("ok");
    expect(traceStatusOf({ status: "error" })).toBe("error");
    expect(traceStatusOf({ status: "infra" })).toBe("infra");
    expect(traceStatusOf({ status: "completed", visibility: "error" })).toBe(
      "error",
    );
  });

  it("failed evals by each eval's own definition", () => {
    expect(scoreFails(DEFS["eval:correctness"], 0)).toBe(true);
    expect(scoreFails(DEFS["eval:correctness"], 1)).toBe(false);
    expect(scoreFails(DEFS["eval:groundedness"], 0.69)).toBe(true);
    expect(scoreFails(DEFS["eval:groundedness"], 0.7)).toBe(false);
    expect(scoreFails(DEFS["eval:toxicity"], 0.3)).toBe(true);
    expect(scoreFails(DEFS["eval:length"], 0)).toBe(false);
    expect(scoreFails(undefined, 0)).toBe(false);
    expect(scoreFails(DEFS["eval:correctness"], null)).toBe(false);
    const scores = {
      "eval:correctness": { value: 0 },
      "eval:groundedness": { value: 0.9 },
      "eval:toxicity": { value: 0.5 },
    };
    expect(failedEvalKeys(scores, (k) => DEFS[k])).toEqual([
      "eval:correctness",
      "eval:toxicity",
    ]);
  });

  it("nearest-rank percentiles", () => {
    expect(percentile([5, 1, 3, 2, 4], 50)).toBe(3);
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90)).toBe(9);
    expect(percentile([7], 90)).toBe(7);
    expect(percentile([null, undefined], 50)).toBeNull();
  });

  it.each([
    [0, "$0.00"],
    [0.00003054, "$0.00003054"],
    [0.00000001, "$0.00000001"],
    [0.0001, "$0.0001"],
    [0.0042, "$0.0042"],
    [0.01, "$0.01"],
    [1240, "$1240.00"],
    [-0.00003054, "-$0.00003054"],
    [-1.25, "-$1.25"],
  ])(
    "uses the shared money contract for %s in rows and summaries",
    (value, text) => {
      expect(fmtCost(value)).toBe(text);
      expect(summaryLine({ traces: 1, errors: 0, cost_usd: value })).toBe(
        `1 trace · 0 errors · ${text}`,
      );
    },
  );

  it.each([null, undefined, NaN, Infinity, "", "0.01"])(
    "keeps nonnumeric trace costs unknown (%s)",
    (value) => {
      expect(fmtCost(value)).toBe("–");
      expect(summaryLine({ traces: 1, errors: 0, cost_usd: value })).toBe(
        "1 trace · 0 errors",
      );
    },
  );

  it("summary and its line", () => {
    const traces = [
      { status: "ok", duration_ms: 1000, cost_usd: 0.03 },
      { status: "error", duration_ms: 2100, cost_usd: 0.03 },
      { status: "ok", duration_ms: 3000, cost_usd: 0.03 },
      { status: "infra", duration_ms: null, cost_usd: null },
    ];
    const s = summarize(traces);
    expect(s).toEqual({
      traces: 4,
      errors: 1,
      infra: 1,
      p50_ms: 2100,
      p90_ms: 3000,
      cost_usd: 0.09,
    });
    expect(summaryLine(s)).toBe(
      "4 traces · 1 error · 1 infra · p50 2.1 s · p90 3.0 s · $0.09",
    );
    expect(summaryLine(summarize([]))).toBe("0 traces · 0 errors");
    expect(fmtDuration(850)).toBe("850 ms");
    expect(fmtDuration(12500)).toBe("13 s");
    expect(fmtCost(0.0042)).toBe("$0.0042");
  });
});
