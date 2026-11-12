// W33-SUMMARY-NAMES: the experiment Summary names runs and evaluations the
// way the Data tab does (`Run B-gpt-4o-mini`, `output-check`), never as bare
// run numbers or by an internal id.
import React from "react";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import { adaptRunAnalysis } from "src/api/develop/analysis";
import ExperimentAnalysisView from "../ExperimentAnalysisView";
import { summaryEvaluationName, summaryRunName } from "../summaryNames";

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
const DATASET_VERSION = "22222222-2222-4222-8222-222222222222";
const RUN_A = "33333333-3333-4333-8333-333333333333";
const RUN_B = "44444444-4444-4444-8444-444444444444";
const METRIC = "55555555-5555-4555-8555-555555555555";
const EVALUATOR_VERSION = "66666666-6666-4666-8666-666666666666";
const EXAMPLE = "77777777-7777-4777-8777-777777777777";
const RAW_ID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

const available = { state: "available", reason: null };
const unavailable = { state: "unavailable", reason: "Not compared here." };

const run = (runId, label, name) => ({
  run_id: runId,
  dataset_id: DATASET,
  dataset_version_id: DATASET_VERSION,
  experiment_id: null,
  label,
  name,
  status: "completed",
  task_kind: "prompt",
  planned_trials: 2,
  recorded_trials: 2,
  missing_trials: 0,
});

const perRun = (runId, value) => ({
  run_id: runId,
  value,
  n: 2,
  expected_n: 2,
  missing_n: 0,
  unavailable_reason: null,
  evaluator_version_id: EVALUATOR_VERSION,
  ci95: null,
  score_status_counts: { assessed: 2 },
});

// The analysis read as the walk saw it: run numbers as labels, the names the
// Data tab shows, and one evaluation that #2 failed once.
function analysisWith({
  runs = [
    run(RUN_A, "#1", "Run A-gpt-4o-mini"),
    run(RUN_B, "#2", "Run B-gpt-4o-mini"),
  ],
  metricName = "output-check",
} = {}) {
  return {
    dataset_id: DATASET,
    running: false,
    runs,
    baseline_run_id: RUN_A,
    baseline_label: runs[0].label,
    candidate_run_id: RUN_B,
    examples_in_view: 2,
    examples_total: 2,
    scope: { complete: true, population: "full_filtered_plan" },
    metrics: [
      {
        key: METRIC,
        name: metricName,
        family: "eval",
        aggregation: "assessed_trial_mean",
        denominator_unit: "example_trial",
        higher_is_better: true,
        pass_threshold: 0.5,
        per_run: [perRun(RUN_A, 1), perRun(RUN_B, 0.5)],
        vs_baseline: [
          {
            baseline_run_id: RUN_A,
            run_id: RUN_B,
            delta: -0.5,
            n: 2,
            expected_n: 2,
            ci95: null,
            p_value: null,
            significant: null,
            availability: available,
          },
        ],
      },
    ],
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
    observed_failures: {
      groups: [
        {
          run_id: RUN_B,
          metric: METRIC,
          failed_trials: 1,
          assessed_trials: 2,
          examples_total: 1,
          example_ids: [EXAMPLE],
          examples_has_more: false,
          causal: false,
        },
      ],
      total: 1,
      has_more: false,
      availability: available,
    },
    availability: {
      uncertainty: unavailable,
      candidate: unavailable,
      root_causes: unavailable,
      ops: unavailable,
      actions: unavailable,
      preference: unavailable,
      human: unavailable,
      agent: unavailable,
      worklist: unavailable,
      breakdown: unavailable,
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
      column_config: [{ name: "output-check" }],
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

function renderSummary() {
  return renderWithRouter(
    <Routes>
      <Route
        path="/dashboard/develop/experiment/:experimentId/summary"
        element={
          <ExperimentAnalysisView
            datasetId={DATASET}
            runIds={[RUN_A, RUN_B]}
            baselineId={RUN_A}
            mode="experiment"
          />
        }
      />
    </Routes>,
    { route: "/dashboard/develop/experiment/E1/summary" },
  );
}

const runCells = (table) =>
  within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]);

describe("W33-SUMMARY-NAMES Summary names runs and evaluations", () => {
  it("uses analysis fixtures the real adapter accepts", () => {
    expect(adaptRunAnalysis({ status: true, result: analysisWith() }).runs).toHaveLength(2);
    expect(
      adaptRunAnalysis({
        status: true,
        result: analysisWith({
          runs: [run(RUN_A, RUN_A, null), run(RUN_B, RUN_B, "Run B-gpt-4o-mini")],
          metricName: METRIC,
        }),
      }).runs,
    ).toHaveLength(2);
  });

  it("names each run in the values table by the name the Data tab shows", () => {
    renderSummary();
    const table = screen.getByRole("table", { name: "Values for output-check" });
    const cells = runCells(table);
    expect(within(cells[0]).getByText("Run A-gpt-4o-mini (#1)")).toBeVisible();
    expect(within(cells[1]).getByText("Run B-gpt-4o-mini (#2)")).toBeVisible();
    expect(within(table).queryByText("#1")).toBeNull();
    expect(within(table).queryByText("#2")).toBeNull();
  });

  it("names the runs in the baseline and candidate pickers", () => {
    renderSummary();
    const baseline = screen.getByRole("combobox", { name: "Analysis baseline" });
    expect(
      within(baseline).getAllByRole("option").map((option) => option.textContent),
    ).toEqual([
      "None — absolute values",
      "Run A-gpt-4o-mini (#1)",
      "Run B-gpt-4o-mini (#2)",
    ]);
    const candidate = screen.getByRole("combobox", { name: "Analysis candidate" });
    expect(
      within(candidate).getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Run B-gpt-4o-mini (#2)"]);
  });

  it("names the evaluation and the failing run, never an id", () => {
    renderSummary();
    expect(screen.getByRole("heading", { name: "output-check" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Metric output-check" })).toBeInTheDocument();
    expect(
      screen.getByText("Run B-gpt-4o-mini (#2): 1 of 2 scored trials failed output-check."),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(RAW_ID);
  });

  it("shows no id when the read names an evaluation or a run by its id", () => {
    analysis = analysisWith({
      runs: [run(RUN_A, RUN_A, null), run(RUN_B, RUN_B, "Run B-gpt-4o-mini")],
      metricName: METRIC,
    });
    renderSummary();
    expect(screen.getByRole("heading", { name: "Unnamed evaluation" })).toBeVisible();
    const table = screen.getByRole("table", { name: "Values for Unnamed evaluation" });
    const cells = runCells(table);
    expect(within(cells[0]).getByText("Unnamed run")).toBeVisible();
    expect(within(cells[1]).getByText("Run B-gpt-4o-mini")).toBeVisible();
    expect(
      screen.getByText("Run B-gpt-4o-mini: 1 of 2 scored trials failed Unnamed evaluation."),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(RAW_ID);
  });

  it("keeps the run number for a run that has no name", () => {
    analysis = analysisWith({
      runs: [run(RUN_A, "#1", null), run(RUN_B, "#2", "  ")],
    });
    renderSummary();
    const cells = runCells(screen.getByRole("table", { name: "Values for output-check" }));
    expect(within(cells[0]).getByText("#1")).toBeVisible();
    expect(within(cells[1]).getByText("#2")).toBeVisible();
    expect(document.body.textContent).not.toMatch(/undefined|null/);
  });
});

describe("summaryRunName / summaryEvaluationName", () => {
  it("joins the run name and number the way the saved ranking does", () => {
    expect(summaryRunName({ run_id: RUN_A, label: "#2", name: "Run B-gpt-4o-mini" }))
      .toBe("Run B-gpt-4o-mini (#2)");
    expect(summaryRunName({ run_id: RUN_A, label: "#2", name: "#2" })).toBe("#2");
    expect(summaryRunName({ run_id: RUN_A, label: "#2", name: null })).toBe("#2");
    expect(summaryRunName({ run_id: RUN_A, label: RUN_A, name: "Prompt A" })).toBe("Prompt A");
    expect(summaryRunName({ run_id: RUN_A, label: RUN_A, name: null })).toBe("Unnamed run");
    expect(summaryRunName({ run_id: RUN_A, label: undefined, name: undefined })).toBe("Unnamed run");
    expect(summaryRunName(undefined)).toBe("Unnamed run");
  });

  it("never names an evaluation by its id", () => {
    expect(summaryEvaluationName({ key: METRIC, name: "output-check" })).toBe("output-check");
    expect(summaryEvaluationName({ key: METRIC, name: METRIC })).toBe("Unnamed evaluation");
    expect(summaryEvaluationName({ key: METRIC, name: " " })).toBe("Unnamed evaluation");
    expect(summaryEvaluationName(undefined)).toBe("Unnamed evaluation");
  });
});
