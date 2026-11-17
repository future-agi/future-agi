/* eslint-disable react/prop-types */
// While rows are selected the results selection bar takes the View menu's
// place in the toolbar row, so the View menu's "Hide rows with no output"
// line is not there. RunResultsView gives the selection bar that line while
// the toolbar tells it the row leaves the hide-empty switch out, and not
// while the switch is on screen; with no rows selected nothing changes. The
// mocks below are the RunResultsView fail-closed harness
// (runResultsView-error-fail-closed.test.jsx). Two differ: the toolbar stands
// in for the switch's room (it tells the page the row is full, or has room),
// and the action bar shows only the entries More is given besides the bar's
// actions, as buttons that show their state.
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
    const lead = h.lead;
    React.useEffect(() => {
      onLead(lead);
    }, [onLead, lead]);
    return h.lead ? (
      <div data-testid="cached-verdict">Earlier matrix comparison</div>
    ) : null;
  },
}));
// The toolbar stands in for the hide-empty switch's room: it tells the page
// when the row leaves the switch out.
vi.mock("../ResultsToolbar", () => ({
  default: ({ taskWorse, onHideEmptyLeftOut }) => (
    <>
      {taskWorse ? (
        <div data-testid="worse-count">{taskWorse.count} worse</div>
      ) : null}
      <button onClick={() => onHideEmptyLeftOut?.(true)}>Row full</button>
      <button onClick={() => onHideEmptyLeftOut?.(false)}>Row has room</button>
    </>
  ),
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
// The selection bar's More: only the entries it is given besides the bar's
// actions, as buttons that show their state.
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: ({ surface, moreExtras = [] }) =>
    surface === "results-selection"
      ? moreExtras.map((a) => (
          <button
            key={a.id}
            aria-pressed={Boolean(a.toggle?.on)}
            onClick={() => a.run()}
          >
            {a.label}
          </button>
        ))
      : null,
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
import { useLiveCounts } from "../liveCountShare";
import {
  HIDE_EMPTY_LABEL,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";

// Two rows of the fail-closed harness's population.
const population = () => ({
  total: 2,
  summary: {
    examples_total: 4,
    runs: [
      {
        run_id: "baseline",
        label: "Baseline",
        status: "Completed",
        dataset_version: { number: 1 },
      },
      {
        run_id: "candidate",
        label: "Candidate",
        status: "Running",
        dataset_version: { number: 2 },
        progress: { done: 2, scored: 2, total: 4, status: "Running" },
      },
    ],
    metrics: [
      {
        key: "eval:resolution",
        name: "Resolution",
        kind: "eval",
        output_type: "pass_fail",
      },
    ],
  },
  rows: [1, 0].map((score, i) => ({
    example_id: `a-row-${i}`,
    row_order: i + 1,
    inputs: { question: `a input ${i}` },
    production: { outcome: "fail", criterion: "Resolution" },
    results: { candidate: { scores: { "eval:resolution": { value: score } } } },
  })),
});
const props = {
  datasetId: "dataset-a",
  runIds: ["baseline", "candidate"],
  baselineId: "baseline",
  mode: "experiment",
  experimentId: "experiment-a",
  onBaselineChange: h.noop,
};
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
const line = () => screen.queryByRole("button", { name: HIDE_EMPTY_LABEL });
const tell = (name) => fireEvent.click(screen.getByRole("button", { name }));
const selectRow = () => {
  fireEvent.click(screen.getByRole("button", { name: "Select a-row-0" }));
  expect(screen.getByTestId("results-selection-bar")).toHaveTextContent(
    "1 selected",
  );
};

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    },
  });
  h.cacheMode = false;
  h.phase = "success";
  h.headerRenders = [];
  h.selectionRenders = [];
  h.configs = { results: [] };
  h.query = {
    data: population(),
    isError: false,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  };
  h.url = { pills: [], show: "all", agg: "run", diff: "off", patch: vi.fn() };
  h.lead = {
    runId: "candidate",
    label: "Candidate",
    worse: 1,
    worseRows: ["a-row-1"],
    bothRan: 2,
    notRun: 1,
    partial: { scored: 2, total: 4 },
  };
  h.setQueue.mockClear();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});
afterEach(() => {
  cleanup();
  client.clear();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});

describe("the selection bar's More and the room for the hide-empty switch", () => {
  it("gets the View menu's line only while the switch is left out, and turns hiding on", () => {
    render(page());
    selectRow();
    expect(line()).toBeNull();

    tell("Row full");
    expect(line()).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(line());
    expect(h.url.patch).toHaveBeenLastCalledWith({
      filters: JSON.stringify(withHideEmpty([], true)),
    });

    tell("Row has room");
    expect(line()).toBeNull();
  });

  it("shows hiding on and turns it off, and adds nothing without a selection", () => {
    h.url = { ...h.url, pills: withHideEmpty([], true) };
    render(page());
    tell("Row full");
    expect(line()).toBeNull();

    selectRow();
    expect(line()).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(line());
    expect(h.url.patch).toHaveBeenLastCalledWith({ filters: null });
  });
});
