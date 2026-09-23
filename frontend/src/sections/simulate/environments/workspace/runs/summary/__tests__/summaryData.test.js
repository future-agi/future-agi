import { describe, it, expect } from "vitest";
import { buildSummaryRow, buildEvalSeries, deriveEvals, EVAL_COLORS } from "../summaryData";

// A run row as produced by `mapExecutions` (newest-first ordinal already
// stamped), extended with the run-level duration the summary table shows.
const run = {
  id: "ex1",
  executionId: "ex1",
  ordinal: 2,
  label: "Run 2",
  status: "passed",
  startedAt: "2026-01-13T16:40:00.000Z",
  finishedAt: "2026-01-13T16:42:10.000Z",
  total: 20,
  passed: 15,
  failed: 5,
  agentVersion: "v2",
  durationS: 11.9,
};

describe("buildSummaryRow", () => {
  it("derives pass rate and tasks from the run and keeps its identity", () => {
    const row = buildSummaryRow(run, { task_success: 49, policy: 28 });
    expect(row.ordinal).toBe(2);
    expect(row.label).toBe("Run 2");
    expect(row.agentVersion).toBe("v2");
    expect(row.tasks).toBe(20);
    expect(row.passRate).toBe(75); // 15 / 20
    expect(row.durationS).toBe(11.9);
    expect(row.at).toBe("2026-01-13T16:42:10.000Z"); // finishedAt preferred
    expect(row.scores).toEqual({ task_success: 49, policy: 28 });
  });

  it("nulls the columns the backend does not provide (no fabricated values)", () => {
    const row = buildSummaryRow(run, {});
    expect(row.tokens).toBeNull();
    expect(row.cost).toBeNull();
    expect(row.saidNotDone).toBeNull();
    expect(row.meanReturn).toBeNull();
  });

  it("does not divide by zero on an empty run", () => {
    const row = buildSummaryRow({ ...run, total: 0, passed: 0 }, undefined);
    expect(row.passRate).toBe(0);
    expect(row.scores).toEqual({});
  });
});

describe("buildEvalSeries", () => {
  const rowsChrono = [
    { label: "Run 1", scores: { task_success: 40, policy: 55 } },
    { label: "Run 2", scores: { task_success: 49 } }, // policy missing → gap
  ];
  const evals = [
    { id: "task_success", name: "Task success", color: "#16A34A" },
    { id: "policy", name: "Policy adherence", color: "#7857FC" },
  ];

  it("builds one series per applied eval, carrying name and colour", () => {
    const series = buildEvalSeries(rowsChrono, evals);
    expect(series).toHaveLength(2);
    expect(series[0]).toMatchObject({ id: "task_success", name: "Task success", color: "#16A34A" });
  });

  it("reads each run's score for the eval and leaves a null gap when absent", () => {
    const series = buildEvalSeries(rowsChrono, evals);
    expect(series[0].data).toEqual([40, 49]);
    expect(series[1].data).toEqual([55, null]); // Run 2 has no policy score
  });

  it("returns no series when the environment has no applied evals", () => {
    expect(buildEvalSeries(rowsChrono, [])).toEqual([]);
  });
});

describe("deriveEvals", () => {
  const rows = [
    { scores: { task_success: 40, policy_adherence: 55 } },
    { scores: { task_success: 49 } },
  ];

  it("derives the eval set from the union of the runs' score keys, first-seen order", () => {
    const evals = deriveEvals(rows);
    expect(evals.map((e) => e.id)).toEqual(["task_success", "policy_adherence"]);
  });

  it("humanises the score key into a sentence-case display name", () => {
    const [first, second] = deriveEvals(rows);
    expect(first.name).toBe("Task success");
    expect(second.name).toBe("Policy adherence");
  });

  it("assigns a distinct palette colour per eval, in order", () => {
    const evals = deriveEvals(rows);
    expect(evals[0].color).toBe(EVAL_COLORS[0]);
    expect(evals[1].color).toBe(EVAL_COLORS[1]);
  });

  it("is empty when no run carries any score", () => {
    expect(deriveEvals([{ scores: {} }, {}])).toEqual([]);
  });
});
