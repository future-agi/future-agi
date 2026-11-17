/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  noop: () => {},
  query: null,
  url: null,
  configs: { results: [] },
  post: vi.fn(),
  say: vi.fn(),
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: h.post, get: vi.fn() },
}));
vi.mock("src/api/develop/run-results", async (original) => ({
  ...(await original()),
  useRunResults: () => h.query,
}));
vi.mock("src/api/develop/r2", async (original) => ({
  ...(await original()),
  useRunConfigs: () => ({ data: h.configs }),
  useMatrix: () => ({ data: null }),
}));
vi.mock("src/api/develop/r4", async (original) => ({
  ...(await original()),
  useVoiceSummary: () => ({ data: null }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "editor" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { UPDATE: "update" },
  RolePermission: { DATASETS: { update: { editor: true } } },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.say, closeSnackbar: h.noop }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  default: h.noop,
  notify: h.noop,
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: h.noop }));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: h.noop,
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
vi.mock("src/sections/common/Runs/rescoreCopyModel", () => ({
  scoreWithChoices: () => [],
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (original) => ({
  ...(await original()),
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("src/sections/common/filters/ResultsExpressionDialog", () => ({
  default: () => null,
}));
vi.mock("../useResultsUrlState", () => ({ default: () => h.url }));
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
  default: () => ({ candidateId: "http-run" }),
}));
vi.mock("../../iterate/iterateStore", () => ({
  useIterateStore: (select) => select({ finished: {}, dismissed: [] }),
  fixCounterText: () => "",
}));
vi.mock("../../iterate/iterateModel", () => ({ canIterate: () => false }));
vi.mock("../reviewMarks", () => ({
  useReviewMarks: Object.assign((select) => select({ marks: {} }), {
    getState: () => ({ setQueue: h.noop }),
  }),
  pairOf: () => "pair",
  reviewedText: () => null,
}));
vi.mock("../resultsColumns", () => ({
  RESULTS_COLUMN_GROUPS: [],
  buildResultColumns: () => [],
  scorePinFor: () => null,
}));
vi.mock("../ResultsGrid", () => ({
  default: React.forwardRef(function Grid({ rows, context }, ref) {
    return (
      <button
        disabled={context.readOnly}
        onClick={() => context.onRerunCell(rows[0], "http-run")}
      >
        Grid rerun cell
      </button>
    );
  }),
}));
vi.mock("../ResultDrawer", () => ({ default: ({ grid }) => grid }));
vi.mock("../ResultsVerdict", () => ({ default: () => null }));
vi.mock("../ResultsToolbar", () => ({
  default: () => null,
  anyWorseTip: () => "",
  showLabel: (v) => v,
}));
vi.mock("../RunBar", () => ({ default: () => null }));
vi.mock("../RunsChip", () => ({ default: ({ children }) => children }));
vi.mock("../LiveStatus", () => ({ default: () => null }));
vi.mock("../AddResultsToDatasetDialog", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: ({ actions }) => {
    const action = actions.find((item) => item.id === "rerun");
    return (
      action && (
        <button disabled={action.disabled} onClick={action.menu[0].run}>
          Selected rows rerun
        </button>
      )
    );
  },
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({ ids: selectedIds }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Selection/askFalconAboutRows", () => ({
  askFalconAboutRows: h.noop,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("../FilterPills", () => ({ default: () => null }));
vi.mock("../EvalDetailsDialog", () => ({ default: () => null }));
vi.mock("../SeeWhyButton", () => ({ default: () => null }));
vi.mock(
  "../../ExperimentBarRightSection/ExperimentBarDataRightSection",
  () => ({ default: () => null }),
);
vi.mock("../../ExperimentData/ExperimentStatusBar", () => ({
  ResultsStatusLine: () => null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: true, run: true, download: true },
    reason: () => null,
  }),
}));
vi.mock("src/sections/develop/useDatasetColumnsLite", () => ({
  default: () => ({ data: [] }),
}));

import SelectionBar from "../SelectionBar";
import RunResultsView from "../RunResultsView";
const clients = [];
const run = {
  run_id: "http-run",
  source: "http",
  task_kind: "external",
  experiment_id: "experiment-a",
  output_column_id: "output-h",
  label: "Endpoint",
  status: "Completed",
};
const row = {
  example_id: "example-a",
  inputs: { question: "selected" },
  results: { "http-run": { status: "completed", output: "success" } },
};
function page(kind, readOnly = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const element =
    kind === "selection" ? (
      <SelectionBar
        datasetId="dataset"
        runs={[run]}
        selectedRows={[row]}
        readOnly={readOnly}
        onClear={h.noop}
      />
    ) : (
      <RunResultsView
        datasetId="dataset"
        runIds={[run.run_id]}
        initialRuns={[run]}
        mode="experiment"
        experimentId="experiment-a"
        onBaselineChange={h.noop}
      />
    );
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  h.post.mockResolvedValue({ data: { status: true } });
  h.url = { pills: [], show: "all", agg: "run", diff: "off", patch: h.noop };
  h.query = {
    data: { rows: [row], total: 1, summary: { runs: [run], metrics: [] } },
    isError: false,
    isLoading: false,
    refetch: h.noop,
  };
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

describe.each(["selection", "grid"])("%s canonical HTTP caller", (kind) => {
  it("renders the real confirmation and sends only after an explicit confirmation, not cancellation", async () => {
    render(page(kind));
    const name =
      kind === "selection" ? "Selected rows rerun" : "Grid rerun cell";
    fireEvent.click(screen.getByRole("button", { name }));
    let dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      "The endpoint may already have processed the earlier requests",
    );
    expect(h.post).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(h.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name }));
    dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Send again" }));
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    expect(h.post.mock.calls[0][1]).toEqual({
      cells: [
        { column_id: "output-h", run_id: "http-run", example_id: "example-a" },
      ],
      failed_only: false,
      acknowledge_http_side_effects: true,
    });
    expect(h.post.mock.calls[0][2].headers["Idempotency-Key"]).toMatch(
      /^fa-http-/,
    );
    await waitFor(() =>
      expect(h.say).toHaveBeenCalledWith(
        kind === "selection" ? "Re-running 1 cell" : "Re-running the cell",
        { variant: "success" },
      ),
    );
  });
});
it("preserves the selection bar read-only gate", () => {
  render(page("selection", true));
  expect(
    screen.getByRole("button", { name: "Selected rows rerun" }),
  ).toBeDisabled();
  expect(h.post).not.toHaveBeenCalled();
});
