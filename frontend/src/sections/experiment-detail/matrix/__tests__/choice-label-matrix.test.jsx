// The Matrix showed a choice evaluator's stored score (`judge 1`) in a
// finished cell and its hover card instead of its label (`judge Pass`).
// A choice try has no pass / fail verdict of its own, so the cell reads the
// row's score, now through the evaluator's choice map.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import MatrixCell from "../MatrixCell";
import {
  buildMatrixModel,
  cellScoreList,
  metricDefsOf,
  unscoredResultText,
} from "../matrixModel";

const A = "11111111-1111-4111-8111-111111111111";
const METRIC = "33333333-3333-4333-8333-333333333333";
const judge = {
  key: METRIC,
  name: "judge",
  kind: "eval",
  type: "deterministic",
  output_type: "choice",
  higher_is_better: null,
  pass_threshold: 0.5,
  choice_scores: { Pass: 1, Fail: 0 },
};

// R3 shape=matrix cell: the try completed with no verdict
// (results_matrix.trial_value -> null) and the row's mean score.
const doneCell = (value, n = 1) => ({
  trials: Array(n).fill(null),
  trial_numbers: Array.from({ length: n }, (_, i) => i + 1),
  result_ids: Array.from({ length: n }, (_, i) => `result-${i}`),
  cost_usd: Array(n).fill(null),
  wall_ms: Array(n).fill(12),
  trace_ids: Array(n).fill(null),
  trace_summary: Array.from({ length: n }, (_, i) => ({
    status: "completed",
    trial_number: i + 1,
    carried: false,
  })),
  failed_evals: Array.from({ length: n }, () => []),
  carried: Array(n).fill(false),
  scores: { [METRIC]: { value, status: "assessed", n } },
});

const matrixOf = (def, cell) => ({
  total: 1,
  rows: [
    {
      row_id: "row-0",
      label: "1",
      values: {},
      source: null,
      cells: { [A]: cell },
    },
  ],
  columns: {
    [A]: {
      trials: 1,
      status: "completed",
      trial_count_source: "immutable_plan",
    },
  },
  summary: {
    runs: [
      {
        run_id: A,
        label: "#1",
        name: "Run A",
        status: "completed",
        progress: { status: "Completed", total: 1, done: 1 },
      },
    ],
    metrics: [def],
  },
});

afterEach(cleanup);

describe("a choice evaluator in the Matrix", () => {
  it("keeps every metric def of the read by key", () => {
    const model = buildMatrixModel(matrixOf(judge, doneCell(1)), {
      runIds: [A],
    });
    expect(model.metricDefs).toEqual({ [METRIC]: judge });
    expect(metricDefsOf(null)).toEqual({});
  });

  it("names the row's choice in the cell text and the hover card", () => {
    const model = buildMatrixModel(matrixOf(judge, doneCell(1)), {
      runIds: [A],
    });
    expect(
      cellScoreList(doneCell(1), model.metricNames, model.metricDefs),
    ).toEqual([{ key: METRIC, name: "judge", value: 1, text: "Pass" }]);
    expect(
      unscoredResultText(doneCell(0, 2), model.metricNames, model.metricDefs),
    ).toBe("judge Fail");
  });

  it("keeps the number for tries that disagree, and without a map", () => {
    const names = { [METRIC]: "judge" };
    const defs = {
      [METRIC]: { ...judge, choice_scores: { Good: 1, Partial: 0.5, Bad: 0 } },
    };
    // Two tries averaging 0.5 may be Good + Bad, never `Partial`.
    expect(unscoredResultText(doneCell(0.5, 2), names, defs)).toBe(
      "judge 0.50",
    );
    expect(unscoredResultText(doneCell(0.5, 1), names, defs)).toBe(
      "judge Partial",
    );
    expect(unscoredResultText(doneCell(1), names)).toBe("judge 1");
  });

  it("shows the label in a finished cell", () => {
    render(
      <MatrixCell
        cell={doneCell(1)}
        runLabel="#1"
        rowLabel="1"
        metricNames={{ [METRIC]: "judge" }}
        metricDefs={{ [METRIC]: judge }}
        dense
      />,
    );
    const cell = screen.getByRole("gridcell", { name: "#1 1: judge Pass" });
    expect(cell).toHaveTextContent("Pass");
  });
});
