/* eslint-disable react/prop-types */
// The results page's Worse, Better and Changed compare the other runs in
// view with the baseline. A link that names one of them while the page has
// no baseline to compare with (none, or not set) reads as All: the read does
// not ask for the show and the toolbar shows All. The link keeps it, so it
// applies again once a baseline is back.
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
  // Each read the page builds, what its toolbar is handed, and its columns.
  views: [],
  toolbars: [],
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
  default: (props) => {
    h.toolbars.push(props);
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
import { showForView } from "../showModel";

const RUNS = ["baseline", "candidate"];
const METRIC = "eval:resolution";
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
    show_counts: null,
  },
  rows: [
    {
      example_id: "row-0",
      row_order: 1,
      inputs: { question: "input 0" },
      results: {
        baseline: { scores: { [METRIC]: { value: 1 } } },
        candidate: { scores: { [METRIC]: { value: 0 } } },
      },
    },
  ],
});
// The parent hands the view its runs and baseline, as both results pages do.
const props = {
  datasetId: "dataset-a",
  runIds: RUNS,
  baselineId: "baseline",
  mode: "experiment",
  experimentId: "experiment-a",
  onBaselineChange: h.noop,
};
let client;
const page = (overrides = {}) => (
  <QueryClientProvider client={client}>
    <MemoryRouter
      initialEntries={[
        "/dashboard/develop/experiment/experiment-a/data?show=worse",
      ]}
    >
      <RunResultsView {...props} {...overrides} />
    </MemoryRouter>
  </QueryClientProvider>
);
// The show the latest read asks for, and the show its toolbar is handed.
const sent = () => runResultsParams(h.views.at(-1)).show;
const shown = () => h.toolbars.at(-1).show;
// The link changes the page made to its show.
const showPatches = () =>
  h.url.patch.mock.calls.filter(([change]) => "show" in (change ?? {}));

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.views = [];
  h.toolbars = [];
  h.columns = { columns: [], visibleIds: [METRIC] };
  h.query = {
    data: population(),
    isError: false,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  };
  // The link names Worse.
  h.url = {
    pills: [],
    show: "regressed",
    agg: "run",
    diff: "off",
    patch: vi.fn(),
  };
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("The show a view reads", () => {
  const view = { runIds: RUNS, baselineId: "baseline" };

  it("keeps Worse, Better and Changed with a baseline and another run", () => {
    for (const show of ["regressed", "improved", "changed"])
      expect(showForView(show, view)).toBe(show);
  });

  it("reads them as All with no baseline, or the baseline as the only run", () => {
    for (const show of ["regressed", "improved", "changed"]) {
      expect(showForView(show, { runIds: RUNS, baselineId: "none" })).toBe(
        "all",
      );
      expect(showForView(show, { runIds: RUNS })).toBe("all");
      expect(
        showForView(show, {
          runIds: ["baseline", "baseline"],
          baselineId: "baseline",
        }),
      ).toBe("all");
    }
  });

  it("reads every show as All with one run, and no show as All", () => {
    expect(
      showForView("regressed", {
        runIds: ["baseline"],
        baselineId: "baseline",
      }),
    ).toBe("all");
    expect(showForView(null, view)).toBe("all");
    expect(showForView(undefined, view)).toBe("all");
  });
});

describe("The results page's show without a baseline", () => {
  it("sends Worse, and hands the toolbar the show and the runs in view, while there is a baseline", () => {
    render(page());
    expect([sent(), shown()]).toEqual(["regressed", "regressed"]);
    expect(h.toolbars.at(-1).runIds).toEqual(RUNS);
  });

  it("reads a linked Worse as All while the baseline is none, and asks for no show", () => {
    render(page({ baselineId: "none" }));
    expect([sent(), shown()]).toEqual([undefined, "all"]);
    for (const view of h.views)
      expect(runResultsParams(view).show).toBeUndefined();
    expect(showPatches()).toEqual([]);
  });

  it("applies the linked Worse again once the baseline is back, and keeps the link", () => {
    const { rerender } = render(page({ baselineId: "none" }));
    expect([sent(), shown()]).toEqual([undefined, "all"]);

    rerender(page({ baselineId: "baseline" }));
    expect([sent(), shown()]).toEqual(["regressed", "regressed"]);
    expect(showPatches()).toEqual([]);
  });
});
