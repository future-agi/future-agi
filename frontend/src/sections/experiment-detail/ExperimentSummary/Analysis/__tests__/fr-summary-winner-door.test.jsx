// FR-EXPERIMENTS-02: the experiment Summary is the last step of a first run
// (compare the runs, then pick a winner). Choose winner must open the
// existing chooser for this experiment, the saved ranking must show on the
// Summary and name runs the way the comparison tables do, and no control may
// send the user back to this same page. The page speaks plain words: no
// internal stage names, no raw ids, and no reason text copied from the
// analysis read's internals.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderHook,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import * as launchSwitch from "src/launch/launchSwitch";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const hooks = vi.hoisted(() => ({
  analysis: vi.fn(),
  stats: vi.fn(),
  drawer: vi.fn(),
  http: { get: vi.fn() },
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: hooks.http,
}));
vi.mock("src/api/develop/analysis", () => ({ useRunAnalysis: hooks.analysis }));
vi.mock("src/api/develop/experiment-stats", () => ({
  useExperimentStats: hooks.stats,
}));
/* eslint-disable react/prop-types */
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ChooseWinnerDrawer",
  () => ({
    default: function ChooseWinnerDrawerProbe(props) {
      hooks.drawer(props);
      return props.open ? (
        <div data-testid="winner-drawer">
          <button type="button" onClick={props.onClose}>
            Close drawer
          </button>
        </div>
      ) : null;
    },
  }),
);
/* eslint-enable react/prop-types */

import ExperimentAnalysisView from "../ExperimentAnalysisView";

const SUMMARY_PATH = "/dashboard/develop/experiment/E1/summary";
const COMPARE_PATH = "/dashboard/develop/D1/compare/summary";
const DATASET = "11111111-1111-4111-8111-111111111111";
const VERSION = "22222222-2222-4222-8222-222222222222";
const EVALUATOR = "33333333-3333-4333-8333-333333333333";
const EVALUATOR_NEXT = "44444444-4444-4444-8444-444444444444";
const EXAMPLE = "55555555-5555-4555-8555-555555555555";
const RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
// The analysis read labels runs by ordinal and keeps the variant name.
const LABELS = ["#1", "#2"];
const NAMES = ["Prompt A", "Prompt B"];

// Words that belong to the build, not to the product.
const INTERNAL_TERMS =
  /\bS1\b|canonical|immutable|\bpins?\b|pinned|fabricated|work bound|population|checkpoint|snapshot|topology|adapter|classic pages|in this scope/i;
const RAW_ID =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

const available = { state: "available", reason: null };
const unavailable = (reason) => ({ state: "unavailable", reason });
const run = (runId, index) => ({
  run_id: runId,
  dataset_id: DATASET,
  dataset_version_id: VERSION,
  experiment_id: null,
  label: LABELS[index],
  name: NAMES[index],
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
  evaluator_version_id: EVALUATOR,
  ci95: null,
  score_status_counts: { scored: 2 },
});

// Valid against adaptRunAnalysis (checked below): one or two runs, one metric.
function analysisFor(runIds) {
  const runs = runIds.map((id, index) => run(id, index));
  const baseline = runIds.length > 1 ? runIds[0] : null;
  return {
    dataset_id: DATASET,
    running: false,
    runs,
    baseline_run_id: baseline,
    baseline_label: baseline ? LABELS[0] : null,
    candidate_run_id: baseline ? runIds[1] : null,
    examples_in_view: 2,
    examples_total: 2,
    scope: { complete: true, population: "full_filtered_plan" },
    metrics: [
      {
        key: "accuracy",
        name: "Accuracy",
        family: "eval",
        aggregation: "assessed_trial_mean",
        denominator_unit: "example_trial",
        higher_is_better: true,
        pass_threshold: null,
        per_run: runIds.map((id, index) => perRun(id, 0.5 + index / 4)),
        vs_baseline: baseline
          ? runIds.slice(1).map((id) => ({
              baseline_run_id: baseline,
              run_id: id,
              delta: 0.25,
              n: 2,
              expected_n: 2,
              ci95: null,
              p_value: null,
              significant: null,
              availability: available,
            }))
          : [],
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
    observed_failures: null,
    availability: {
      uncertainty: unavailable("Confidence intervals are not computed."),
      candidate: available,
      root_causes: unavailable("Root causes are not computed."),
      ops: unavailable("Latency and cost are not compared here."),
      actions: unavailable("Bulk actions are not offered here."),
      preference: unavailable("No preference data."),
      human: unavailable("No human scores."),
      agent: unavailable("No agent traces."),
      worklist: unavailable("No worklist."),
      breakdown: unavailable("No breakdown selected."),
    },
  };
}

// Two runs where #2 failed one of its two scored trials.
function analysisWithFailure(runIds) {
  const analysis = analysisFor(runIds);
  return {
    ...analysis,
    observed_failures: {
      groups: [
        {
          run_id: runIds[1],
          metric: "accuracy",
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
  };
}

// The reasons exactly as the analysis read words them
// (futureagi/model_hub/services/results_analysis.py, results_root_causes.py):
// #2 has no scored trial and a different evaluator version, so its paired
// difference is incompatible, and no evaluator reports failures.
function serverWordedAnalysis(runIds) {
  const analysis = analysisFor(runIds);
  const [metric] = analysis.metrics;
  return {
    ...analysis,
    metrics: [
      {
        ...metric,
        per_run: [
          perRun(runIds[0], 0.5),
          {
            ...perRun(runIds[1], null),
            n: 0,
            unavailable_reason: "No assessed trials in this scope.",
            evaluator_version_id: EVALUATOR_NEXT,
            score_status_counts: {},
          },
        ],
        vs_baseline: [
          {
            baseline_run_id: runIds[0],
            run_id: runIds[1],
            delta: null,
            n: 0,
            expected_n: 2,
            ci95: null,
            p_value: null,
            significant: null,
            availability: {
              state: "incompatible",
              reason: "Evaluator version or configuration snapshots differ.",
            },
          },
        ],
      },
    ],
    observed_failures: {
      groups: [],
      total: 0,
      has_more: false,
      availability: unavailable(
        "No assessed evaluations with a usable pinned pass/fail rule in this scope.",
      ),
    },
    availability: {
      uncertainty: unavailable(
        "Confidence intervals, p-values and significance are not estimated in S1.",
      ),
      candidate: unavailable(
        "Candidate is explicit or the first selected non-baseline run, not a ranked winner.",
      ),
      root_causes: unavailable(
        "Causal diagnosis is unavailable. No project-authorized trace topology join is included in S1; failed evaluations are observations, not causes.",
      ),
      ops: unavailable(
        "Cost and latency analysis are not projected by this S1 adapter.",
      ),
      actions: unavailable(
        "Choose winner, rescore and download need verified canonical-to-native mappings; use classic pages.",
      ),
      preference: unavailable(
        "Preference evidence is not available in this S1 adapter.",
      ),
      human: unavailable("Human evidence is not available in this S1 adapter."),
      agent: unavailable("Agent evidence is not available in this S1 adapter."),
      worklist: unavailable(
        "Worklist evidence is not available in this S1 adapter.",
      ),
      breakdown: unavailable(
        "Breakdown evidence is not available in this S1 adapter.",
      ),
    },
  };
}

// Both runs cover the same two examples, but #2 plans two trials per example
// where the baseline plans one. The plans differ only in trials, and the
// analysis read reports it in its own words (results_analysis.py).
function trialPlanAnalysis(runIds) {
  const analysis = analysisFor(runIds);
  const [metric] = analysis.metrics;
  return {
    ...analysis,
    runs: analysis.runs.map((item, index) =>
      index === 1 ? { ...item, planned_trials: 4, recorded_trials: 4 } : item,
    ),
    metrics: [
      {
        ...metric,
        per_run: [
          perRun(runIds[0], 0.5),
          { ...perRun(runIds[1], 0.75), n: 4, expected_n: 4 },
        ],
        vs_baseline: [
          {
            baseline_run_id: runIds[0],
            run_id: runIds[1],
            delta: null,
            n: 0,
            expected_n: 4,
            ci95: null,
            p_value: null,
            significant: null,
            availability: {
              state: "incompatible",
              reason: "Runs do not share the exact example/trial plan.",
            },
          },
        ],
      },
    ],
  };
}

const NO_WINNER_STATS = {
  column_config: [{ name: "Accuracy" }],
  table_data: [],
  metadata: { is_winner_chosen: false },
};
const WINNER_STATS = {
  column_config: [{ name: "Accuracy" }],
  table_data: [
    {
      dataset_id: "ds-a",
      experiment_dataset_name: "Prompt A",
      Accuracy: 50,
      rank: 2,
    },
    {
      dataset_id: "ds-b",
      experiment_dataset_name: "Prompt B",
      Accuracy: 75,
      rank: 1,
    },
    // A ranked row no analysis run carries (the experiment's base column).
    {
      dataset_id: "ds-base",
      experiment_dataset_name: "Base column",
      Accuracy: 25,
      rank: 3,
    },
  ],
  metadata: { is_winner_chosen: true },
};

let stats;
let statsQuery;
let buildAnalysis;
const clients = [];

beforeEach(() => {
  vi.clearAllMocks();
  stats = NO_WINNER_STATS;
  statsQuery = null;
  buildAnalysis = analysisFor;
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: madeRealSurfaceIds(),
  });
  // Like the real hook, no run ids means no analysis read.
  hooks.analysis.mockImplementation((_datasetId, { runIds }) => ({
    data: runIds.length ? buildAnalysis(runIds) : undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }));
  // The hook is the react-query result of the stats read; `data` is the
  // selected `result` (column_config, table_data, metadata).
  hooks.stats.mockImplementation(
    () =>
      statsQuery ?? {
        data: stats,
        isPending: false,
        isLoading: false,
        isError: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
  );
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
});

function newClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  return client;
}

function renderSummary({ runIds = [RUN_A, RUN_B], path = SUMMARY_PATH } = {}) {
  return renderWithRouter(
    <QueryClientProvider client={newClient()}>
      <Routes>
        <Route path="/dashboard/develop">
          <Route
            path="experiment/:experimentId/summary"
            element={
              <ExperimentAnalysisView
                datasetId={DATASET}
                runIds={runIds}
                baselineId={runIds.length < 2 ? "none" : undefined}
                mode="experiment"
              />
            }
          />
          <Route
            path=":dataset/compare/summary"
            element={
              <ExperimentAnalysisView
                datasetId={DATASET}
                runIds={runIds}
                mode="compare"
              />
            }
          />
        </Route>
      </Routes>
    </QueryClientProvider>,
    { route: path },
  );
}

const hrefPaths = () =>
  [...document.querySelectorAll("[href]")].map(
    (el) => new URL(el.getAttribute("href"), "http://app.test").pathname,
  );
const statsReadEnabled = () =>
  hooks.stats.mock.calls.some(
    ([experimentId, options]) =>
      Boolean(experimentId) && options?.enabled !== false,
  );

describe("FR-EXPERIMENTS-02 Summary winner door", () => {
  it("uses analysis fixtures the real adapter accepts", async () => {
    const { adaptRunAnalysis } = await vi.importActual(
      "src/api/develop/analysis",
    );
    for (const build of [
      analysisFor,
      analysisWithFailure,
      serverWordedAnalysis,
      trialPlanAnalysis,
    ])
      expect(
        adaptRunAnalysis({ status: true, result: build([RUN_A, RUN_B]) }).runs,
      ).toHaveLength(2);
    expect(
      adaptRunAnalysis({ status: true, result: analysisFor([RUN_A]) }).runs,
    ).toHaveLength(1);
  });

  it("(1) offers Choose winner as an enabled action on a two-run experiment", () => {
    renderSummary();
    const choose = screen.getByRole("button", { name: "Choose winner" });
    expect(choose).toBeEnabled();
    expect(choose).not.toHaveAttribute("aria-describedby");
    expect(hooks.stats).toHaveBeenCalled();
    expect(hooks.stats.mock.calls.at(-1)[0]).toBe("E1");
    expect(statsReadEnabled()).toBe(true);
  });

  it("(2) opens the existing chooser for this experiment with the stats evals", () => {
    renderSummary();
    expect(screen.queryByTestId("winner-drawer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Choose winner" }));
    expect(screen.getByTestId("winner-drawer")).toBeInTheDocument();
    const props = hooks.drawer.mock.calls.at(-1)[0];
    expect(props.open).toBe(true);
    expect(props.experimentId).toBe("E1");
    expect(props.evalsList).toEqual([{ name: "Accuracy" }]);

    fireEvent.click(screen.getByRole("button", { name: "Close drawer" }));
    expect(screen.queryByTestId("winner-drawer")).toBeNull();
  });

  it("(3) has no link or button that targets this Summary's own URL", () => {
    renderSummary();
    expect(screen.queryByRole("link", { name: "Classic summary" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Classic summary" })).toBeNull();
    const paths = hrefPaths();
    expect(paths).not.toContain(SUMMARY_PATH);
    // The Results data door stays.
    expect(paths).toContain("/dashboard/develop/experiment/E1/data");
  });

  it("(4) does not send users to classic pages", () => {
    renderSummary();
    // W6: the Summary no longer mounts the launch scope notice.
    expect(screen.queryByTestId("results-launch-scope")).toBeNull();
    expect(document.body.textContent).not.toContain("Use the classic pages");
    expect(document.body.textContent).not.toMatch(/classic (pages|summary|results)/i);
  });

  it("(4b) says once, in plain words, that the experiment has no runs", () => {
    renderSummary({ runIds: [] });
    expect(
      screen.getByText("This experiment has no runs to summarize."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Choose winner" }),
    ).toHaveAccessibleDescription("Choose winner ranks two or more runs.");
    expect(screen.getAllByText(/no runs/i)).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/canonical/i);
    expect(document.body.textContent).not.toMatch(/classic (pages|summary|results)/i);
  });

  it("(5) names ranked runs the way the comparison tables do", () => {
    stats = WINNER_STATS;
    renderSummary();
    const ranking = screen.getByRole("region", { name: "Saved ranking" });
    expect(within(ranking).getByText("Winner: Prompt B (#2)")).toBeVisible();
    const rows = within(ranking)
      .getAllByRole("row")
      .slice(1)
      .map((row) =>
        within(row)
          .getAllByRole("cell")
          .map((cell) => cell.textContent),
      );
    expect(rows).toEqual([
      ["1", "Prompt B (#2)"],
      ["2", "Prompt A (#1)"],
      ["3", "Base column"],
    ]);
    // The comparison table names the runs the same way.
    const table = screen.getByRole("table", { name: "Values for Accuracy" });
    expect(within(table).getByText("Prompt A (#1)")).toBeVisible();
    expect(within(table).getByText("Prompt B (#2)")).toBeVisible();
  });

  it("(5b) shows no saved ranking before a winner is chosen", () => {
    renderSummary();
    expect(screen.queryByRole("region", { name: "Saved ranking" })).toBeNull();
  });

  it("(6) explains that a one-run experiment needs two runs for a winner", () => {
    renderSummary({ runIds: [RUN_A] });
    const buttons = screen.queryAllByRole("button", { name: "Choose winner" });
    expect(buttons.filter((button) => !button.disabled)).toEqual([]);
    expect(screen.getByText(/Choose winner ranks two or more runs/)).toBeVisible();
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleDescription(
      "Choose winner ranks two or more runs. This experiment has one run.",
    );
    expect(statsReadEnabled()).toBe(false);
  });

  it("(7) leaves Compare's Summary actions unchanged", () => {
    stats = WINNER_STATS;
    renderSummary({ path: COMPARE_PATH });
    expect(screen.getByRole("button", { name: "Choose winner" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Re-score" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    expect(
      screen.getByText(/Choose winner, Re-score and Download are not available on this page/),
    ).toBeVisible();
    expect(screen.queryByRole("region", { name: "Saved ranking" })).toBeNull();
    expect(screen.queryByText(/two or more runs/)).toBeNull();
    expect(screen.queryByTestId("winner-drawer")).toBeNull();
    expect(hooks.drawer).not.toHaveBeenCalled();
    expect(statsReadEnabled()).toBe(false);
  });

  it("keeps Re-score and Download disabled on the experiment Summary", () => {
    renderSummary();
    expect(screen.getByRole("button", { name: "Re-score" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("(8) uses plain words on the Summary, with no internal terms or raw ids", () => {
    buildAnalysis = analysisWithFailure;
    renderSummary();
    const text = document.body.textContent;
    expect(text).not.toMatch(INTERNAL_TERMS);
    expect(text).not.toMatch(RAW_ID);
    expect(
      screen.queryByRole("columnheader", { name: "Evaluator version" }),
    ).toBeNull();
    expect(
      screen.getByText(/All runs were scored with the same evaluator version/),
    ).toBeVisible();
    expect(
      screen.getByText("Prompt B (#2): 1 of 2 scored trials failed Accuracy."),
    ).toBeVisible();
    expect(screen.getByText(/They show what failed, not why/)).toBeVisible();
  });

  it("(8b) turns the analysis read's internal reasons into plain words", () => {
    buildAnalysis = serverWordedAnalysis;
    renderSummary();
    expect(document.body.textContent).not.toMatch(INTERNAL_TERMS);
    expect(document.body.textContent).not.toMatch(RAW_ID);
    const table = screen.getByRole("table", { name: "Values for Accuracy" });
    expect(
      within(table).getByText(
        "Uses a different evaluator version or settings than the baseline.",
      ),
    ).toBeVisible();
    expect(within(table).getByText("No scored trials.")).toBeVisible();
    expect(
      screen.getByText(/scored with different evaluator versions/),
    ).toBeVisible();
    expect(screen.getByText(/No failures to show/)).toBeVisible();
    expect(screen.queryByText(/They show what failed/)).toBeNull();
    expect(
      screen.getByText(/Re-score and Download are not available on this page/),
    ).toBeVisible();
  });

  it("(8c) lists no hidden Results parts on the Summary, even when they are hidden", () => {
    // W6: the launch scope notice is no longer on the Summary, so hiding the
    // Results surfaces adds no list of them to this page.
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false, madeReal: [] });
    const shown = vi.spyOn(launchSwitch, "isSurfaceShown").mockReturnValue(false);
    try {
      renderSummary();
      expect(screen.queryByTestId("results-launch-scope")).toBeNull();
      expect(document.body.textContent).not.toMatch(/Not available here:/);
      expect(document.body.textContent).not.toMatch(/checkpoint/i);
    } finally {
      shown.mockRestore();
    }
  });

  it("(8d) keeps the word failed for trials that failed an evaluation", () => {
    buildAnalysis = analysisWithFailure;
    renderSummary();
    // Trials that ended in an error are left out of averages; trials that
    // failed an evaluation are scored, counted in the average and listed
    // under Observed failures.
    const averages = screen.getByText(/Averages use scored trials only/);
    expect(averages).toHaveTextContent(
      "Missing, errored and unscored trials are left out, not counted as zero.",
    );
    expect(averages.textContent).not.toMatch(/\bfailed\b/i);
    const failures = screen
      .getByRole("heading", { name: "Observed failures" })
      .closest("section");
    const said = screen.getAllByText(/\bfailed\b/i);
    expect(said.length).toBeGreaterThan(0);
    said.forEach((element) => expect(failures).toContainElement(element));
  });

  it("(8e) says a run with a different trial plan differs in examples or trials", () => {
    buildAnalysis = trialPlanAnalysis;
    renderSummary();
    const table = screen.getByRole("table", { name: "Values for Accuracy" });
    expect(
      within(table).getByText(
        "Uses a different set of examples or trials than the baseline.",
      ),
    ).toBeVisible();
    expect(within(table).queryByText(/different examples than/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/example\/trial plan/);
  });

  it("(8f) names both kinds of evaluator that report failures", () => {
    buildAnalysis = serverWordedAnalysis;
    renderSummary();
    expect(
      screen.getByText(
        "No failures to show. Only pass/fail evaluators and evaluators with a pass threshold report failures.",
      ),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(/pass\/fail rule/);
  });

  it("(8g) shows one calm line instead of the two scope paragraphs", () => {
    // W6: the two "not included" paragraphs read as
    // an internal scope note; one calm line of at most twelve words replaces
    // them.
    renderSummary();
    expect(screen.queryByTestId("results-launch-scope")).toBeNull();
    const text = document.body.textContent;
    expect(text).not.toMatch(/not included/i);
    for (const name of [
      "Task side-by-side",
      "classification",
      "Explore workbench",
      "custom columns",
      "derived metrics",
      "Best of N",
      "match-by-key",
      "evaluator-version impact",
      "advanced re-score",
      "add-to-dataset",
      "score overrides",
      "Voice/live-session",
      "simulated uploads",
    ]) {
      expect(text.toLowerCase()).not.toContain(name.toLowerCase());
    }
    const note = screen.getByTestId("summary-release-note");
    expect(note).toHaveTextContent(
      /^Some Summary views are not available in this release\.$/,
    );
    expect(note).toBeVisible();
    expect(note.textContent.trim().split(/\s+/).length).toBeLessThanOrEqual(12);
    // It is a line, not a notice box.
    expect(note.closest('[role="alert"]')).toBeNull();
  });

  it("(9) gives Choose winner a visible reason while the evaluations load", () => {
    statsQuery = {
      data: undefined,
      isPending: true,
      isLoading: true,
      isError: false,
      isSuccess: false,
      refetch: vi.fn(),
    };
    renderSummary();
    const choose = screen.getByRole("button", { name: "Choose winner" });
    expect(choose).toBeDisabled();
    expect(screen.getByText("Loading the evaluations to rank…")).toBeVisible();
    expect(choose).toHaveAccessibleDescription("Loading the evaluations to rank…");
  });

  it("(9b) ties a disabled Choose winner to the load error and its Retry", () => {
    const refetch = vi.fn();
    statsQuery = {
      data: undefined,
      isPending: false,
      isLoading: false,
      isError: true,
      isSuccess: false,
      refetch,
    };
    renderSummary();
    const choose = screen.getByRole("button", { name: "Choose winner" });
    expect(choose).toBeDisabled();
    expect(choose).toHaveAccessibleDescription(/could not be loaded/);
    expect(screen.queryByText("Loading the evaluations to rank…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });
});

describe("useExperimentStats", () => {
  const response = { data: { status: true, result: NO_WINNER_STATS } };

  function renderStats(experimentId, options) {
    const client = newClient();
    /* eslint-disable react/prop-types */
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    /* eslint-enable react/prop-types */
    const { useExperimentStats } = hooksModule;
    const view = renderHook(
      () => useExperimentStats(experimentId, options),
      { wrapper },
    );
    return { client, ...view };
  }

  let hooksModule;
  let endpointsModule;
  beforeEach(async () => {
    hooksModule = await vi.importActual("src/api/develop/experiment-stats");
    endpointsModule = await vi.importActual("src/utils/axios");
    hooks.http.get.mockResolvedValue(response);
  });

  it("reads the stats under the key the chooser invalidates after a save", async () => {
    const { client, result } = renderStats("E1");
    await waitFor(() => expect(result.current.data).toEqual(NO_WINNER_STATS));
    expect(hooks.http.get).toHaveBeenCalledWith(
      endpointsModule.endpoints.develop.experiment.getSummary("E1"),
    );
    // Raw axios response in the cache, as the classic Summary reads it.
    expect(client.getQueryData(["experiment-summary", "E1"])).toEqual(response);

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiment-summary", "E1"] });
    });
    await waitFor(() => expect(hooks.http.get).toHaveBeenCalledTimes(2));
  });

  it("does not read when disabled", async () => {
    const { result } = renderStats("E1", { enabled: false });
    expect(result.current.fetchStatus).toBe("idle");
    expect(hooks.http.get).not.toHaveBeenCalled();
  });
});
