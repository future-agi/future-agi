// W4 (walk 1111, row 9): an experiment's Data page on first open. Both
// evaluations read `Not scored · no expected value` though the run's own
// evaluator had failed (Toxicity needs no expected answer), and an
// experiment with no Expected answers column said only `No expected value`.
// The score cell now says the evaluation failed; the Expected cell says
// which column to choose and where.
/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { notScoredReason } from "../cellCommon";
import { ScoreCellRenderer } from "../MetricCells";
import { ExpectedCell } from "../ExampleCells";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const RUN = "run-1";
const METRIC = "metric-toxicity";
const context = {
  defsByKey: { [METRIC]: { key: METRIC, name: "Toxicity" } },
  runsById: { [RUN]: { run_id: RUN, status: "Completed" } },
};
const rowWith = (score, expected = { value: null }) => ({
  example_id: "e1",
  expected,
  results: { [RUN]: { status: "completed", scores: { [METRIC]: score } } },
});
const failed = { value: null, status: "not_assessed", n: 0, error_trials: 1 };
const unscored = { value: null, status: "not_assessed", n: 0, error_trials: 0 };

describe("notScoredReason", () => {
  it("names a failed evaluation before guessing at the expected value", () => {
    expect(
      notScoredReason({
        run: { status: "Completed" },
        row: { expected: { value: null } },
        def: {},
        score: failed,
      }),
    ).toBe("the evaluation failed");
  });

  it("keeps the earlier reasons first", () => {
    expect(
      notScoredReason({
        run: { status: "Running" },
        row: {},
        def: {},
        score: failed,
      }),
    ).toBe("run not finished");
    expect(
      notScoredReason({
        run: { status: "Completed" },
        row: { expected: { value: null } },
        def: {},
        score: unscored,
      }),
    ).toBe("no expected value");
  });
});

describe("Score cell on the experiment Data page", () => {
  it("says the evaluation failed, not that the row has no expected value", () => {
    render(
      <ScoreCellRenderer
        data={rowWith(failed)}
        context={context}
        runId={RUN}
        metricKey={METRIC}
      />,
    );
    expect(
      screen.getByText("Not scored · the evaluation failed"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("no expected value");
  });

  it("does not blame Expected once the row has one", () => {
    render(
      <ScoreCellRenderer
        data={rowWith(unscored, { value: { expected_output: "Paris" } })}
        context={context}
        runId={RUN}
        metricKey={METRIC}
      />,
    );
    expect(document.body.textContent).not.toContain("no expected value");
  });
});

describe("Expected cell on the experiment Data page", () => {
  it("shows the expected column's value", () => {
    render(
      <ExpectedCell
        data={{
          example_id: "e1",
          expected: { value: { expected_output: "Paris" } },
        }}
        context={{}}
      />,
    );
    expect(screen.getAllByText("Paris").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("No expected value");
  });

  it("says which column to choose and where when the experiment has none", () => {
    render(
      <ExpectedCell
        data={{
          example_id: "e2",
          expected: { value: null, reason: "no_reference_column" },
        }}
        context={{}}
      />,
    );
    expect(
      screen.getByText("No expected column · choose one in Edit › Data"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("expected-no-column")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("No expected value");
  });

  it("keeps the plain empty line for any other row", () => {
    render(
      <ExpectedCell
        data={{ example_id: "e3", expected: { value: null } }}
        context={{}}
      />,
    );
    expect(screen.getByText("No expected value")).toBeInTheDocument();
  });
});
