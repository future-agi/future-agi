import { describe, expect, it } from "vitest";
import {
  agentSummary,
  breakdownSummary,
  evalsSummary,
  humanSummary,
  opsSummary,
} from "../sectionSummaries";
import { allRunsCatalog } from "../allRunsModel";

const A = "a6000000-0000-4000-8000-00000000002a";
const B = "a6000000-0000-4000-8000-00000000002b";
const D = "a6000000-0000-4000-8000-00000000002d";

const analysis = {
  baseline_run_id: B,
  candidate_run_id: D,
  runs: [
    { run_id: A, label: "#2A", latency_p50_ms: 6093, cost_per_row: 0.004 },
    { run_id: B, label: "#2B", latency_p50_ms: 9833, cost_per_row: 0.011 },
    { run_id: D, label: "#2D", latency_p50_ms: 74346, cost_per_row: 0.21 },
  ],
  metrics: [
    {
      key: "eval:task_success",
      name: "Task success",
      family: "eval",
      output_type: "pass_fail",
      per_run: [
        { run_id: A, value: 0.5333, n: 30 },
        { run_id: B, value: 0.6556, n: 30 },
        { run_id: D, value: 0.911, n: 30 },
      ],
    },
    {
      key: "eval:reply_rubric",
      name: "Task reply rubric v1",
      family: "eval",
      output_type: "pass_fail",
      per_run: [
        { run_id: A, value: 0.52, n: 27 },
        { run_id: B, value: 0.882, n: 24 },
        { run_id: D, value: null, n: 0 },
      ],
    },
    { key: "ops:latency", family: "ops", output_type: "ms", per_run: [] },
  ],
  agent: [
    { run_id: B, avg_steps: 7.12 },
    { run_id: D, avg_steps: 16.04 },
  ],
  breakdown: {
    group_by: "split",
    metric: "eval:reply_rubric",
    groups: [
      { label: "smoke", vs_baseline: { [A]: { delta: -0.143 } } },
      { label: "No split", vs_baseline: { [A]: { delta: -0.417 } } },
    ],
  },
};

describe("sectionSummaries (one view A5-02)", () => {
  it("Evals: the lowest pass rate of any pass/fail eval and its run", () => {
    expect(evalsSummary(analysis)).toBe(
      "lowest pass rate: Task reply rubric v1 52.0% on #2A",
    );
  });

  it("Latency and cost: slowest p50 and cheapest per task (All runs numbers)", () => {
    expect(opsSummary(analysis)).toBe(
      "#2D slowest p50 74 s · #2A cheapest $0.0040 per task",
    );
  });

  it("Human review: reviewed rows and judge agreement of the candidate", () => {
    const withPrefs = {
      ...analysis,
      preference: {
        [D]: { items: 30, judged: 12, judge: { agreement: { pct: 0.833 } } },
      },
    };
    expect(humanSummary(withPrefs)).toBe(
      "12 of 30 rows reviewed · judge agrees 83%",
    );
    expect(humanSummary({ ...analysis, preference: {}, human: [] })).toBe(null);
    expect(humanSummary({ ...analysis, preference: {}, human: [{}, {}] })).toBe(
      "2 rubric labels",
    );
  });

  it("Agent steps: the run with the most steps per task", () => {
    expect(agentSummary(analysis)).toBe(
      "#2D takes the most steps (16.0 per task)",
    );
    expect(agentSummary({ ...analysis, agent: [] })).toBe(null);
  });

  it("Breakdown: group count and the biggest gap vs the baseline", () => {
    expect(breakdownSummary(analysis)).toBe(
      "2 splits · biggest gap: No split (#2A −41.7 pts)",
    );
    expect(breakdownSummary({ ...analysis, breakdown: { groups: [] } })).toBe(
      null,
    );
  });

  it("nothing computable → null (the header shows the title only)", () => {
    expect(evalsSummary({ runs: [], metrics: [] })).toBe(null);
    expect(opsSummary({ runs: [] })).toBe(null);
  });
});

describe("allRunsCatalog (one view A5-09)", () => {
  const evals = [
    { key: "eval:a", name: "A" },
    { key: "eval:b", name: "B" },
    { key: "eval:c", name: "C" },
  ];
  it("default: Run (locked, frozen) · Success · Cost · Time · Passes every eval · Errors · first 2 evals", () => {
    const cat = allRunsCatalog({ evals, maxTrials: 3 });
    const shown = cat
      .filter((c) => c.defaultVisible !== false)
      .map((c) => c.id);
    expect(shown).toEqual([
      "run",
      "success_rate",
      "cost_per_row",
      "average_response_time",
      "pass_rate",
      "errored_rows",
      "eval:a",
      "eval:b",
    ]);
    expect(cat[0]).toMatchObject({ locked: true, defaultPinned: "left" });
  });
  it("hidden by default: other evals, Reliable, Trials, Time p99, Tokens, Total cost", () => {
    const cat = allRunsCatalog({ evals, maxTrials: 3 });
    const hidden = cat
      .filter((c) => c.defaultVisible === false)
      .map((c) => c.id);
    expect(hidden).toEqual([
      "reliable_rate",
      "flaky_rows",
      "eval:c",
      "latency_p99",
      "total_token",
      "cost_total",
    ]);
  });
  it("one try per task: no Reliable / Trials columns; Ranking once ranked", () => {
    const ids = allRunsCatalog({ evals, maxTrials: 1, hasRank: true }).map(
      (c) => c.id,
    );
    expect(ids).not.toContain("reliable_rate");
    expect(ids).not.toContain("flaky_rows");
    expect(ids[1]).toBe("rank");
  });
});
