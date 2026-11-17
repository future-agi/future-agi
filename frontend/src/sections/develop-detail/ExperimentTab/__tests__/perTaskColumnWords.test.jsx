// The list's per-task column names what it counts (model calls only), and
// a run of your own code or a harness keeps the list's sentence for a run on
// your key instead of "Not recorded".
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "src/utils/test-utils";
import { columnCatalog } from "../leaderboardColumns";
import {
  CostPerTaskCell,
  NOT_MEASURED_KEY,
  TimePerTaskCell,
  shownPerTask,
} from "../LeaderboardSummaryCells";
import { leaderboardNumbers } from "../leaderboardVersions";
import { fmtSecs1 } from "../leaderboardHeadline";

const done = (id, extra = {}) => ({
  run_id: id,
  label: `#${id}`,
  status: "Completed",
  listed: true,
  progress: { done: 2, total: 2 },
  ...extra,
});
const code = done("code", { config: { kind: "code" } });
const harness = done("harness", { config: { kind: "harness" } });
const prompt = done("prompt");
const runs = [code, harness, prompt];
// Each run: two rows, one completed try each, recorded as [cost_usd, wall_ms].
const RECORDED = {
  code: [
    [null, 400],
    [null, 600],
  ],
  harness: [
    [0.01, 400],
    [0.01, 600],
  ],
  prompt: [
    [0.01, 400],
    [0.03, 600],
  ],
};
const matrix = {
  rows: [0, 1].map((i) => ({
    row_id: `row-${i}`,
    cells: Object.fromEntries(
      Object.entries(RECORDED).map(([id, rows]) => [
        id,
        {
          trials: [true],
          trace_summary: [{ status: "completed" }],
          cost_usd: [rows[i][0]],
          wall_ms: [rows[i][1]],
        },
      ]),
    ),
  })),
  columns: Object.fromEntries(Object.keys(RECORDED).map((id) => [id, {}])),
};
const context = {
  numbers: leaderboardNumbers(runs, matrix, { noun: "task" }),
  baselineRun: null,
  baselineId: null,
  baselineLabel: null,
  showDeltas: false,
};
const text = (Cell, run) =>
  render(
    <Cell node={{ group: false, data: run }} data={run} context={context} />,
  ).container.textContent;

afterEach(cleanup);

describe("the per-task column's words", () => {
  it("is labelled for model calls, with its tip", () => {
    const col = columnCatalog([], [prompt]).find(
      (c) => c.id === "cost_per_task",
    );
    expect(col.label).toBe("Model cost per task");
    expect(col.description).toBe(
      "Model calls only; evaluations are billed separately.",
    );
  });

  it("a run of your own code keeps the list's on-your-key sentence", () => {
    const shown = text(CostPerTaskCell, code);
    expect(NOT_MEASURED_KEY).toBe("Not measured: runs on your key");
    expect(shown).toContain(NOT_MEASURED_KEY);
    expect(shown).toContain("Not measured");
    expect(shown).not.toContain("Not recorded");
    expect(shownPerTask(code, context)).toBeNull();
  });

  it("a harness run keeps it too, whatever its tries recorded", () => {
    const shown = text(CostPerTaskCell, harness);
    expect(shown).toContain(NOT_MEASURED_KEY);
    expect(shownPerTask(harness, context)).toBeNull();
  });

  it("their time is still the recorded one, and a prompt run keeps its number", () => {
    expect(text(TimePerTaskCell, code)).toContain(fmtSecs1(500));
    expect(shownPerTask(prompt, context)).toBeCloseTo(0.02, 10);
  });
});
