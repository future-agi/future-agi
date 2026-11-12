// An experiment's Data tab showed a choice evaluator's stored score (`1`) in
// every score cell instead of its label (`Pass`). The evaluator scores with
// choice_scores {Pass: 1, Fail: 0}; the read now describes it as a choice
// metric with that map, and the cell reads the label for the row.
/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import { ScoreCellRenderer } from "../MetricCells";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const RUN = "run-1";
const METRIC = "metric-judge";
// metric_defs[] / summary.metrics item for the evaluator (results_catalog).
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
const graded = { ...judge, choice_scores: { Good: 1, Partial: 0.5, Bad: 0 } };

// The row's score as results_grid.result_cell reads it (mean of tries).
const assessed = (value, n = 1) => ({
  value,
  n,
  status: "assessed",
  rule: "assessed_trial_mean",
  total_trials: n,
  error_trials: 0,
});

const renderCell = (def, score) =>
  render(
    <ScoreCellRenderer
      data={{
        example_id: "e1",
        expected: { value: null },
        results: {
          [RUN]: { status: "completed", scores: { [METRIC]: score } },
        },
      }}
      context={{
        defsByKey: { [METRIC]: def },
        runsById: { [RUN]: { run_id: RUN, status: "Completed" } },
      }}
      runId={RUN}
      metricKey={METRIC}
    />,
  );

afterEach(cleanup);

describe("a choice evaluator's score cell on the Data tab", () => {
  it("shows Pass for a row the evaluator answered Pass", () => {
    renderCell(judge, assessed(1));
    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.queryByText("1")).toBeNull();
  });

  it("shows Fail for a row the evaluator answered Fail", () => {
    renderCell(judge, assessed(0));
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("shows Pass when every one of three tries passed", () => {
    renderCell(judge, assessed(1, 3));
    expect(screen.getByText("Pass")).toBeInTheDocument();
  });

  it("names the matching choice for one try", () => {
    renderCell(graded, assessed(0.5));
    expect(screen.getByText("Partial")).toBeInTheDocument();
  });

  it("keeps the number for tries that disagree", () => {
    // Two tries averaging 0.5 may be Good + Bad, never `Partial`.
    renderCell(graded, assessed(0.5, 2));
    expect(screen.getByText("0.50")).toBeInTheDocument();
    expect(screen.queryByText("Partial")).toBeNull();
  });

  it("keeps the number when the read gives no choice map", () => {
    renderCell({ ...judge, choice_scores: null }, assessed(1));
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.queryByText("Pass")).toBeNull();
  });
});
