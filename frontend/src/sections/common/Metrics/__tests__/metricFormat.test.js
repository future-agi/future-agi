import { describe, expect, it } from "vitest";
import {
  EMPTY_VALUE,
  LOW_SAMPLE_N,
  MOVEMENT_RULE_TEXT,
  deltaTone,
  formatCi,
  formatDelta,
  formatValue,
  heatColor,
  movementSummaryText,
} from "../metricFormat";

const correctness = {
  key: "eval:correctness",
  name: "Correctness",
  output_type: "pass_fail",
  higher_is_better: true,
};
const groundedness = {
  key: "eval:groundedness",
  output_type: "score",
  higher_is_better: true,
  pass_threshold: 0.7,
};
const latency = {
  key: "ops:latency",
  kind: "system",
  output_type: "ms",
  higher_is_better: false,
};
const cost = {
  key: "ops:cost",
  kind: "system",
  output_type: "usd",
  higher_is_better: false,
};
const tokens = {
  key: "ops:tokens",
  kind: "system",
  output_type: "count",
  higher_is_better: false,
};
const steps = {
  key: "agent:steps",
  kind: "agent",
  output_type: "count",
  higher_is_better: false,
};
const toolCalls = {
  key: "agent:tool_calls",
  kind: "agent",
  output_type: "count",
  higher_is_better: null,
};
const quality = {
  key: "human:quality",
  output_type: "stars",
  higher_is_better: true,
  min: 1,
  max: 5,
};

describe("formatValue", () => {
  it("formats the §4 examples", () => {
    expect(formatValue(correctness, 0.84)).toBe("84.0%");
    expect(formatValue(correctness, 0.794)).toBe("79.4%");
    expect(formatValue(groundedness, 0.82)).toBe("0.82");
    expect(formatValue(latency, 1840)).toBe("1.8 s");
    expect(formatValue(cost, 0.0023)).toBe("$0.0023");
    expect(formatValue(cost, 0.74)).toBe("$0.74");
    expect(formatValue(tokens, 412)).toBe("412");
  });

  it("formats one row's values", () => {
    expect(formatValue(correctness, 1, { agg: "value" })).toBe("Pass");
    expect(formatValue(correctness, 0, { agg: "value" })).toBe("Fail");
    expect(formatValue(correctness, 2 / 3, { agg: "value" })).toBe("66.7%");
    expect(formatValue(latency, 412)).toBe("412 ms");
    expect(formatValue(steps, 4.7)).toBe("4.7");
    expect(formatValue(tokens, 98000, { agg: "sum" })).toBe("98,000");
    expect(formatValue(quality, 4)).toBe("4");
    expect(formatValue(groundedness, 0.71, { agg: "pass_rate" })).toBe("71.0%");
  });

  it("never prints NaN, undefined or a bare dash", () => {
    [null, undefined, NaN, "", Infinity].forEach((v) =>
      expect(formatValue(correctness, v)).toBe(EMPTY_VALUE),
    );
    expect(formatValue(undefined, 3)).toBe("3");
    expect(formatValue(correctness, "Passed")).toBe("Passed");
  });
});

describe("formatDelta", () => {
  it("formats the §4 examples", () => {
    expect(formatDelta(correctness, 0.071)).toBe("+7.1 pts");
    expect(formatDelta(groundedness, -0.04)).toBe("−0.04");
    expect(formatDelta(latency, 800)).toBe("+800 ms");
    expect(formatDelta(cost, 0.38, { base: 1 })).toBe("+38%");
  });

  it("handles small, zero and missing deltas", () => {
    expect(formatDelta(correctness, -0.021)).toBe("−2.1 pts");
    expect(formatDelta(correctness, 0)).toBe("±0.0 pts");
    expect(formatDelta(latency, -1200)).toBe("−1.2 s");
    expect(formatDelta(latency, 40)).toBe("+40 ms");
    expect(formatDelta(steps, 1.2)).toBe("+1.2");
    expect(formatDelta(cost, 0.0012)).toBe("+$0.0012");
    expect(formatDelta(tokens, 120, { base: 400 })).toBe("+30%");
    expect(formatDelta(correctness, null)).toBe(EMPTY_VALUE);
    expect(formatDelta(groundedness, 0.05, { agg: "pass_rate" })).toBe(
      "+5.0 pts",
    );
  });

  it("formats a CI half-width", () => {
    expect(formatCi(correctness, 0.038)).toBe("±3.8 pts");
    expect(formatCi(groundedness, 0.015)).toBe("±0.01");
    expect(formatCi(correctness, null)).toBe("");
  });
});

describe("deltaTone", () => {
  it("follows direction, and is noise when not significant", () => {
    expect(deltaTone(correctness, 0.071, { significant: true })).toBe("good");
    expect(deltaTone(correctness, -0.05, { significant: true })).toBe("bad");
    expect(deltaTone(latency, 800, { significant: true })).toBe("bad");
    expect(deltaTone(latency, -800, { significant: true })).toBe("good");
    expect(deltaTone(correctness, 0.03, { significant: false })).toBe("noise");
    expect(deltaTone(toolCalls, 2, { significant: true })).toBe("neutral");
    expect(deltaTone(correctness, 0)).toBe("neutral");
    expect(deltaTone(correctness, null)).toBe("neutral");
    expect(deltaTone(correctness, 0.071)).toBe("good");
  });
});

describe("heatColor", () => {
  it("uses the eval-cell scale, reversed when lower is better", () => {
    expect(heatColor(correctness, 1)).toBe("var(--score-green-strong)");
    expect(heatColor(correctness, 0)).toBe("var(--score-red-strong)");
    expect(heatColor(groundedness, 0.5)).toBe("var(--score-orange)");
    const lowerBetter = { ...groundedness, higher_is_better: false };
    expect(heatColor(lowerBetter, 0)).toBe("var(--score-green-strong)");
    expect(heatColor(quality, 5)).toBe("var(--score-green-strong)");
  });

  it("gives no heat to ops, counts and direction-less metrics", () => {
    expect(heatColor(latency, 1000)).toBeNull();
    expect(heatColor(steps, 4)).toBeNull();
    expect(
      heatColor({ ...groundedness, higher_is_better: null }, 0.4),
    ).toBeNull();
    expect(heatColor(correctness, null)).toBeNull();
  });
});

describe("movementSummaryText", () => {
  it("writes the M6 tooltip", () => {
    expect(
      movementSummaryText(correctness, {
        ci95: 0.038,
        significant: false,
        improved: 14,
        regressed: 7,
        unchanged: 215,
        notComparable: 12,
      }),
    ).toBe(
      "Within noise (95% CI ±3.8 pts) · 14 improved · 7 regressed · 215 unchanged · 12 not comparable",
    );
  });

  it("adds low sample and partial progress", () => {
    const text = movementSummaryText(correctness, {
      significant: true,
      ci95: 0.04,
      n: 20,
      partial: true,
      done: 142,
      total: 248,
    });
    expect(text).toContain("95% CI ±4.0 pts");
    expect(text).toContain(`fewer than ${LOW_SAMPLE_N} rows`);
    expect(text).toContain("Partial · 142 of 248 rows");
  });

  it("states the movement rule", () => {
    expect(MOVEMENT_RULE_TEXT).toMatch(/0\.05/);
    expect(MOVEMENT_RULE_TEXT).toMatch(/10%/);
  });
});
