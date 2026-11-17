/* eslint-disable react/prop-types */
// When the results read failed, the page replaced
// everything, the toolbar included, with "Couldn't load these rows." and
// Retry, so `Hide rows with no output` disappeared and could not be turned
// off. A failed read now keeps the switch (when it is on) under the error.
// The mocks below are the RunResultsView fail-closed harness
// (runResultsView-error-fail-closed.test.jsx); only the grid mock's unused
// `ref` argument is renamed `_ref`.
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  query: null,
  url: null,
  lead: null,
  configs: { results: [] },
  noop: () => {},
  marks: {},
  setQueue: vi.fn(),
  cacheMode: false,
  phase: "success",
  headerRenders: [],
  selectionRenders: [],
}));
vi.mock("src/api/develop/run-results", async () => {
  const actual = await vi.importActual("src/api/develop/run-results");
  return {
    ...actual,
    // cacheMode is fixed for a mount. Header cases use the real observer/key/
    // select path with fetching disabled, not a mocked error flag.
    useRunResults: (datasetId, view) =>
      h.cacheMode
        ? actual.useRunResults(datasetId, view, { enabled: false })
        : h.query,
    useRerunResultCells: () => ({ mutate: h.noop }),
  };
});
vi.mock("src/api/develop/r2", async () => ({
  ...(await vi.importActual("src/api/develop/r2")),
  useRunConfigs: () => ({ data: h.configs }),
  useMatrix: () => ({ data: null }),
}));
vi.mock("src/api/develop/r4", async () => ({
  ...(await vi.importActual("src/api/develop/r4")),
  useVoiceSummary: () => ({ data: null }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "viewer" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { UPDATE: "update" },
  RolePermission: { DATASETS: { update: { viewer: false } } },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: h.noop,
  closeSnackbar: h.noop,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/ErrorState", () => ({
  default: ({ onRetry }) => (
    <div role="alert">
      Results unavailable<button onClick={onRetry}>Retry</button>
    </div>
  ),
}));
vi.mock("src/sections/common/ux/toast", async () => ({
  ...(await vi.importActual("src/sections/common/ux/toast")),
  default: h.noop,
  notify: h.noop,
}));
vi.mock("src/sections/common/ux/useShortcuts", async () => ({
  ...(await vi.importActual("src/sections/common/ux/useShortcuts")),
  default: h.noop,
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: () => {},
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/oneview/Section", () => ({
  useSections: () => ({}),
}));
vi.mock("src/sections/common/ux/oneview/useViewportDensity", () => ({
  default: () => "regular",
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, h.noop],
}));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({
  default: () => ["formatted", h.noop],
}));
vi.mock("src/sections/common/ValueViewer/TextDiff", () => ({
  TextDiffLegend: () => null,
}));
vi.mock("src/sections/common/Runs/runColors", () => ({
  useRunColors: () => ({}),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ request: h.noop, dialog: null }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async () => ({
  // runNumbers.rowLabelOf calls the real rowLabel on populated verdict leads.
  ...(await vi.importActual("src/sections/common/Datasets/datasetKind")),
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("src/sections/common/filters/ResultsExpressionDialog", () => ({
  default: () => null,
}));
vi.mock("../useResultsUrlState", async () => {
  const actual = await vi.importActual("../useResultsUrlState");
  return { ...actual, default: () => (h.cacheMode ? actual.default() : h.url) };
});
vi.mock("../useResultsPrefs", () => ({
  default: () => ({ showReasons: false, setShowReasons: h.noop }),
}));
vi.mock("../useResultsColumns", () => ({
  default: () => ({ columns: [], visibleIds: [] }),
}));
vi.mock("../headers/useEvalVersionDoor", () => ({
  useEvalVersionDoor: () => ({ dialog: null }),
}));
vi.mock("../../paths/usePagePair", () => ({
  default: () => ({ candidateId: "candidate" }),
}));
vi.mock("../../iterate/iterateStore", () => ({
  useIterateStore: (select) => select({ finished: {}, dismissed: [] }),
  fixCounterText: () => "",
}));
vi.mock("../reviewMarks", () => ({
  useReviewMarks: Object.assign((select) => select({ marks: h.marks }), {
    getState: () => ({ setQueue: h.setQueue }),
  }),
  pairOf: (a, b) => `${a}:${b}`,
  reviewedText: () => null,
}));
vi.mock("../resultsColumns", () => ({
  RESULTS_COLUMN_GROUPS: [],
  buildResultColumns: () => [],
  scorePinFor: () => null,
}));
vi.mock("../ResultsGrid", () => ({
  default: React.forwardRef(function Grid({ rows, onSelectionChanged }, _ref) {
    return (
      <div data-testid="rows">
        {rows.map((r) => (
          <div key={r.example_id}>
            {r.example_id}
            <button
              aria-label={`Select ${r.example_id}`}
              onClick={() => onSelectionChanged([r])}
            >
              Select row
            </button>
          </div>
        ))}
      </div>
    );
  }),
}));
vi.mock("../ResultDrawer", () => ({ default: ({ grid }) => grid }));
vi.mock("../ResultsVerdict", () => ({
  // This deliberately retains an independent comparison cache on a results
  // error. Only the real parent's boundary may suppress it.
  default: function CachedVerdict({ onLead }) {
    React.useEffect(() => {
      onLead(h.lead);
    }, [onLead, h.lead]);
    return h.lead ? (
      <div data-testid="cached-verdict">Earlier matrix comparison</div>
    ) : null;
  },
}));
vi.mock("../ResultsToolbar", () => ({
  default: ({ taskWorse }) =>
    taskWorse ? (
      <div data-testid="worse-count">{taskWorse.count} worse</div>
    ) : null,
  anyWorseTip: () => "",
  showLabel: (v) => v,
}));
vi.mock("../RunBar", () => ({
  default: ({ runs }) => (
    <div data-testid="run-progress">
      {JSON.stringify(runs.map((r) => r.progress))}
    </div>
  ),
}));
vi.mock("../RunsChip", () => ({ default: ({ children }) => children }));
vi.mock("../LiveStatus", () => ({
  default: ({ live, paused, newCount, onPause, onUpdate, onResume }) =>
    live ? (
      <div data-testid="live-progress">
        {live.scored}/{live.total} scored
        <span data-testid="new-count">{newCount}</span>
        {paused ? (
          <>
            <button onClick={onUpdate}>Update</button>
            <button onClick={onResume}>Resume</button>
          </>
        ) : (
          <button onClick={onPause}>Pause updates</button>
        )}
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
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: () => null,
}));
vi.mock("../FilterPills", () => ({ default: () => null }));
vi.mock("../EvalDetailsDialog", () => ({ default: () => null }));
vi.mock("../SeeWhyButton", () => ({ default: () => null }));
vi.mock(
  "../../ExperimentBarRightSection/ExperimentBarDataRightSection",
  () => ({ default: () => null }),
);
vi.mock("../../ExperimentData/ExperimentStatusBar", () => ({
  ResultsStatusLine: ({ total }) => (
    <div data-testid="results-total">{total}</div>
  ),
}));

// Keep the real header, status aggregation and shared-count consumer. Only
// peripheral controls and fetches are replaced; both query caches are real.
vi.mock("src/api/develop/runs", async () => {
  const actual = await vi.importActual("src/api/develop/runs");
  return {
    ...actual,
    useExperimentDetail: (id) =>
      actual.useExperimentDetail(id, { enabled: false }),
    useDatasetRuns: () => ({ data: { results: [], pinned_run_id: null } }),
  };
});
vi.mock("src/api/develop/develop-detail", async () => ({
  ...(await vi.importActual("src/api/develop/develop-detail")),
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: false } }),
}));
vi.mock("src/sections/common/Audit/AuditDrawer", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Datasets/VersionChip", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Datasets/SplitChips", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Runs/RunStatusChip", async () => ({
  ...(await vi.importActual("src/sections/common/Runs/RunStatusChip")),
  StatusChip: ({ status, label }) => {
    h.headerRenders.push({ phase: h.phase, label: label ?? status });
    return <div data-testid="experiment-status">{label ?? status}</div>;
  },
}));
vi.mock("../../DevelopExperimentSelect", async () => ({
  ...(await vi.importActual("../../DevelopExperimentSelect")),
  default: () => null,
}));

import RunResultsView from "../RunResultsView";
import { withHideEmpty } from "src/sections/common/filters/hideEmpty";

const props = {
  datasetId: "dataset-a",
  runIds: ["baseline", "candidate"],
  baselineId: "baseline",
  mode: "experiment",
  experimentId: "experiment-a",
  onBaselineChange: h.noop,
};
const MIXED =
  "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.";
let client;
const page = () => (
  <QueryClientProvider client={client}>
    <MemoryRouter
      initialEntries={["/dashboard/develop/experiment/experiment-a/data"]}
    >
      <RunResultsView {...props} />
    </MemoryRouter>
  </QueryClientProvider>
);
const theSwitch = () =>
  screen.queryByRole("checkbox", { name: "Hide rows with no output" });

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    },
  });
  h.cacheMode = false;
  h.phase = "error";
  h.configs = { results: [] };
  h.lead = null;
  h.query = {
    data: undefined,
    isError: true,
    isLoading: false,
    isFetching: false,
    error: { response: { status: 500 } },
    refetch: vi.fn(),
  };
  h.url = { pills: [], show: "all", agg: "run", diff: "off", patch: vi.fn() };
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("Hide rows with no output when the results read fails", () => {
  it("stays under the error, on, and turns off with one click", () => {
    h.url = { ...h.url, pills: withHideEmpty([], true) };
    render(page());
    expect(screen.getByRole("alert")).toHaveTextContent("Results unavailable");
    expect(theSwitch()).toBeTruthy();
    expect(theSwitch().checked).toBe(true);
    fireEvent.click(theSwitch());
    expect(h.url.patch).toHaveBeenCalledWith({ filters: null });
  });

  it("stays under the runs-can't-be-lined-up notice too, keeping the other filters", () => {
    const split = { type: "split", values: ["smoke"] };
    h.url = { ...h.url, pills: withHideEmpty([split], true) };
    h.query = { ...h.query, error: { statusCode: 409, result: MIXED } };
    render(page());
    expect(screen.getByTestId("mixed-runs-notice")).toBeTruthy();
    expect(theSwitch().checked).toBe(true);
    fireEvent.click(theSwitch());
    expect(h.url.patch).toHaveBeenCalledWith({
      filters: JSON.stringify([split]),
    });
  });

  it("adds nothing under the error when the switch is off", () => {
    render(page());
    expect(screen.getByRole("alert")).toHaveTextContent("Results unavailable");
    expect(theSwitch()).toBeNull();
  });
});
