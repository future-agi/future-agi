// W1-4 / W1-3: the leaderboard hover card counts the rows a value left out
// (N/A answers, score errors) and names a refused delta for what it is: two
// eval versions, not "did not score".
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalHoverCard from "../EvalHoverCard";

vi.mock("src/sections/common/Analysis/workbench/JudgeTrust", () => ({
  TrustLine: () => null,
  useJudgeTrust: () => null,
}));

const def = {
  key: "eval:custom:refund",
  name: "refund-policy-cited",
  output_type: "pass_fail",
  higher_is_better: true,
};
const base = { run_id: "r-a", label: "#2A", metrics: { aggregates: {} } };

describe("W1 EvalHoverCard", () => {
  it("counts N/A rows and score errors beside the value", () => {
    const run = {
      run_id: "r-b",
      label: "#2B",
      metrics: {
        aggregates: {
          [def.key]: {
            avg: 0.84,
            median: 1,
            std: 0.3,
            n: 11,
            n_na: 9,
            n_err: 1,
          },
        },
      },
    };
    render(<EvalHoverCard def={def} run={run} datasetId="d" />);
    expect(
      screen.getByText("9 N/A (45%) · 1 err · left out of the value"),
    ).toBeTruthy();
  });

  it("says Not compared for two eval versions, not 'did not score'", () => {
    const run = {
      run_id: "r-b",
      label: "#2A·2",
      metrics: {
        aggregates: {
          [def.key]: {
            avg: 0.7,
            median: 1,
            std: 0.4,
            n: 20,
            not_comparable_reason: "eval_versions_differ",
            versions_compared: { run: "v4", baseline: "v2" },
          },
        },
      },
    };
    render(
      <EvalHoverCard def={def} run={run} baselineRun={base} datasetId="d" />,
    );
    expect(
      screen.getByText(
        "Not compared: #2A·2 scored with v4, #2A with v2. Re-score one on the other's version to compare.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/did not score/)).toBeNull();
  });
});
