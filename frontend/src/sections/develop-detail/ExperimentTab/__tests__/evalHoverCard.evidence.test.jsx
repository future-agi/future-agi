import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalHoverCard from "../EvalHoverCard";

vi.mock("src/sections/common/Analysis/workbench/JudgeTrust", () => ({
  TrustLine: () => null,
  useJudgeTrust: () => null,
}));

const def = {
  key: "40000000-0000-4000-8000-000000000001",
  name: "Correctness",
  output_type: "pass_fail",
  higher_is_better: true,
  trials_k: 3,
};
// The history adapter's consumer shape: two examples, three assessed tries
// each. n is the assessed-trial count, not independent example evidence.
const scoredRun = (run_id, label, value = 0.5) => ({
  run_id,
  label,
  status: "completed",
  trials: 3,
  progress: { done: 2, scored: 2, total: 2, errors: 0 },
  metrics: {
    [def.key]: { value, n: 6, rule: "assessed_trial_mean" },
    aggregates: {
      [def.key]: {
        avg: value,
        n: 6,
        rule: "assessed_trial_mean",
        median: null,
        ci95: null,
      },
    },
  },
});
const candidate = () => scoredRun("candidate", "#2");
const pairedUnavailable =
  "Paired statistics unavailable for Correctness vs #1. Both runs have scores, but no paired comparison is available.";
const unscoredBaseline =
  "#1 did not score Correctness, so there is nothing to compare.";
const expectNoDelta = () => {
  expect(screen.queryByText(/^vs #1 on /)).toBeNull();
  expect(screen.queryByText("How rows are counted")).toBeNull();
};

afterEach(cleanup);

describe("Hover-card metric evidence", () => {
  it("keeps the multi-trial canonical pass rate and count without an invented interval", () => {
    render(<EvalHoverCard def={def} run={candidate()} datasetId="dataset" />);
    expect(screen.getByText("Pass rate 50.0% · Std — · n 6").textContent).toBe(
      "Pass rate 50.0% · Std — · n 6",
    );
    expect(screen.queryByText(/^95% interval /)).toBeNull();
    expectNoDelta();
  });

  it.each([
    ["pass_fail", "95% interval 40.4%–59.6%"],
    ["score", "95% interval 0.46–0.54"],
  ])("retains the supported legacy %s interval", (output_type, expected) => {
    const run = {
      ...candidate(),
      metrics: {
        aggregates: {
          [def.key]: { avg: 0.5, median: 0.5, std: 0.2, n: 100 },
        },
      },
    };
    render(
      <EvalHoverCard
        def={{ ...def, output_type }}
        run={run}
        datasetId="dataset"
      />,
    );
    expect(screen.getByText(/^95% interval /).textContent).toBe(expected);
  });

  it.each([0, 0.25])(
    "names absent pairing when the baseline has a scored mean of %s",
    (value) => {
      render(
        <EvalHoverCard
          def={def}
          run={candidate()}
          baselineRun={scoredRun("baseline", "#1", value)}
          datasetId="dataset"
        />,
      );
      expect(screen.getByText(pairedUnavailable).textContent).toBe(
        pairedUnavailable,
      );
      expect(screen.queryByText(unscoredBaseline)).toBeNull();
      expectNoDelta();
    },
  );

  it.each([
    ["absent", {}],
    ["unassessed", { [def.key]: { avg: null, n: 0 } }],
  ])("names a genuinely %s baseline score", (_, aggregates) => {
    render(
      <EvalHoverCard
        def={def}
        run={candidate()}
        baselineRun={{
          run_id: "baseline",
          label: "#1",
          metrics: { aggregates },
        }}
        datasetId="dataset"
      />,
    );
    expect(screen.getByText(unscoredBaseline).textContent).toBe(
      unscoredBaseline,
    );
    expect(screen.queryByText(pairedUnavailable)).toBeNull();
    expectNoDelta();
  });

  it("keeps evaluator-version refusal ahead of the missing-pairing explanation", () => {
    const run = candidate();
    Object.assign(run.metrics.aggregates[def.key], {
      not_comparable_reason: "eval_versions_differ",
      versions_compared: { run: "v4", baseline: "v2" },
    });
    render(
      <EvalHoverCard
        def={def}
        run={run}
        baselineRun={scoredRun("baseline", "#1")}
        datasetId="dataset"
      />,
    );
    expect(screen.getByText(/^Not compared:/).textContent).toBe(
      "Not compared: #2 scored with v4, #1 with v2. Re-score one on the other's version to compare.",
    );
    expect(screen.queryByText(pairedUnavailable)).toBeNull();
    expect(screen.queryByText(unscoredBaseline)).toBeNull();
    expectNoDelta();
  });
});
