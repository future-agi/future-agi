/* eslint-disable react/prop-types */
// The review queue walks the show in view, and its last row offers the next
// one: Better after Worse, Changed after Better. With the read's count for
// the next show the offer carries that number, and a count of none offers
// nothing. While Hide rows with no output is on, the read's counts are not
// passed on (they count the rows the switch hides as well), so the toolbar
// shows none; the queue still offers the next show, with no number.
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
  // What the page's toolbar is handed, and the page's columns.
  toolbars: [],
  columns: { columns: [], visibleIds: [] },
}));
vi.mock("src/api/develop/run-results", async () => {
  const actual = await vi.importActual("src/api/develop/run-results");
  return {
    ...actual,
    useRunResults: () => h.query,
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
// The queue the page hands the review loop is the consumer under test.
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
// The toolbar's words for the shows, as on screen.
vi.mock("../ResultsToolbar", () => ({
  default: (props) => {
    h.toolbars.push(props);
    return null;
  },
  anyWorseTip: () => "",
  showLabel: (k) =>
    k === "regressed" ? "Worse" : k === "improved" ? "Better" : "Changed",
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
import {
  rowsWithOutput,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";

const RUNS = ["baseline", "candidate"];
const METRIC = "eval:resolution";
// What the read counted over the rows it answered (more than this page's
// two rows, so its numbers and the rows in view tell apart).
const COUNTED = { regressed: 7, improved: 3, changed: 5 };
const row = (n, output) => ({
  example_id: `row-${n}`,
  row_order: n + 1,
  inputs: { question: `input ${n}` },
  results: {
    baseline: { output, scores: { [METRIC]: { value: 1 } } },
    candidate: { output, scores: { [METRIC]: { value: 0 } } },
  },
});
// The read's answer: two rows, both with an output unless the second has
// none from either run.
const answer = ({ counts = COUNTED, emptyRow = false } = {}) => ({
  total: 2,
  summary: {
    examples_total: 2,
    baseline_run_id: "baseline",
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
    show_counts: counts,
  },
  rows: [row(0, "Paris"), row(1, emptyRow ? "" : "Lyon")],
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
const page = () => (
  <QueryClientProvider client={client}>
    <MemoryRouter
      initialEntries={["/dashboard/develop/experiment/experiment-a/data"]}
    >
      <RunResultsView {...props} />
    </MemoryRouter>
  </QueryClientProvider>
);
// Open the page on a show, with the switch on or off. With it on, the page
// gets what the read hands it then: the answer without its rows with no
// output, and without the read's counts.
const open = ({ show = "regressed", hide = false, ...given } = {}) => {
  const read = answer(given);
  h.query = { ...h.query, data: hide ? rowsWithOutput(read, RUNS) : read };
  h.url = { ...h.url, show, pills: hide ? withHideEmpty([], true) : [] };
  render(page());
  return h.setQueue.mock.calls.at(-1)[0];
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.toolbars = [];
  h.columns = { columns: [], visibleIds: [METRIC] };
  h.setQueue.mockClear();
  h.query = {
    data: answer(),
    isError: false,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  };
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

describe("The review queue's next show with Hide rows with no output off", () => {
  it("offers Better with the read's number after Worse", () => {
    const queue = open();
    expect(queue.label).toBe("Worse (7)");
    expect(queue.rowIds).toEqual(["row-0", "row-1"]);
    expect(queue.next.label).toBe("Better (3)");
  });

  it("offers Changed with the read's number after Better", () => {
    expect(open({ show: "improved" }).next.label).toBe("Changed (5)");
  });

  it("offers nothing after Worse when the read counted no Better row", () => {
    const queue = open({ counts: { regressed: 2, improved: 0, changed: 2 } });
    expect(queue.next).toBeNull();
  });

  it("offers nothing after Changed", () => {
    expect(open({ show: "changed" }).next).toBeNull();
  });

  it("offers Better with no number when the read counted none", () => {
    expect(open({ counts: null }).next.label).toBe("Better");
  });

  it("hands the toolbar the read's counts", () => {
    open();
    expect(h.toolbars.at(-1).showCounts).toEqual(COUNTED);
  });
});

describe("The review queue's next show with Hide rows with no output on", () => {
  it.each([
    ["it hides no row", false, ["row-0", "row-1"]],
    ["it hides a row", true, ["row-0"]],
  ])("offers Better with no number after Worse when %s", (_, emptyRow, ids) => {
    const queue = open({ hide: true, emptyRow });
    expect(queue.rowIds).toEqual(ids);
    // The queue names the rows in view, not the read's number.
    expect(queue.label).toBe(`Worse (${ids.length})`);
    expect(queue.next.label).toBe("Better");
  });

  it("offers Changed with no number after Better", () => {
    expect(open({ hide: true, show: "improved" }).next.label).toBe("Changed");
  });

  it("goes to the next show when taken", () => {
    const queue = open({ hide: true });
    queue.next.onGo();
    expect(h.url.patch).toHaveBeenLastCalledWith({
      show: "improved",
      row: null,
    });
  });

  it("offers nothing after Changed", () => {
    expect(open({ hide: true, show: "changed" }).next).toBeNull();
  });

  it("keeps the read's counts off the toolbar", () => {
    open({ hide: true });
    expect(h.toolbars.at(-1).showCounts).toBeNull();
  });
});
