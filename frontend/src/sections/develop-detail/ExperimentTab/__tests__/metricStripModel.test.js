import { describe, it, expect } from "vitest";
import {
  chartsSummary,
  errorRateOf,
  evalSummary,
  normalizedScore,
  runNotes,
  scoreDefs,
  summarize,
} from "../metricStripModel";
import { distinctConfigLine } from "../leaderboardModel";

const CORRECTNESS = {
  key: "eval:correctness",
  kind: "eval",
  output_type: "pass_fail",
  higher_is_better: true,
  is_dataset_eval: true,
};
const GROUNDEDNESS = {
  key: "eval:groundedness",
  kind: "eval",
  output_type: "score",
  higher_is_better: true,
  is_dataset_eval: true,
};
const TASK = {
  key: "eval:task_completion",
  kind: "eval",
  output_type: "pass_fail",
};

const run = (
  label,
  {
    avg,
    n = 238,
    status = "Completed",
    done = 238,
    total = 238,
    cost,
    errorRate,
  } = {},
) => ({
  run_id: label,
  label,
  status,
  progress: { done, total },
  metrics: {
    aggregates: avg == null ? {} : { [CORRECTNESS.key]: { avg, n } },
    cost_usd: cost,
    error_rate: errorRate,
  },
});

describe("scores card", () => {
  it("draws the dataset's evals, else any eval", () => {
    expect(
      scoreDefs([CORRECTNESS, TASK, GROUNDEDNESS]).map((d) => d.key),
    ).toEqual(["eval:correctness", "eval:groundedness"]);
    expect(scoreDefs([TASK]).map((d) => d.key)).toEqual([
      "eval:task_completion",
    ]);
  });

  it("puts every eval on 0–100", () => {
    expect(normalizedScore(run("#2B", { avg: 0.866 }), CORRECTNESS)).toBe(86.6);
    expect(normalizedScore(run("#6A"), CORRECTNESS)).toBeNull();
  });

  it("summarises latest and best by direction", () => {
    const runs = [
      run("#1A", { avg: 0.794 }),
      run("#2B", { avg: 0.866 }),
      run("#7B", { avg: 0.6 }),
    ];
    const s = evalSummary(runs, CORRECTNESS);
    expect(s.latest.label).toBe("#7B");
    expect(s.best.label).toBe("#2B");
  });

  it("puts partial, stopped, waiting and low-sample notes in the tooltip", () => {
    expect(
      runNotes(
        run("#7A", { avg: 0.8, n: 10, status: "Running", done: 10, total: 48 }),
        CORRECTNESS,
      ),
    ).toEqual(["Running · 10 of 48 rows (partial)", "Low sample · n=10"]);
    expect(
      runNotes(run("#8A", { status: "Cancelled", done: 96, total: 248 })),
    ).toEqual(["Stopped at 96 of 248 rows"]);
    // L7c g5: the one waiting noun (an upload run waits for results).
    expect(
      runNotes({
        ...run("#6A", { status: "WaitingForResults", done: 0, total: 20 }),
        target_type: "external",
      }),
    ).toEqual(["Waiting for results"]);
  });
});

describe("ops cards", () => {
  it("finds the latest and the extreme value", () => {
    const runs = [
      run("#1A", { cost: 0.21 }),
      run("#4A", { cost: 1.5 }),
      run("#7B", { cost: 0.01 }),
    ];
    const high = summarize(runs, (r) => r.metrics.cost_usd);
    expect([high.latest.label, high.best.label]).toEqual(["#7B", "#4A"]);
    const low = summarize(runs, (r) => r.metrics.cost_usd, {
      lowerIsBetter: true,
    });
    expect(low.best.label).toBe("#7B");
    expect(summarize([run("#6A")], () => null)).toBeNull();
  });

  it("has no error rate before any row ran", () => {
    expect(errorRateOf(run("#6A", { done: 0, errorRate: 0 }))).toBeNull();
    expect(errorRateOf(run("#2A", { errorRate: 0.008 }))).toBe(0.008);
  });
});

describe("distinct config line", () => {
  const prompt = (id, model, temperature) => ({
    run_id: id,
    target_type: "prompt",
    target: {
      prompt_name: "Support Reply",
      prompt_version_name: "V5",
      model,
      model_params: { temperature },
    },
  });
  it("shows only what differs between sibling runs", () => {
    const a = prompt("A", "gpt-4o-mini", 0.2);
    const b = prompt("B", "gpt-4o-mini", 0.9);
    expect(distinctConfigLine(a, [a, b])).toBe("t=0.2");
    const c = prompt("C", "claude-sonnet-4-5", 0.2);
    expect(distinctConfigLine(c, [a, c])).toBe("claude-sonnet-4-5");
    expect(distinctConfigLine(a, [a])).toBe(
      "Support Reply V5 · gpt-4o-mini · t=0.2",
    );
  });
});

describe("chartsSummary (A2-01 collapsed Charts line)", () => {
  const withLatency = (r, s, i) => ({
    ...r,
    created_at: `2026-10-0${i + 1}T00:00:00Z`,
    metrics: { ...r.metrics, latency_p50_s: s },
  });
  const runs = [
    withLatency(run("#1A", { avg: 0.62, cost: 2.38, errorRate: 0 }), 9.8, 0),
    withLatency(run("#2A", { avg: 0.53, cost: 0.952, errorRate: 0 }), 6.1, 1),
    withLatency(run("#2D", { avg: 0.91, cost: 18.1, errorRate: 0.02 }), 64, 2),
  ];

  it("says three facts: best, cheapest per task, fastest (IT-R3 G2 P5)", () => {
    expect(chartsSummary(runs, [CORRECTNESS])).toBe(
      "best score #2D 91.0% · cheapest #2A $0.0040 · fastest #2A 6.1 s",
    );
  });

  it("follows the Scores card's eval and leaves out clauses without data", () => {
    const noCost = runs.map((r) => ({
      ...r,
      metrics: { ...r.metrics, cost_usd: null },
    }));
    expect(chartsSummary(noCost, [CORRECTNESS])).not.toContain("cheapest");
    // A score eval nobody has: no `best score` clause.
    expect(
      chartsSummary(runs, [CORRECTNESS, GROUNDEDNESS], {
        scoreKey: GROUNDEDNESS.key,
      }),
    ).not.toContain("best score");
    expect(chartsSummary([], [CORRECTNESS])).toBe(null);
  });

  it("ranks finished runs only (L7c g5 kept:so-far-headline)", () => {
    const playing = withLatency(
      run("#9A", {
        avg: 0.99,
        cost: 0.0001,
        errorRate: 0,
        status: "Running",
        done: 3,
      }),
      0.2,
      3,
    );
    const text = chartsSummary([...runs, playing], [CORRECTNESS]);
    expect(text).not.toContain("#9A");
    expect(text).toBe(
      "best score #2D 91.0% · cheapest #2A $0.0040 · fastest #2A 6.1 s",
    );
    expect(chartsSummary([playing], [CORRECTNESS])).toBe(null);
  });
});
