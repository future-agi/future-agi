import { describe, expect, it } from "vitest";
import { executionToRun } from "../runs";
import { buildSummaryRow } from "src/sections/simulate/environments/workspace/runs/summary/summaryData";

const rawExecution = (outcomeSummary) => ({
  id: "history-evaluated-run",
  status: "Completed",
  total_calls: outcomeSummary.total,
  completed_calls: outcomeSummary.total,
  success_rate: 100,
  // The harness receipt stays passed when a platform evaluation fails.
  outcome_passed: outcomeSummary.total,
  outcome_failed: 0,
  outcome_skipped: 0,
  outcome_summary: outcomeSummary,
});

const summary = (overrides = {}) => ({
  total: 1,
  outcomes: { passed: 0, failed: 1, error: 0, inconclusive: 0, queued: 0, in_progress: 0 },
  measured: 1,
  pass_rate: 0,
  ...overrides,
});

describe("H9-C25-002 evaluator-aware history", () => {
  it("shows zero pass when a live evaluation fails a passed receipt", () => {
    const run = executionToRun(rawExecution(summary()));
    expect(run).toMatchObject({ passed: 0, failed: 1, measured: 1, passRate: 0, status: "failed" });
    expect(buildSummaryRow(run).passRate).toBe(0);
  });

  it("uses the detail measured denominator, not all calls", () => {
    const run = executionToRun(rawExecution(summary({
      total: 6,
      outcomes: { passed: 1, failed: 1, error: 1, inconclusive: 2, queued: 1, in_progress: 0 },
      measured: 2,
      pass_rate: 50,
    })));
    // Errors remain visible as failures, but neither errors nor unmeasured
    // calls dilute the evaluator pass rate. Queued calls alone are pending.
    expect(run).toMatchObject({ total: 6, passed: 1, failed: 2, pending: 1, measured: 2 });
    expect(buildSummaryRow(run)).toMatchObject({ tasks: 6, passRate: 50 });
  });

  it("preserves the server's precision instead of recomputing a rounded percentage", () => {
    const run = executionToRun(rawExecution(summary({
      total: 3,
      outcomes: { passed: 2, failed: 1 },
      measured: 3,
      pass_rate: 66.67,
    })));
    expect(buildSummaryRow(run).passRate).toBe(66.67);
  });

  it.each([0, 1])("keeps a no-measurement rate absent with %i calls", (total) => {
    const run = executionToRun(rawExecution(summary({
      total,
      outcomes: { passed: 0, failed: 0, inconclusive: total },
      measured: 0,
      pass_rate: null,
    })));
    expect(run.measured).toBe(0);
    expect(run.pending).toBe(0);
    expect(buildSummaryRow(run).passRate).toBeNull();
  });

  it("keeps the legacy fallback for servers without an outcome summary", () => {
    const run = executionToRun({
      id: "legacy-run",
      status: "Completed",
      total_calls: 4,
      outcome_passed: 3,
      outcome_failed: 1,
      outcome_summary: null,
    });
    expect(run).toMatchObject({ passed: 3, failed: 1, pending: 0 });
    expect(buildSummaryRow(run).passRate).toBe(75);
  });
});
