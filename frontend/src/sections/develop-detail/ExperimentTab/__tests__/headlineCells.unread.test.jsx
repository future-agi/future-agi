// A finished run that no answered read covers (no recorded plan, or its
// group's read failed) shows a true reason in Success, Model cost per task
// and Time (p50), never its own summary fields; a run whose read returned
// only part of its rows says so in Success too.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "src/utils/test-utils";
import { EMPTY } from "src/sections/common/format/fmt";
import {
  CostPerTaskCell,
  SuccessRateCell,
  TimePerTaskCell,
} from "../LeaderboardSummaryCells";
import { leaderboardNumbers } from "../leaderboardVersions";

const NO_PLAN = "Not read: this run has no recorded plan.";
const READ_FAILED = "Could not read this run's results. Try again.";
const PARTIAL = "More tasks than one read holds, so not totalled here";

// A finished run with the summary fields an older list might carry: the
// cells must not use them.
const done = (id) => ({
  run_id: id,
  label: `#${id}`,
  status: "Completed",
  listed: true,
  progress: { done: 3, total: 3 },
  success_rate: { passed: 2, total: 3 },
  metrics: { cost_usd: 0.3, latency_p50_s: 2 },
});
const runs = [done("noPlan"), done("failed"), done("long")];
const matrix = {
  rows: [0, 1].map((i) => ({
    row_id: `row-${i}`,
    cells: {
      long: {
        trials: [true],
        trace_summary: [{ status: "completed" }],
        cost_usd: [0.01],
        wall_ms: [1000],
      },
    },
  })),
  columns: { long: {} },
};
const numbers = leaderboardNumbers(runs, matrix, {
  noun: "task",
  partialRunIds: ["long"],
  unreadRuns: { noPlan: NO_PLAN, failed: READ_FAILED },
});
const context = {
  numbers,
  baselineRun: null,
  baselineId: null,
  baselineLabel: null,
  showDeltas: false,
};
const text = (Cell, id) => {
  const data = runs.find((r) => r.run_id === id);
  return render(
    <Cell node={{ group: false, data }} data={data} context={context} />,
  ).container.textContent;
};

afterEach(cleanup);

describe("a finished run no answered read covers", () => {
  it("with no recorded plan, every headline cell says it was not read", () => {
    [SuccessRateCell, CostPerTaskCell, TimePerTaskCell].forEach((Cell) => {
      const shown = text(Cell, "noPlan");
      cleanup();
      expect(shown).toContain("Not read");
      expect(shown).toContain(NO_PLAN);
      expect(shown).not.toContain("%");
      expect(shown).not.toContain("$");
      expect(shown.trim()).not.toBe(EMPTY);
    });
  });

  it("whose group's read failed, every headline cell says so", () => {
    [SuccessRateCell, CostPerTaskCell, TimePerTaskCell].forEach((Cell) => {
      const shown = text(Cell, "failed");
      cleanup();
      expect(shown).toContain("Could not read");
      expect(shown).toContain(READ_FAILED);
      expect(shown).not.toContain("No finished tasks yet");
    });
  });
});

describe("a run with more rows than one read holds", () => {
  it("Success says the read is partial, as the other two cells do", () => {
    const success = text(SuccessRateCell, "long");
    cleanup();
    expect(success).toContain("Too many tasks");
    expect(success).toContain(PARTIAL);
    expect(success).not.toContain("%");
    expect(text(TimePerTaskCell, "long")).toContain(PARTIAL);
  });
});
