/* eslint-disable react/prop-types */
// A results link whose order this build's read does not answer. The read
// is sent no order and serves Row order, so the page reads Row order: its
// Sort button says Sort, with Row order chosen, and the experiment header
// counts the same view. The link keeps its order. A link with an order the
// read answers, or with none, reads as before; that includes the orders by
// how a run's tries spread (most variable on a metric, most flaky). In a
// build that is not a launch build every order that fits the view reads as
// chosen; a most-variable order that names no metric does not fit, so it
// reads as the view's default.
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  query: null,
  url: null,
  configs: { results: [] },
  noop: () => {},
  marks: {},
  setQueue: vi.fn(),
  phase: "success",
  headerRenders: [],
  selectionRenders: [],
  // Each read the page builds, the sort its toolbar shows, and its columns.
  views: [],
  toolbarSorts: [],
  columns: { columns: [], visibleIds: [] },
}));
vi.mock("src/api/develop/run-results", async () => {
  const actual = await vi.importActual("src/api/develop/run-results");
  return {
    ...actual,
    useRunResults: (datasetId, view) => {
      h.views.push(view);
      return h.query;
    },
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
  return { ...actual, default: () => h.url };
});
vi.mock("../useResultsPrefs", () => ({
  default: () => ({ showReasons: false, setShowReasons: h.noop }),
}));
vi.mock("../useResultsColumns", () => ({ default: () => h.columns }));
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
  default: React.forwardRef(function Grid({ rows }, _ref) {
    return (
      <div data-testid="rows">
        {rows.map((r) => (
          <div key={r.example_id}>{r.example_id}</div>
        ))}
      </div>
    );
  }),
}));
vi.mock("../ResultDrawer", () => ({ default: ({ grid }) => grid }));
vi.mock("../ResultsVerdict", () => ({ default: () => null }));
vi.mock("../ResultsToolbar", () => ({
  default: ({ sort }) => {
    h.toolbarSorts.push(sort);
    return null;
  },
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
import { runResultsParams } from "src/api/develop/run-results";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { sortForView, sortLabel } from "../sortModel";

const RUNS = ["baseline", "candidate"];
const METRIC = "eval:resolution";
// An order by a metric's spread that names no metric: the read does not
// answer it.
const NOT_ANSWERED = ["variance:candidate"];
// Orders by how the candidate's tries spread.
const SPREAD = [`variance:${METRIC}:candidate`, "flakiness:candidate"];
const population = () => ({
  total: 1,
  summary: {
    examples_total: 1,
    runs: RUNS.map((id) => ({
      run_id: id,
      label: id,
      status: "Completed",
      dataset_version: { number: 1 },
    })),
    metrics: [
      {
        key: METRIC,
        name: "Resolution",
        kind: "eval",
        output_type: "pass_fail",
      },
    ],
  },
  rows: [
    {
      example_id: "row-0",
      row_order: 1,
      inputs: { question: "input 0" },
      production: { outcome: "fail", criterion: "Resolution" },
      results: {
        baseline: { scores: { [METRIC]: { value: 1 } } },
        candidate: { scores: { [METRIC]: { value: 0 } } },
      },
    },
  ],
});
const props = {
  datasetId: "dataset-a",
  runIds: RUNS,
  baselineId: "baseline",
  mode: "experiment",
  experimentId: "experiment-a",
  onBaselineChange: h.noop,
};
let client;
const page = (search) => (
  <QueryClientProvider client={client}>
    <MemoryRouter
      initialEntries={[
        `/dashboard/develop/experiment/experiment-a/data${search}`,
      ]}
    >
      <RunResultsView {...props} />
    </MemoryRouter>
  </QueryClientProvider>
);
const linkOf = (sort) => `?sort=${encodeURIComponent(sort)}`;
// The order the latest read asks for, and the order the Sort button shows.
const sent = () => runResultsParams(h.views.at(-1)).sort;
const shown = () => h.toolbarSorts.at(-1);
const sortPatches = () =>
  h.url.patch.mock.calls.filter(([change]) => "sort" in (change ?? {}));

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.views = [];
  h.toolbarSorts = [];
  h.columns = { columns: [], visibleIds: [METRIC] };
  h.query = {
    data: population(),
    isError: false,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  };
  h.url = { pills: [], show: "all", agg: "run", diff: "off", patch: vi.fn() };
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
});

describe("A results link whose order the read does not answer", () => {
  it.each(NOT_ANSWERED)(
    "reads %s as Row order: no order is sent and the button says Sort",
    (sort) => {
      setLaunchOverrideForTest({ launch: true });
      render(page(linkOf(sort)));
      expect([sent(), shown()]).toEqual([undefined, "order"]);
      expect(sortLabel(shown())).toBe("Sort");
      expect(sortPatches()).toEqual([]);
    },
  );

  it("reads the view's own order the same way for its header", () => {
    setLaunchOverrideForTest({ launch: true });
    const view = { runIds: RUNS, baselineId: "baseline" };
    for (const sort of NOT_ANSWERED)
      expect(sortForView(sort, view)).toBe("order");
    expect(sortForView(null, view)).toBe("regressed");
    expect(sortForView("regressed", view)).toBe("regressed");
    expect(sortForView("-order", view)).toBe("-order");
  });
});

describe("A results link whose order the read answers, or with none", () => {
  it("reads Worse than baseline first by default, and sends it", () => {
    setLaunchOverrideForTest({ launch: true });
    render(page(""));
    expect([sent(), shown()]).toEqual(["regressed", "regressed"]);
  });

  it("reads a link's answered order as chosen", () => {
    setLaunchOverrideForTest({ launch: true });
    render(page(linkOf("-order")));
    expect([sent(), shown()]).toEqual(["-order", "-order"]);
  });

  it.each(SPREAD)("reads %s as chosen, and sends it", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    render(page(linkOf(sort)));
    expect([sent(), shown()]).toEqual([sort, sort]);
    expect(sortPatches()).toEqual([]);
  });

  it.each(SPREAD)(
    "reads %s as chosen in a build that is not a launch build",
    (sort) => {
      render(page(linkOf(sort)));
      expect([sent(), shown()]).toEqual([sort, sort]);
    },
  );

  it("reads an order that names no metric as the view's default in a build that is not a launch build", () => {
    render(page(linkOf(NOT_ANSWERED[0])));
    expect([sent(), shown()]).toEqual(["regressed", "regressed"]);
    expect(sortPatches()).toEqual([]);
  });
});
