// W10-EXPERIMENTS-DATASETS-U04: the Summary must not end in actions nobody can
// take. Re-score and Download stay disabled on the Summary, so it names the
// Data tab, where both are, and links to it with the page's selection. It
// never sends users to classic pages and never links back to itself.
import React from "react";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, screen } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
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

const DATASET = "11111111-1111-4111-8111-111111111111";
const VERSION = "22222222-2222-4222-8222-222222222222";
const RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const EXPERIMENT_SUMMARY = "/dashboard/develop/experiment/E1/summary";
const COMPARE_SUMMARY = "/dashboard/develop/D1/compare/summary";

const available = { state: "available", reason: null };
const unavailable = (reason) => ({ state: "unavailable", reason });

// Two completed runs, no metrics; the reasons are the analysis read's own.
const analysis = {
  dataset_id: DATASET,
  running: false,
  runs: [RUN_A, RUN_B].map((runId, index) => ({
    run_id: runId,
    dataset_id: DATASET,
    dataset_version_id: VERSION,
    experiment_id: null,
    label: `#${index + 1}`,
    name: `Prompt ${index + 1}`,
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
  metrics: [],
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
  observed_failures: null,
  availability: {
    uncertainty: unavailable("Confidence intervals are not estimated."),
    candidate: available,
    root_causes: unavailable("Root causes are not projected."),
    ops: unavailable("Cost and latency analysis are not projected by this S1 adapter."),
    // The page must not relay this reason.
    actions: unavailable(
      "Choose winner, rescore and download need verified canonical-to-native mappings; use classic pages.",
    ),
    preference: unavailable("No preference data."),
    human: unavailable("No human scores."),
    agent: unavailable("No agent traces."),
    worklist: unavailable("No worklist."),
    breakdown: unavailable("No breakdown selected."),
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  // The staging build is a launch build, so the scope notice renders.
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: madeRealSurfaceIds(),
  });
  hooks.analysis.mockImplementation((_datasetId, { runIds }) => ({
    data: runIds.length ? analysis : undefined,
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
  setLaunchOverrideForTest(null);
});

function renderSummary(path) {
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
    { route: path },
  );
}

const linkPaths = () =>
  screen
    .queryAllByRole("link")
    .map((link) => new URL(link.getAttribute("href"), "http://app.test").pathname);

describe("W10-EXPERIMENTS-DATASETS-U04 Summary actions", () => {
  it("names the Data tab for Re-score and Download on the experiment Summary, keeping the selection", () => {
    renderSummary(`${EXPERIMENT_SUMMARY}?baseline=${RUN_A}`);

    expect(screen.getByRole("button", { name: "Re-score" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    const dataTab = screen.getByRole("link", { name: "Data tab" });
    expect(dataTab).toHaveAttribute(
      "href",
      `/dashboard/develop/experiment/E1/data?baseline=${RUN_A}`,
    );
    const notice = dataTab.closest('[role="alert"]');
    expect(notice).toHaveTextContent("Re-score and Download are not available on this page.");
    expect(notice).toHaveTextContent(/Open the Data tab to .*re-score or download\./);
    // The existing door to the Data tab keeps the same selection.
    expect(screen.getByRole("link", { name: "Open Results data" })).toHaveAttribute(
      "href",
      `/dashboard/develop/experiment/E1/data?baseline=${RUN_A}`,
    );
  });

  it("names the Data tab for Re-score and Download on Compare's Summary", () => {
    renderSummary(`${COMPARE_SUMMARY}?baseline=${RUN_A}`);

    expect(screen.getByRole("button", { name: "Choose winner" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Re-score" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    const dataTab = screen.getByRole("link", { name: "Data tab" });
    expect(dataTab).toHaveAttribute(
      "href",
      `/dashboard/develop/D1/compare/data?baseline=${RUN_A}`,
    );
    expect(dataTab.closest('[role="alert"]')).toHaveTextContent(
      /Open the Data tab to .*re-score or download\./,
    );
  });

  it("never sends users to classic pages or links back to the same Summary", () => {
    renderSummary(`${EXPERIMENT_SUMMARY}?baseline=${RUN_A}`);

    // W6: the Summary no longer mounts the launch scope notice.
    expect(screen.queryByTestId("results-launch-scope")).toBeNull();
    expect(document.body).not.toHaveTextContent(/classic/i);
    expect(screen.queryByRole("link", { name: /classic summary/i })).toBeNull();
    expect(linkPaths()).not.toContain(EXPERIMENT_SUMMARY);
  });
});
