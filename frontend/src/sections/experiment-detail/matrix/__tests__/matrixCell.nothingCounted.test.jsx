// The Matrix for a run with nothing to count (no evaluation with a Pass/fail
// result): its cells show no `0/2` and no tint, and say their tries errored;
// it takes part in no Show set; and a row's "Solved by" line leaves it out of
// its count and names its errored cells.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import MatrixCell from "../MatrixCell";
import * as model from "../matrixModel";
import hoverSource from "../MatrixHoverCard.jsx?raw";
import viewSource from "../MatrixView.jsx?raw";

const NONE = "run-none";
const NONE2 = "run-none-2";
const PASSES = "run-passes";

const tries = (value, status, n = 2) => ({
  trials: Array(n).fill(value),
  trace_summary: Array.from({ length: n }, (_, i) => ({
    status,
    trial_number: i + 1,
    carried: false,
  })),
  failed_evals: Array(n).fill([]),
});
const column = (label, passFailEvals) => ({
  label,
  trials: 2,
  status: "completed",
  trial_count_source: "immutable_plan",
  pass_fail_evals: passFailEvals,
});
const matrixOf = (cells, columns) => ({
  rows: [1, 2].map((n) => ({ row_id: `row-${n}`, n, label: `T${n}`, cells })),
  columns,
});

afterEach(cleanup);

describe("a Matrix cell of a run with nothing to count", () => {
  it("shows no figure and says its tries errored", () => {
    render(
      <MatrixCell
        cell={tries("error", "error")}
        runLabel="#1"
        rowLabel="T4"
        noPassFail
        dense
      />,
    );
    const cell = screen.getByRole("gridcell", {
      name: "#1 T4: 2 of 2 trials errored",
    });
    expect(cell).toHaveTextContent("—");
    expect(cell).not.toHaveTextContent("0/2");
  });

  it("says so for one try", () => {
    render(
      <MatrixCell
        cell={tries("error", "error", 1)}
        runLabel="#1"
        rowLabel="T4"
        noPassFail
        dense
      />,
    );
    expect(
      screen.getByRole("gridcell", { name: "#1 T4: 1 of 1 trial errored" }),
    ).toBeInTheDocument();
  });

  it("has no tint", () => {
    expect(model.cellTintRate?.(tries("error", "error"), true)).toBeNull();
    expect(model.cellTintRate?.(tries("error", "error"), false)).toBe(0);
  });

  it("says its tries errored in the hover card", () => {
    expect(model.erroredText?.(tries("error", "error"))).toBe(
      "2 of 2 trials errored",
    );
    // Reads the source: a wiring pin.
    expect(hoverSource).toMatch(/noPassFail \? erroredText\(cell\)/);
    expect(viewSource).toMatch(
      /noPassFail=\{Boolean\(\s*hover && model\.columnsById\[hover\.runId\]\?\.noPassFail,?\s*\)\}/,
    );
  });

  it("leaves a run with something counted as it was", () => {
    // Green at the base.
    render(
      <MatrixCell
        cell={tries(false, "completed")}
        runLabel="#2"
        rowLabel="T4"
        dense
      />,
    );
    const cell = screen.getByRole("gridcell", {
      name: "#2 T4: 0 of 2 trials passed",
    });
    expect(cell).toHaveTextContent("0/2");
  });
});

describe("the Show chips with a run that has nothing to count", () => {
  it("leave it out of every set but All", () => {
    const m = model.buildMatrixModel(
      matrixOf(
        { [NONE]: tries("error", "error"), [PASSES]: tries(true, "completed") },
        { [NONE]: column("#1", 0), [PASSES]: column("#2", 1) },
      ),
      { runIds: [NONE, PASSES] },
    );
    expect(m.showCounts).toEqual({
      all: 2,
      disagree: 0,
      flaky: 0,
      nobody: 0,
      everyone: 2,
    });
  });

  it("count every run when the matrix does not say", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are in the sets, as before.
    const m = model.buildMatrixModel(
      matrixOf(
        { [NONE]: tries("error", "error"), [PASSES]: tries(true, "completed") },
        { [NONE]: column("#1", undefined), [PASSES]: column("#2", 1) },
      ),
      { runIds: [NONE, PASSES] },
    );
    expect(m.showCounts.disagree).toBe(2);
    expect(m.showCounts.everyone).toBe(0);
  });
});

describe("a row's Solved by line with a run that has nothing to count", () => {
  it("counts the runs with a figure and names the errored one", () => {
    const m = model.buildMatrixModel(
      matrixOf(
        { [NONE]: tries("error", "error"), [PASSES]: tries(true, "completed") },
        { [NONE]: column("#1", 0), [PASSES]: column("#2", 1) },
      ),
      { runIds: [NONE, PASSES] },
    );
    expect(m.rows[0].solvedText).toBe("Solved by 1 of 1 · 1 errored");
  });

  it("has no count when no run in the row has a figure", () => {
    const m = model.buildMatrixModel(
      matrixOf(
        { [NONE]: tries("error", "error"), [NONE2]: tries("error", "error") },
        { [NONE]: column("#1", 0), [NONE2]: column("#3", 0) },
      ),
      { runIds: [NONE, NONE2] },
    );
    expect(m.rows[0].solvedText).toBe("Solved by — · 2 errored");
  });

  it("reads as before when every run has a figure", () => {
    // Green at the base.
    const m = model.buildMatrixModel(
      matrixOf(
        {
          [NONE]: tries(false, "completed"),
          [PASSES]: tries(true, "completed"),
        },
        { [NONE]: column("#1", 1), [PASSES]: column("#2", 1) },
      ),
      { runIds: [NONE, PASSES] },
    );
    expect(m.rows[0].solvedText).toBe("Solved by 1 of 2");
  });
});
