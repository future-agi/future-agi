// The list's Success, Cost per task and Time (p50) cells, read from the
// leaderboard's numbers over a results matrix: a number where every try that
// ran recorded one, otherwise a few visible words with the full reason in
// the cell's text (hover and keyboard focus), never a bare dash on a
// finished run.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "src/utils/test-utils";
import { EMPTY, fmtCost, fmtPct } from "src/sections/common/format/fmt";
import {
  CostPerTaskCell,
  SuccessRateCell,
  TimePerTaskCell,
} from "../LeaderboardSummaryCells";
import { leaderboardNumbers } from "../leaderboardVersions";
import { fmtSecs1 } from "../leaderboardHeadline";
import { RECORDED_REASON, RECORDED_SHORT } from "../recordedTaskNumbers";

const done = (id) => ({
  run_id: id,
  label: `#${id}`,
  status: "Completed",
  listed: true,
  progress: { done: 3, total: 3 },
});
// tries per row: [status, passed, cost_usd, wall_ms]
const cellOf = (tries) => ({
  trials: tries.map(([s, p]) => (s === "completed" ? p : s)),
  trace_summary: tries.map(([status]) => ({ status })),
  cost_usd: tries.map(([, , usd]) => usd),
  wall_ms: tries.map(([, , , ms]) => ms),
});
const RUNS = {
  priced: [
    [["completed", true, 0.01, 1000]],
    [["completed", true, 0.02, 3000]],
    [["completed", false, 0.03, 2000]],
  ],
  unrecorded: [
    [["completed", true, null, null]],
    [["completed", false, null, null]],
    [["error", null, null, null]],
  ],
  stopped: [
    [["stopped", null, null, null]],
    [["stopped", null, null, null]],
    [["not_reached", null, null, null]],
  ],
  going: [
    [["queued", null, null, null]],
    [["queued", null, null, null]],
    [["queued", null, null, null]],
  ],
};
const runs = [
  done("priced"),
  done("unrecorded"),
  { ...done("stopped"), status: "Cancelled" },
  { ...done("going"), status: "Running", progress: { done: 0, total: 3 } },
];
const matrix = {
  rows: [0, 1, 2].map((i) => ({
    row_id: `row-${i}`,
    cells: Object.fromEntries(
      Object.entries(RUNS).map(([id, rows]) => [id, cellOf(rows[i])]),
    ),
  })),
  columns: Object.fromEntries(Object.keys(RUNS).map((id) => [id, {}])),
};
const numbers = leaderboardNumbers(runs, matrix, { noun: "task" });
const context = {
  numbers,
  baselineRun: null,
  baselineId: null,
  baselineLabel: null,
  showDeltas: false,
};
const cell = (Cell, id) => {
  const data = runs.find((r) => r.run_id === id);
  const { container } = render(
    <Cell node={{ group: false, data }} data={data} context={context} />,
  );
  return container.textContent;
};

afterEach(cleanup);

describe("the list's headline cells over recorded tries", () => {
  it("a finished run with every try recorded shows all three numbers", () => {
    expect(cell(SuccessRateCell, "priced")).toContain(fmtPct(2 / 3));
    expect(cell(CostPerTaskCell, "priced")).toContain(fmtCost(0.02));
    expect(cell(TimePerTaskCell, "priced")).toContain(fmtSecs1(2000));
  });

  it("a finished run whose tries recorded nothing says so in words", () => {
    const perTask = cell(CostPerTaskCell, "unrecorded");
    expect(perTask).toContain(RECORDED_SHORT[RECORDED_REASON.perTaskNone]);
    expect(perTask).toContain(RECORDED_REASON.perTaskNone);
    expect(perTask.trim()).not.toBe(EMPTY);
    const time = cell(TimePerTaskCell, "unrecorded");
    expect(time).toContain(RECORDED_SHORT[RECORDED_REASON.timeNone]);
    expect(time).toContain(RECORDED_REASON.timeNone);
  });

  it("a stopped run with no try that ran says that, not a dash", () => {
    const perTask = cell(CostPerTaskCell, "stopped");
    expect(perTask).toContain(RECORDED_SHORT[RECORDED_REASON.noneRan]);
    expect(perTask).toContain(RECORDED_REASON.noneRan);
    expect(cell(TimePerTaskCell, "stopped")).toContain(
      RECORDED_SHORT[RECORDED_REASON.noneRan],
    );
  });

  it("a run still going with no finished try keeps the dash and its reason", () => {
    const perTask = cell(CostPerTaskCell, "going");
    expect(perTask.startsWith(EMPTY)).toBe(true);
    expect(perTask).toContain(RECORDED_REASON.noTries);
  });
});
