// The row drawer's score table showed a choice evaluator's stored score
// (`1`) for each run instead of its label (`Pass`).
/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import ScoreMatrix from "../ScoreMatrix";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Runs/RunChip", () => ({
  default: ({ run }) => <span>{run.label}</span>,
}));

const A = "run-a";
const B = "run-b";
const METRIC = "metric-judge";
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
const assessed = (value, n = 1) => ({ value, n, status: "assessed" });

afterEach(cleanup);

describe("a choice evaluator in the row drawer", () => {
  it("reads each run's label", () => {
    render(
      <ScoreMatrix
        metrics={[judge]}
        runs={[
          { run_id: A, label: "#1" },
          { run_id: B, label: "#2" },
        ]}
        colors={{}}
        baselineId={A}
        row={{
          example_id: "e1",
          results: {
            [A]: { status: "completed", scores: { [METRIC]: assessed(1) } },
            [B]: { status: "completed", scores: { [METRIC]: assessed(0, 2) } },
          },
        }}
        onPick={vi.fn()}
      />,
    );
    expect(screen.getByText("judge")).toBeInTheDocument();
    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.queryByText("1")).toBeNull();
    expect(screen.queryByText("0")).toBeNull();
  });
});
