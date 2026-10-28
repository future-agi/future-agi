import { describe, it, expect } from "vitest";
import {
  buildSummaryRow,
  countCoveredScenarios,
  deriveEvals,
  EVAL_COLORS,
  isFinishedRun,
} from "../summaryData";

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

  it("leaves tokens and cost empty: the runs list has no field for them", () => {
    const row = buildSummaryRow(run, {});
    expect(row.tokens).toBeNull();
    expect(row.cost).toBeNull();
  });

  it("carries no columns the API has no data for", () => {
    const row = buildSummaryRow(run, {});
    expect(row).not.toHaveProperty("saidNotDone");
    expect(row).not.toHaveProperty("meanReturn");
  });

  it("carries the real passed and failed counts and when the run finished", () => {
    const row = buildSummaryRow(run, {});
    expect(row.passed).toBe(15);
    expect(row.failed).toBe(5);
    expect(row.finishedAt).toBe("2026-01-13T16:42:10.000Z");
    expect(buildSummaryRow({ ...run, failed: undefined }, {}).failed).toBe(5);
  });

  it("does not divide by zero on an empty run", () => {
    const row = buildSummaryRow({ ...run, total: 0, passed: 0 }, undefined);
    expect(row.passRate).toBe(0);
    expect(row.scores).toEqual({});
  });
});

describe("deriveEvals", () => {
  const rows = [
    { scores: { task_success: 40, policy_adherence: 55 } },
    { scores: { task_success: 49 } },
  ];

  it("derives the eval set from the union of the runs' score keys, first-seen order", () => {
    const evals = deriveEvals(rows);
    expect(evals.map((e) => e.id)).toEqual([
      "task_success",
      "policy_adherence",
    ]);
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

describe("countCoveredScenarios", () => {
  it("counts the distinct scenarios across runs that carry ids", () => {
    const rows = [{ scenarioIds: ["a", "b"] }, { scenarioIds: ["b", "c"] }];
    expect(countCoveredScenarios(rows, 20)).toBe(3);
  });

  it("counts every id once when runs overlap fully", () => {
    const rows = [{ scenarioIds: ["a", "b"] }, { scenarioIds: ["a", "b"] }];
    expect(countCoveredScenarios(rows, 20)).toBe(2);
  });

  it("skips a run without ids instead of falling back to the environment total", () => {
    const rows = [{ scenarioIds: ["a", "b"] }, { scenarioIds: [] }, {}];
    expect(countCoveredScenarios(rows, 20)).toBe(2);
  });

  it("falls back to the environment total only when no run carries ids", () => {
    expect(countCoveredScenarios([{ scenarioIds: [] }, {}], 20)).toBe(20);
    expect(countCoveredScenarios([], 20)).toBe(20);
    expect(countCoveredScenarios(undefined, 20)).toBe(20);
  });
});

describe("isFinishedRun", () => {
  it("counts a run that finished with a verdict", () => {
    expect(isFinishedRun(run)).toBe(true);
    expect(isFinishedRun({ ...run, status: "failed" })).toBe(true);
  });

  it("leaves off running, stopped and empty runs", () => {
    expect(isFinishedRun({ ...run, status: "running", finishedAt: null })).toBe(
      false,
    );
    expect(isFinishedRun({ ...run, status: "cancelled" })).toBe(false);
    expect(isFinishedRun({ ...run, total: 0 })).toBe(false);
    expect(isFinishedRun({ ...run, finishedAt: null })).toBe(false);
  });
});
