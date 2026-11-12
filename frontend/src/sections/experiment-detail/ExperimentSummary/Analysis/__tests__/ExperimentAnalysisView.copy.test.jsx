// W10-EXPERIMENTS-DATASETS-U05: the Summary speaks customer language. Every
// reason the analysis read sends is a sentinel here: the page shows its own
// words, never the read's reason text, internal terms or evaluator version
// ids. Where the Summary does not compare cost and latency it says where they
// are (the Data tab) instead of why the build leaves them out.
import React from "react";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, screen } from "src/utils/test-utils";
import { adaptRunAnalysis } from "src/api/develop/analysis";
import ExperimentAnalysisView from "../ExperimentAnalysisView";

const hooks = vi.hoisted(() => ({ analysis: vi.fn(), stats: vi.fn() }));
vi.mock("src/api/develop/analysis", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunAnalysis: hooks.analysis,
}));
vi.mock("src/api/develop/experiment-stats", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentStats: hooks.stats,
}));

const REASON = "SERVER-REASON-SENTINEL";
const DATASET = "11111111-1111-4111-8111-111111111111";
const DATASET_VERSION = "22222222-2222-4222-8222-222222222222";
const RUN_A = "33333333-3333-4333-8333-333333333333";
const RUN_B = "44444444-4444-4444-8444-444444444444";
const VERSION_A = "55555555-5555-4555-8555-555555555555";
const VERSION_B = "66666666-6666-4666-8666-666666666666";
const INTERNAL_TERMS =
  /\bS1\b|immutable|canonical|unbound|work bound|pinned|\bpins?\b|adapter|classic pages/i;
const RAW_ID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

const unavailable = { state: "unavailable", reason: REASON };
const AVAILABILITY_KEYS = [
  "uncertainty", "candidate", "root_causes", "ops", "actions",
  "preference", "human", "agent", "worklist", "breakdown",
];

// #2 has no scored trial, a different evaluator version and no paired
// difference; nothing reports failures.
function analysisWith({ ops = unavailable } = {}) {
  return {
    dataset_id: DATASET,
    running: false,
    runs: [RUN_A, RUN_B].map((runId, index) => ({
      run_id: runId,
      dataset_id: DATASET,
      dataset_version_id: DATASET_VERSION,
      experiment_id: null,
      label: `#${index + 1}`,
      name: null,
      status: "completed",
      task_kind: "prompt",
      planned_trials: 2,
      recorded_trials: 2,
      missing_trials: 0,
    })),
    baseline_run_id: RUN_A,
    baseline_label: "#1",
    candidate_run_id: RUN_B,
    examples_in_view: 2,
    examples_total: 2,
    scope: { complete: true, population: "full_filtered_plan" },
    metrics: [{
      key: "accuracy",
      name: "Accuracy",
      family: "eval",
      aggregation: "assessed_trial_mean",
      denominator_unit: "example_trial",
      higher_is_better: true,
      pass_threshold: null,
      per_run: [
        { run_id: RUN_A, value: 0.5, n: 2, expected_n: 2, missing_n: 0,
          unavailable_reason: null, evaluator_version_id: VERSION_A, ci95: null,
          score_status_counts: { scored: 2 } },
        { run_id: RUN_B, value: null, n: 0, expected_n: 2, missing_n: 2,
          unavailable_reason: REASON, evaluator_version_id: VERSION_B, ci95: null,
          score_status_counts: {} },
      ],
      vs_baseline: [{
        baseline_run_id: RUN_A, run_id: RUN_B, delta: null, n: 0, expected_n: 2,
        ci95: null, p_value: null, significant: null,
        availability: { state: "incompatible", reason: REASON },
      }],
    }],
    verdicts: [],
    default_evals: [],
    breakdown_options: [],
    preference: null,
    human: null,
    agent: null,
    worklist: null,
    breakdown: null,
    pattern: null,
    root_causes: null,
    observed_failures: { groups: [], total: 0, has_more: false, availability: unavailable },
    availability: {
      ...Object.fromEntries(AVAILABILITY_KEYS.map((key) => [key, unavailable])),
      ops,
    },
  };
}

let analysis;

beforeEach(() => {
  vi.clearAllMocks();
  analysis = analysisWith();
  hooks.analysis.mockImplementation(() => ({
    data: analysis,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
  hooks.stats.mockReturnValue({
    data: {
      column_config: [{ name: "Accuracy" }],
      table_data: [],
      metadata: { is_winner_chosen: false },
    },
    isPending: false,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
});

function renderSummary(mode = "experiment") {
  return renderWithRouter(
    <Routes>
      <Route path="/dashboard/develop">
        <Route
          path="experiment/:experimentId/summary"
          element={
            <ExperimentAnalysisView
              datasetId={DATASET}
              runIds={[RUN_A, RUN_B]}
              baselineId={RUN_A}
              mode="experiment"
            />
          }
        />
        <Route
          path=":dataset/compare/summary"
          element={
            <ExperimentAnalysisView
              datasetId={DATASET}
              runIds={[RUN_A, RUN_B]}
              mode="compare"
            />
          }
        />
      </Route>
    </Routes>,
    {
      route: mode === "experiment"
        ? "/dashboard/develop/experiment/E1/summary"
        : "/dashboard/develop/D1/compare/summary",
    },
  );
}

describe("W10-EXPERIMENTS-DATASETS-U05 Summary copy", () => {
  it("uses analysis fixtures the real adapter accepts", () => {
    expect(adaptRunAnalysis({ status: true, result: analysisWith() }).runs).toHaveLength(2);
    expect(
      adaptRunAnalysis({
        status: true,
        result: analysisWith({ ops: { state: "available", reason: null } }),
      }).runs,
    ).toHaveLength(2);
  });

  it.each(["experiment", "compare"])(
    "shows no server reason, internal term or evaluator version id on the %s Summary",
    (mode) => {
      renderSummary(mode);

      expect(screen.getByRole("heading", { name: "Summary" })).toBeInTheDocument();
      const text = document.body.textContent;
      expect(text).not.toContain(REASON);
      expect(text).not.toMatch(INTERNAL_TERMS);
      expect(text).not.toMatch(RAW_ID);
      expect(screen.queryByRole("columnheader", { name: /evaluator version/i })).toBeNull();
    },
  );

  it("says cost and latency are on the Data tab when the Summary does not compare them", () => {
    renderSummary();

    const notice = screen.getByRole("link", { name: "Data tab" }).closest('[role="alert"]');
    expect(notice).toHaveTextContent("Cost and latency are not compared here.");
    expect(notice).toHaveTextContent(/Open the Data tab to see cost and latency/);
    expect(notice).not.toHaveTextContent(REASON);
  });

  it("does not mention cost and latency when the Summary compares them", () => {
    analysis = analysisWith({ ops: { state: "available", reason: null } });
    renderSummary();

    const notice = screen.getByRole("link", { name: "Data tab" }).closest('[role="alert"]');
    expect(notice).toHaveTextContent("Open the Data tab to re-score or download.");
    expect(notice).not.toHaveTextContent(/cost and latency/i);
  });
});
