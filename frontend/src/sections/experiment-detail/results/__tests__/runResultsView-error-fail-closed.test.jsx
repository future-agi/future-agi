/* eslint-disable react/prop-types */
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  query: null, url: null, lead: null, configs: { results: [] },
  noop: () => {}, marks: {}, setQueue: vi.fn(),
  cacheMode: false, phase: "success", headerRenders: [], selectionRenders: [],
}));
vi.mock("src/api/develop/run-results", async () => {
  const actual = await vi.importActual("src/api/develop/run-results");
  return {
    ...actual,
    // cacheMode is fixed for a mount. Header cases use the real observer/key/
    // select path with fetching disabled, not a mocked error flag.
    useRunResults: (datasetId, view) => h.cacheMode
      ? actual.useRunResults(datasetId, view, { enabled: false }) : h.query,
    useRerunResultCells: () => ({ mutate: h.noop }),
  };
});
vi.mock("src/api/develop/r2", async () => ({
  ...await vi.importActual("src/api/develop/r2"),
  useRunConfigs: () => ({ data: h.configs }),
  useMatrix: () => ({ data: null }),
}));
vi.mock("src/api/develop/r4", async () => ({
  ...await vi.importActual("src/api/develop/r4"),
  useVoiceSummary: () => ({ data: null }),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "viewer" }) }));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { UPDATE: "update" }, RolePermission: { DATASETS: { update: { viewer: false } } },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.noop, closeSnackbar: h.noop }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/ErrorState", () => ({
  default: ({ onRetry }) => <div role="alert">Results unavailable<button onClick={onRetry}>Retry</button></div>,
}));
vi.mock("src/sections/common/ux/toast", async () => ({
  ...await vi.importActual("src/sections/common/ux/toast"),
  default: h.noop, notify: h.noop,
}));
vi.mock("src/sections/common/ux/useShortcuts", async () => ({
  ...await vi.importActual("src/sections/common/ux/useShortcuts"),
  default: h.noop,
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({ useRegisterCommands: () => {} }));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/oneview/Section", () => ({ useSections: () => ({}) }));
vi.mock("src/sections/common/ux/oneview/useViewportDensity", () => ({ default: () => "regular" }));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({ useLayoutPrefs: () => [{}, h.noop] }));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({ default: () => ["formatted", h.noop] }));
vi.mock("src/sections/common/ValueViewer/TextDiff", () => ({ TextDiffLegend: () => null }));
vi.mock("src/sections/common/Runs/runColors", () => ({ useRunColors: () => ({}) }));
vi.mock("src/sections/common/Runs/useRescore", () => ({ useRescore: () => ({ request: h.noop, dialog: null }) }));
vi.mock("src/sections/common/Datasets/datasetKind", async () => ({
  // runNumbers.rowLabelOf calls the real rowLabel on populated verdict leads.
  ...await vi.importActual("src/sections/common/Datasets/datasetKind"),
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("src/sections/common/filters/ResultsExpressionDialog", () => ({ default: () => null }));
vi.mock("../useResultsUrlState", async () => {
  const actual = await vi.importActual("../useResultsUrlState");
  return { ...actual, default: () => h.cacheMode ? actual.default() : h.url };
});
vi.mock("../useResultsPrefs", () => ({ default: () => ({ showReasons: false, setShowReasons: h.noop }) }));
vi.mock("../useResultsColumns", () => ({ default: () => ({ columns: [], visibleIds: [] }) }));
vi.mock("../headers/useEvalVersionDoor", () => ({ useEvalVersionDoor: () => ({ dialog: null }) }));
vi.mock("../../paths/usePagePair", () => ({ default: () => ({ candidateId: "candidate" }) }));
vi.mock("../../iterate/iterateStore", () => ({
  useIterateStore: (select) => select({ finished: {}, dismissed: [] }),
  fixCounterText: () => "",
}));
vi.mock("../reviewMarks", () => ({
  useReviewMarks: Object.assign((select) => select({ marks: h.marks }), {
    getState: () => ({ setQueue: h.setQueue }),
  }),
  pairOf: (a, b) => `${a}:${b}`, reviewedText: () => null,
}));
vi.mock("../resultsColumns", () => ({
  RESULTS_COLUMN_GROUPS: [], buildResultColumns: () => [], scorePinFor: () => null,
}));
vi.mock("../ResultsGrid", () => ({
  default: React.forwardRef(function Grid({ rows, onSelectionChanged }, ref) {
    return <div data-testid="rows">{rows.map((r) => <div key={r.example_id}>
      {r.example_id}
      <button aria-label={`Select ${r.example_id}`} onClick={() => onSelectionChanged([r])}>Select row</button>
    </div>)}</div>;
  }),
}));
vi.mock("../ResultDrawer", () => ({ default: ({ grid }) => grid }));
vi.mock("../ResultsVerdict", () => ({
  // This deliberately retains an independent comparison cache on a results
  // error. Only the real parent's boundary may suppress it.
  default: function CachedVerdict({ onLead }) {
    React.useEffect(() => { onLead(h.lead); }, [onLead, h.lead]);
    return h.lead ? <div data-testid="cached-verdict">Earlier matrix comparison</div> : null;
  },
}));
vi.mock("../ResultsToolbar", () => ({
  default: ({ taskWorse }) => taskWorse ? <div data-testid="worse-count">{taskWorse.count} worse</div> : null,
  anyWorseTip: () => "", showLabel: (v) => v,
}));
vi.mock("../RunBar", () => ({
  default: ({ runs }) => <div data-testid="run-progress">{JSON.stringify(runs.map((r) => r.progress))}</div>,
}));
vi.mock("../RunsChip", () => ({ default: ({ children }) => children }));
vi.mock("../LiveStatus", () => ({
  default: ({ live, paused, newCount, onPause, onUpdate, onResume }) => live ? (
    <div data-testid="live-progress">
      {live.scored}/{live.total} scored
      <span data-testid="new-count">{newCount}</span>
      {paused ? <><button onClick={onUpdate}>Update</button><button onClick={onResume}>Resume</button></> :
        <button onClick={onPause}>Pause updates</button>}
    </div>
  ) : null,
}));
// SelectionBar and useSelectAllMatching remain REAL. Observe every render at
// the real tagging consumer boundary, including the original row payloads/IDs.
vi.mock("../AddResultsToDatasetDialog", () => ({
  default: ({ datasetId, rows, exampleIds }) => {
    h.selectionRenders.push({ phase: h.phase, datasetId, rows, exampleIds });
    return null;
  },
}));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({ default: () => null }));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({ default: () => null }));
vi.mock("../FilterPills", () => ({ default: () => null }));
vi.mock("../EvalDetailsDialog", () => ({ default: () => null }));
vi.mock("../SeeWhyButton", () => ({ default: () => null }));
vi.mock("../../ExperimentBarRightSection/ExperimentBarDataRightSection", () => ({ default: () => null }));
vi.mock("../../ExperimentData/ExperimentStatusBar", () => ({
  ResultsStatusLine: ({ total }) => <div data-testid="results-total">{total}</div>,
}));

// Keep the real header, status aggregation and shared-count consumer. Only
// peripheral controls and fetches are replaced; both query caches are real.
vi.mock("src/api/develop/runs", async () => {
  const actual = await vi.importActual("src/api/develop/runs");
  return {
    ...actual,
    useExperimentDetail: (id) => actual.useExperimentDetail(id, { enabled: false }),
    useDatasetRuns: () => ({ data: { results: [], pinned_run_id: null } }),
  };
});
vi.mock("src/api/develop/develop-detail", async () => ({
  ...await vi.importActual("src/api/develop/develop-detail"),
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: false } }),
}));
vi.mock("src/sections/common/Audit/AuditDrawer", () => ({ default: () => null }));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({ default: () => null }));
vi.mock("src/sections/common/Datasets/VersionChip", () => ({ default: () => null }));
vi.mock("src/sections/common/Datasets/SplitChips", () => ({ default: () => null }));
vi.mock("src/sections/common/Runs/RunStatusChip", async () => ({
  ...await vi.importActual("src/sections/common/Runs/RunStatusChip"),
  StatusChip: ({ status, label }) => {
    h.headerRenders.push({ phase: h.phase, label: label ?? status });
    return <div data-testid="experiment-status">{label ?? status}</div>;
  },
}));
vi.mock("../../DevelopExperimentSelect", async () => ({
  ...await vi.importActual("../../DevelopExperimentSelect"),
  default: () => null,
}));

import RunResultsView from "../RunResultsView";
import ExperimentRow from "../../ExperimentRow";
import { RESULTS_PAGE_SIZE, runResultsKey, runResultsParams } from "src/api/develop/run-results";
import { useLiveCounts } from "../liveCountShare";

// RetestStrip, retestSummary, liveModel and populationLine are real: assertions
// pin the output of the retained-summary paths, not a mock of the fixed hook.
const population = (prefix = "a", scored = 2) => ({
  total: 2,
  summary: {
    examples_total: 4,
    runs: [
      { run_id: "baseline", label: "Baseline", status: "Completed", dataset_version: { number: 1 } },
      { run_id: "candidate", label: "Candidate", status: "Running", dataset_version: { number: 2 },
        progress: { done: scored, scored, total: 4, status: "Running" } },
    ],
    metrics: [{ key: "eval:resolution", name: "Resolution", kind: "eval", output_type: "pass_fail" }],
  },
  rows: [1, 0].map((score, i) => ({
    example_id: `${prefix}-row-${i}`, row_order: i + 1,
    inputs: { question: `${prefix} input ${i}` },
    production: { outcome: "fail", criterion: "Resolution" },
    results: { candidate: { scores: { "eval:resolution": { value: score } } } },
  })),
});
const props = { datasetId: "dataset-a", runIds: ["baseline", "candidate"], baselineId: "baseline",
  mode: "experiment", experimentId: "experiment-a", onBaselineChange: h.noop };
let client;
const page = (overrides = {}) => (
  <QueryClientProvider client={client}>
    <MemoryRouter initialEntries={["/dashboard/develop/experiment/experiment-a/data"]}>
      <RunResultsView {...props} {...overrides} />
    </MemoryRouter>
  </QueryClientProvider>
);
const failedPills = [{ type: "rows", ids: ["a-row-1"] }];
function HeaderScopeSwitch() {
  const navigate = useNavigate();
  return <>
    <button onClick={() => navigate(`?filters=${encodeURIComponent(JSON.stringify(failedPills))}`)}>Failed scope</button>
    <button onClick={() => navigate("?filters=")}>Healthy scope</button>
  </>;
}
const headerPage = (showResults = true) => (
  <QueryClientProvider client={client}>
    <MemoryRouter initialEntries={["/dashboard/develop/experiment/experiment-a/data"]}>
      <Routes>
        <Route path="/dashboard/develop/experiment/:experimentId/data" element={<>
          {/* The real wrapper mounts the header BEFORE the outlet. */}
          <ExperimentRow />
          <HeaderScopeSwitch />
          {showResults && <RunResultsView {...props} />}
        </>} />
      </Routes>
    </MemoryRouter>
  </QueryClientProvider>
);
const envelope = (result) => ({ data: { status: true, result } });
const gridKey = (overrides = {}) => [...runResultsKey(props.datasetId), "grid", {
  ...runResultsParams({ runIds: props.runIds, baselineId: props.baselineId,
    pills: [], show: "all", search: "", sort: "regressed", ...overrides }),
  page: 0, page_size: RESULTS_PAGE_SIZE,
}];
const applyFilter = (rerender) => {
  h.url = { ...h.url, pills: [{ type: "rows", ids: ["a-row-1"] }] };
  h.query = { ...h.query, data: { ...h.query.data, total: 1, rows: [h.query.data.rows[1]] } };
  rerender(page());
  // The unfiltered answer survives a healthy filter, although only one row is shown.
  expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 now pass · 1 still fail (Resolution)");
  expect(screen.getByTestId("results-retest-still")).toHaveTextContent("See the 1 still failing");
  expect(screen.queryByText("a-row-0")).toBeNull();
  expect(screen.getByText("a-row-1")).toBeTruthy();
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: {
    retry: false, staleTime: Infinity, gcTime: Infinity,
  } } });
  h.cacheMode = false;
  h.phase = "success";
  h.headerRenders = [];
  h.selectionRenders = [];
  h.configs = { results: [] };
  h.query = { data: population(), isError: false, isLoading: false, isFetching: false, refetch: vi.fn() };
  h.url = { pills: [], show: "all", agg: "run", diff: "off", patch: h.noop };
  h.lead = { runId: "candidate", label: "Candidate", worse: 1, worseRows: ["a-row-1"],
    bothRan: 2, notRun: 1, partial: { scored: 2, total: 4 } };
  h.setQueue.mockClear();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});
afterEach(() => {
  cleanup();
  client.clear();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});

describe("RunResultsView fail-closed results", () => {
  it.each([403, 404, 500])("hides all earlier result output after a cached %s error", (status) => {
    const { rerender } = render(page({ initialRuns: population().summary.runs }));
    expect(screen.getByText("a-row-0")).toBeTruthy();
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("2 production failures");
    expect(screen.getByTestId("cached-verdict")).toBeTruthy();
    expect(screen.getByTestId("results-rows-version")).toHaveTextContent("2 rows both ran");
    expect(screen.getByTestId("live-progress")).toHaveTextContent("2/4 scored");
    expect(useLiveCounts.getState().byExperiment["experiment-a"].candidate.done).toBe(2);
    applyFilter(rerender);
    // React Query can retain the exact successful payload on a failed refetch.
    const retained = h.query.data;
    h.query = { ...h.query, isError: true, error: { response: { status } } };
    rerender(page({ initialRuns: population().summary.runs }));
    expect(h.query.data).toBe(retained);
    expect(screen.getByRole("alert")).toHaveTextContent("Results unavailable");
    for (const id of ["results-retest", "results-retest-still", "cached-verdict",
      "live-progress", "results-rows-version", "worse-count"])
      expect(screen.queryByTestId(id)).toBeNull();
    expect(screen.queryByText(/a-row-/)).toBeNull();
    expect(screen.queryByText(/Resolution/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause updates" })).toBeNull();
    expect(useLiveCounts.getState().byExperiment["experiment-a"] ?? {}).toEqual({});
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(h.query.refetch).toHaveBeenCalledTimes(1);

    // Recovery with a genuinely empty population cannot resurrect either
    // the unfiltered re-test answer or the previous verdict's population.
    h.lead = null;
    h.query = { ...h.query, isError: false, error: null, data: { rows: [], total: 0, summary: { runs: [] } } };
    rerender(page());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("results-retest")).toBeNull();
    expect(screen.queryByTestId("cached-verdict")).toBeNull();
    expect(screen.queryByTestId("results-rows-version")).toBeNull();
  });

  it("drops the retained filtered summary when the dataset scope changes", () => {
    const { rerender } = render(page());
    applyFilter(rerender);
    h.lead = null;
    h.query = { ...h.query, data: { rows: [], total: 0, summary: { runs: [] } } };
    rerender(page({ datasetId: "dataset-b" }));
    expect(screen.queryByTestId("results-retest")).toBeNull();
    expect(screen.queryByTestId("results-rows-version")).toBeNull();
    expect(screen.queryByTestId("worse-count")).toBeNull();
    expect(screen.queryByText(/a-row-/)).toBeNull();
    expect(screen.queryByTestId("live-progress")).toBeNull();
    expect(useLiveCounts.getState().byExperiment["experiment-a"] ?? {}).toEqual({});
  });

  it("preserves healthy Pause Update Resume and new-result counts", () => {
    const { rerender } = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Pause updates" }));
    h.query = { ...h.query, data: population("b", 3) };
    rerender(page());
    expect(screen.getByText("a-row-0")).toBeTruthy();
    expect(screen.queryByText("b-row-0")).toBeNull();
    expect(screen.getByTestId("new-count")).toHaveTextContent("1");
    expect(screen.getByTestId("live-progress")).toHaveTextContent("3/4 scored");
    expect(useLiveCounts.getState().byExperiment["experiment-a"].candidate.done).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Update" }));
    expect(screen.getByText("b-row-0")).toBeTruthy();
    expect(screen.getByTestId("new-count")).toHaveTextContent("0");
    h.query = { ...h.query, data: population("c", 4) };
    rerender(page());
    expect(screen.getByText("b-row-0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(screen.getByText("c-row-0")).toBeTruthy();
    expect(useLiveCounts.getState().byExperiment["experiment-a"].candidate.done).toBe(4);
  });

  it("keeps the unfiltered re-test answer through filter loading without initialRuns", () => {
    const compare = { mode: "compare", experimentId: undefined };
    const all = h.query.data;
    const { rerender } = render(page(compare));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("2 production failures");
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 now pass · 1 still fail (Resolution)");
    h.url = { ...h.url, pills: [{ type: "rows", ids: ["a-row-1"] }] };
    h.query = { ...h.query, data: undefined, isLoading: true, isFetching: true };
    rerender(page(compare));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("2 production failures");
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 now pass · 1 still fail (Resolution)");
    h.query = { ...h.query, isLoading: false, isFetching: false,
      data: { ...all, total: 1, rows: [all.rows[1]] } };
    rerender(page(compare));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("2 production failures");
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 now pass · 1 still fail (Resolution)");
    expect(screen.getByTestId("results-retest-still")).toHaveTextContent("See the 1 still failing");
    expect(screen.queryByText("a-row-0")).toBeNull();
    expect(screen.getByText("a-row-1")).toBeTruthy();
  });

  it.each(["focus", "runs", "baseline", "reducer", "versions"])(
    "invalidates the retained answer on a real %s change during loading", (scope) => {
      const { rerender } = render(page());
      applyFilter(rerender);
      h.lead = null;
      h.query = { ...h.query, data: undefined, isLoading: true, isFetching: true };
      const overrides = {};
      if (scope === "focus") h.url = { ...h.url, run: "baseline" };
      if (scope === "runs") overrides.runIds = ["baseline", "another-candidate"];
      if (scope === "baseline") overrides.baselineId = "candidate";
      if (scope === "reducer") h.url = { ...h.url, agg: "median" };
      if (scope === "versions") h.url = { ...h.url, mixVersions: true };
      rerender(page(overrides));
      expect(screen.queryByTestId("results-retest")).toBeNull();
    },
  );

  it("invalidates A's retained answer when delayed configs resolve focus to B while filtered", () => {
    h.lead = null;
    h.configs = undefined;
    const all = population();
    all.summary.runs[1] = { ...all.summary.runs[1], label: "Candidate A" };
    all.summary.runs.push({ run_id: "candidate-b", label: "Candidate B", status: "Completed" });
    all.rows = all.rows.map((row) => ({ ...row, results: { ...row.results,
      "candidate-b": { scores: { "eval:resolution": { value: 1 } } },
    } }));
    h.query = { ...h.query, data: all };
    const threeRuns = { runIds: ["baseline", "candidate", "candidate-b"], mode: "compare", experimentId: undefined };
    const { rerender } = render(page(threeRuns));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("Candidate A");
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 now pass · 1 still fail");
    h.url = { ...h.url, pills: failedPills };
    h.query = { ...h.query, data: { ...all, total: 1, rows: [all.rows[1]] } };
    rerender(page(threeRuns));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("2 production failures");
    expect(screen.getByTestId("results-retest-still")).toHaveTextContent("See the 1 still failing");
    h.configs = { results: [{ run_id: "candidate-b", parent_run_id: "baseline" }] };
    rerender(page(threeRuns));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("Candidate B");
    // Only B's currently loaded row is known; A's all-row answer is not B's.
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 production failure: 1 now pass · 0 still fail");
    expect(screen.queryByTestId("results-retest-still")).toBeNull();
    // An undefined-data filter load retains B's identity, not a fallback to A.
    h.query = { ...h.query, data: undefined, isLoading: true, isFetching: true };
    rerender(page(threeRuns));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("1 now pass · 0 still fail");
    h.query = { ...h.query, data: all, isLoading: false, isFetching: false };
    h.url = { ...h.url, pills: [] };
    rerender(page(threeRuns));
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("Candidate B");
    expect(screen.getByTestId("results-retest-text")).toHaveTextContent("2 now pass · 0 still fail");
  });

  it.each(["dataset-loading", "dataset-populated", "view-loading", "view-populated"])(
    "never hands earlier selected rows to the real consumer on %s", (transition) => {
      const original = h.query.data;
      const { rerender } = render(page());
      fireEvent.click(screen.getByRole("button", { name: "Select a-row-0" }));
      expect(screen.getByTestId("results-selection-bar")).toHaveTextContent("1 selected");
      expect(h.selectionRenders.length).toBeGreaterThan(0);
      expect(h.selectionRenders.every((r) => r.datasetId === "dataset-a" &&
        r.exampleIds.length === 1 && r.exampleIds[0] === "a-row-0" && r.rows[0] === original.rows[0])).toBe(true);
      const datasetChange = transition.startsWith("dataset");
      const loading = transition.endsWith("loading");
      const destination = datasetChange ? { datasetId: "dataset-b" } : {};
      if (!datasetChange) h.url = { ...h.url, pills: [{ type: "rows", ids: ["b-row-0"] }] };
      h.lead = null;
      h.phase = "destination";
      h.selectionRenders = [];
      h.query = { ...h.query, data: loading ? undefined : population("b"), isLoading: loading, isFetching: loading };
      rerender(page(destination));
      if (loading) expect(screen.getByTestId("results-loading")).toBeTruthy();
      else expect(screen.getByText("b-row-0")).toBeTruthy();
      expect(screen.queryByTestId("results-selection-bar")).toBeNull();
      expect(h.selectionRenders).toEqual([]); // includes any render before reset effects
      if (loading) {
        h.query = { ...h.query, data: population("b"), isLoading: false, isFetching: false };
        rerender(page(destination));
        expect(screen.getByText("b-row-0")).toBeTruthy();
        expect(screen.queryByTestId("results-selection-bar")).toBeNull();
        expect(h.selectionRenders).toEqual([]);
      }
      // Returning to the source must not revive the invalidated storage.
      h.url = { ...h.url, pills: [] };
      h.query = { ...h.query, data: original };
      rerender(page());
      expect(screen.queryByTestId("results-selection-bar")).toBeNull();
      expect(h.selectionRenders).toEqual([]);
      // The guard still permits a fresh selection in the destination scope.
      if (!datasetChange) h.url = { ...h.url, pills: [{ type: "rows", ids: ["b-row-0"] }] };
      h.query = { ...h.query, data: population("b") };
      rerender(page(destination));
      fireEvent.click(screen.getByRole("button", { name: "Select b-row-0" }));
      expect(screen.getByTestId("results-selection-bar")).toHaveTextContent("1 selected");
      expect(h.selectionRenders.length).toBeGreaterThan(0);
      expect(h.selectionRenders.every((r) => r.datasetId === (datasetChange ? "dataset-b" : "dataset-a") &&
        r.exampleIds.length === 1 && r.exampleIds[0] === "b-row-0" && r.rows[0].inputs.question === "b input 0")).toBe(true);
    },
  );

  it.each([403, 404, 500])("does not resurrect the real selection consumer after a %s error", (status) => {
    const { rerender } = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Select a-row-0" }));
    expect(screen.getByTestId("results-selection-bar")).toHaveTextContent("1 selected");
    expect(h.selectionRenders.length).toBeGreaterThan(0);
    h.selectionRenders = [];
    h.query = { ...h.query, isError: true, error: { response: { status } } };
    rerender(page());
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByTestId("results-selection-bar")).toBeNull();
    expect(h.selectionRenders).toEqual([]);
    h.query = { ...h.query, isError: false, error: null };
    rerender(page());
    expect(screen.getByText("a-row-0")).toBeTruthy();
    expect(screen.queryByTestId("results-selection-bar")).toBeNull();
    expect(h.selectionRenders).toEqual([]);
  });

  it.each([403, 404, 500])("blocks every header render when the cached scope already failed with %s before mount", (status) => {
    h.cacheMode = true;
    client.setQueryData(["experiment", props.experimentId], envelope({ dataset_id: props.datasetId,
      name: "Cached experiment", runs: population("header", 3).summary.runs, runs_total: 2, runs_has_more: false }));
    const key = gridKey();
    client.setQueryData(key, envelope(population()));
    const query = client.getQueryCache().find({ queryKey: key, exact: true });
    const retained = query.state.data;
    query.setState({ status: "error", error: { response: { status } }, fetchStatus: "idle" });
    expect(useLiveCounts.getState().stateByExperiment[props.experimentId]).toBeUndefined();
    h.phase = "already-error";
    render(headerPage());
    expect(query.state.data).toBe(retained);
    expect(screen.getByRole("alert")).toHaveTextContent("Results unavailable");
    expect(h.headerRenders.length).toBeGreaterThan(0);
    expect(h.headerRenders.every((r) => r.label === "Running")).toBe(true);
  });

  it.each([403, 404, 500])("blocks every header render when navigating to a cached %s failed filter scope", (status) => {
    h.cacheMode = true;
    client.setQueryData(["experiment", props.experimentId], envelope({ dataset_id: props.datasetId,
      name: "Cached experiment", runs: population("header", 3).summary.runs, runs_total: 2, runs_has_more: false }));
    client.setQueryData(gridKey(), envelope(population()));
    const failedKey = gridKey({ pills: failedPills });
    client.setQueryData(failedKey, envelope(population()));
    const failedQuery = client.getQueryCache().find({ queryKey: failedKey, exact: true });
    failedQuery.setState({ status: "error", error: { response: { status } }, fetchStatus: "idle" });
    render(headerPage());
    expect(screen.getByTestId("experiment-status")).toHaveTextContent("Running · 2/4");
    expect(useLiveCounts.getState().stateByExperiment[props.experimentId]?.blocked).toBe(false);
    h.headerRenders = [];
    h.phase = "failed-scope";
    fireEvent.click(screen.getByRole("button", { name: "Failed scope" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Results unavailable");
    expect(h.headerRenders.length).toBeGreaterThan(0);
    expect(h.headerRenders.every((r) => r.label === "Running")).toBe(true);
    // An error in another cached scope must not block the healthy current one.
    h.phase = "healthy-scope";
    fireEvent.click(screen.getByRole("button", { name: "Healthy scope" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("experiment-status")).toHaveTextContent("Running · 2/4");
    expect(failedQuery.state.status).toBe("error");
  });

  it.each([403, 404, 500])(
    "blocks the real cached header on its first %s error render and recovers", (status) => {
      h.cacheMode = true;
      const header = { dataset_id: props.datasetId, name: "Cached experiment",
        runs: population("header", 3).summary.runs, runs_total: 2, runs_has_more: false };
      const headerKey = ["experiment", props.experimentId];
      client.setQueryData(headerKey, envelope(header));
      const key = gridKey();
      client.setQueryData(key, envelope(population()));
      const { rerender } = render(headerPage());
      expect(screen.getByTestId("experiment-status")).toHaveTextContent("Running · 2/4");
      expect(screen.getByText("a-row-0")).toBeTruthy();
      const cachedHeader = client.getQueryData(headerKey);
      const query = client.getQueryCache().find({ queryKey: key, exact: true });
      const cachedResults = query.state.data;
      h.headerRenders = [];
      h.phase = "error";
      act(() => {
        query.setState({ status: "error", error: { response: { status } }, fetchStatus: "idle" });
        // The block must arrive from the query-cache event itself, before
        // RunResultsView can publish from a layout or passive effect.
        expect(useLiveCounts.getState().stateByExperiment[props.experimentId]?.blocked).toBe(true);
      });
      rerender(headerPage());
      expect(query.state.data).toBe(cachedResults);
      expect(client.getQueryData(headerKey)).toBe(cachedHeader);
      expect(screen.getByRole("alert")).toHaveTextContent("Results unavailable");
      expect(screen.getByTestId("experiment-status")).toHaveTextContent(/^Running$/);
      const errorRenders = h.headerRenders.filter((r) => r.phase === "error");
      expect(errorRenders.length).toBeGreaterThan(0);
      expect(errorRenders.every((r) => !/\d+\s*\/\s*\d+/.test(r.label))).toBe(true);
      expect(errorRenders[0].label).toBe("Running");
      expect(useLiveCounts.getState().stateByExperiment[props.experimentId]).toMatchObject({
        datasetId: props.datasetId, blocked: true,
      });

      h.phase = "recovery";
      act(() => client.setQueryData(key, envelope(population("b", 3))));
      rerender(headerPage());
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByTestId("experiment-status")).toHaveTextContent("Running · 3/4");
      expect(useLiveCounts.getState().stateByExperiment[props.experimentId]?.blocked).toBe(false);
      rerender(headerPage(false));
      expect(useLiveCounts.getState().stateByExperiment[props.experimentId]).toBeUndefined();
      expect(screen.getByTestId("experiment-status")).toHaveTextContent("Running · 3/4");
      act(() => query.setState({ status: "error", error: new Error("Detached results") }));
      expect(useLiveCounts.getState().stateByExperiment[props.experimentId]).toBeUndefined();
    },
  );
});
