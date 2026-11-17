// Honest lines row 22: Results reads the dataset's run list through
// useRunConfigs for fix-run parents and run sources. That read is on the
// launch hold list and is refused before any request; Results must say so
// ("Not available in this release") instead of silently dropping that part.
// Mocks below are copied from runResultsView-error-fail-closed.test.jsx.
/* eslint-disable react/prop-types */
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  query: null, url: null, lead: null, configs: { results: [] }, held: false,
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
  useRunConfigs: () => ({ data: h.configs, held: h.held }),
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

const NOT_IN_RELEASE = "Not available in this release";

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: {
    retry: false, staleTime: Infinity, gcTime: Infinity,
  } } });
  h.cacheMode = false;
  h.phase = "success";
  h.headerRenders = [];
  h.selectionRenders = [];
  h.configs = { results: [] };
  h.held = true;
  h.query = { data: population(), isError: false, isLoading: false, isFetching: false, refetch: vi.fn() };
  h.url = { pills: [], show: "all", agg: "run", diff: "off", patch: h.noop };
  h.lead = null;
  h.setQueue.mockClear();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});
afterEach(() => {
  cleanup();
  client.clear();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});

describe("Results when the run list is held in this release", () => {
  it("says Not available in this release in the results head", () => {
    render(page({ initialRuns: population().summary.runs }));
    const head = screen.getByTestId("results-head");
    expect(head.textContent).toContain(NOT_IN_RELEASE);
    expect(screen.getByTestId("results-runs-not-in-release").textContent).toContain(
      NOT_IN_RELEASE,
    );
    // The results themselves still show.
    expect(screen.getByText("a-row-0")).toBeTruthy();
  });

  it("shows no such line when the run list is allowed", () => {
    h.held = false;
    h.configs = { results: [] };
    render(page({ initialRuns: population().summary.runs }));
    expect(screen.queryByText(new RegExp(NOT_IN_RELEASE))).toBeNull();
  });
});
