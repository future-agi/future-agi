/* eslint-disable react/prop-types */
// On Compare runs, Matrix with a second run read "Couldn't load the matrix."
// with Retry under a toast, because the two runs use different dataset
// versions, trial plans or evaluators and the results endpoint refuses to
// line them up. The Matrix now reads only the runs in the baseline's group,
// says in one line which runs it shows and which it leaves out (above the
// loaded matrix too), keeps every run in the page's URL, and a refusal it
// could not foresee reads in plain words. On the experiment page, one run
// shown while the experiment's other runs can't be lined up with it says so
// instead of "Pick at least 2 runs".
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  records: [],
  r1Loading: false,
  matrixArgs: [],
  matrixData: null,
  noop: () => {},
}));

vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetRuns: () => ({
    data: { results: h.records, pinned_run_id: null },
    isLoading: h.r1Loading,
  }),
}));
vi.mock("../useMatrixData", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (args) => {
    h.matrixArgs.push(args);
    return h.matrixData;
  },
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: undefined, held: false }),
}));
vi.mock("../useVoiceMatrix", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "viewer" }),
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, h.noop],
}));
vi.mock("src/sections/common/ux/oneview/useViewportDensity", () => ({
  default: () => "regular",
}));
vi.mock("src/sections/common/Runs/runColors", () => ({
  useRunColors: () => ({}),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows", dataset: null }),
}));
vi.mock("src/sections/common/MatrixRuns/MatrixRunMenu", () => ({
  default: () => ({
    columnItems: () => [],
    cellItems: () => [],
    withLines: (roles) => roles,
  }),
}));
vi.mock("src/sections/common/ux/useShortcuts", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.noop,
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: () => {},
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: ({ excludeIds, label }) => (
    <button data-testid="run-picker" data-exclude={excludeIds.join(",")}>
      {label}
    </button>
  ),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The loaded matrix: the drawer shows the grid it is given; the grid, the
// toolbar, the verdict line, the chart and the cards are stand-ins.
vi.mock("../../results/ResultDrawer", () => ({
  default: ({ grid }) => <div data-testid="matrix-drawer">{grid}</div>,
}));
vi.mock("../MatrixGrid", async (importOriginal) => {
  const { forwardRef } = await import("react");
  return {
    ...(await importOriginal()),
    default: forwardRef(function MatrixGridStub({ model }, _ref) {
      return <div data-testid="matrix-grid">{model.runIds.join(",")}</div>;
    }),
  };
});
vi.mock("../MatrixHeader", async (importOriginal) => ({
  ...(await importOriginal()),
  MatrixToolbar: () => null,
}));
vi.mock("../MatrixLegend", async (importOriginal) => ({
  ...(await importOriginal()),
  LegendHelp: () => null,
}));
vi.mock("../ParetoStrip", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../PatternCallout", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../MatrixHoverCard", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../results/VerdictFold", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.noop,
}));

import MatrixView from "../MatrixView";
import { MIXED_RUNS_WORDS } from "src/utils/mixedRunsRefusal";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";
const RECORDS = [
  { run_id: A, label: "#1", comparison_group: "plan-old" },
  { run_id: B, label: "#2", comparison_group: "plan-new" },
  { run_id: C, label: "#3", comparison_group: "plan-new" },
];
const MIXED = {
  statusCode: 409,
  result:
    "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.",
};
const idle = (over = {}) => ({
  matrix: null,
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
  running: false,
  allRuns: [],
  runsById: {},
  inView: [],
  rootCauses: null,
  frontierShift: null,
  ...over,
});

// R3 shape=matrix of #2 and #3 (completed, one try per row).
const doneCell = (pass) => ({
  trials: [pass],
  trial_numbers: [1],
  result_ids: [`result-${pass}`],
  cost_usd: [null],
  wall_ms: [12],
  trace_ids: [null],
  trace_summary: [{ status: "completed", trial_number: 1, carried: false }],
  failed_evals: [[]],
  carried: [false],
  scores: {},
});
const column = {
  trials: 1,
  status: "completed",
  trial_count_source: "immutable_plan",
};
const LOADED = {
  total: 2,
  rows: [1, 2].map((n) => ({
    row_id: `row-${n}`,
    n,
    label: `T${n}`,
    values: {},
    source: null,
    cells: { [B]: doneCell(1), [C]: doneCell(n === 1 ? 1 : 0) },
  })),
  columns: { [B]: column, [C]: column },
  summary: {
    runs: [
      { run_id: B, label: "#2", status: "completed" },
      { run_id: C, label: "#3", status: "completed" },
    ],
    metrics: [],
  },
};
const LOADED_RUNS = {
  [B]: { run_id: B, label: "#2", status: "completed" },
  [C]: { run_id: C, label: "#3", status: "completed" },
};
// An R1 record of an experiment's run.
const record = (runId, label, experimentId, group) => ({
  run_id: runId,
  label,
  experiment_id: experimentId,
  comparison_group: group,
});

const renderCompare = (runIds, baselineId = B) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter
        initialEntries={[
          `/dashboard/develop/ds-1/compare/matrix?runs=${runIds.join(",")}&baseline=${baselineId}`,
        ]}
      >
        <MatrixView
          mode="compare"
          datasetId="ds-1"
          runIds={runIds}
          baselineId={baselineId}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
const renderExperiment = (runIds) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter
        initialEntries={["/dashboard/develop/experiment/exp-1/matrix"]}
      >
        <MatrixView
          mode="experiment"
          datasetId="ds-1"
          experimentId="exp-1"
          runIds={runIds}
          baselineId={runIds[0]}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
const lastRead = () => h.matrixArgs[h.matrixArgs.length - 1];

beforeEach(() => {
  h.records = RECORDS;
  h.r1Loading = false;
  h.matrixArgs = [];
  h.matrixData = idle();
});
afterEach(() => cleanup());

describe("Matrix on Compare runs, runs that can't be lined up", () => {
  it("never reads the mix; one plain line names both runs, Add runs stays", () => {
    renderCompare([A, B]);
    expect(lastRead().runIds).toEqual([B]);
    for (const args of h.matrixArgs) expect(args.runIds).not.toContain(A);
    expect(screen.getByTestId("matrix-left-out")).toHaveTextContent(
      "#1 and #2 can't be lined up row by row: they use different dataset versions, trial plans or evaluators. Pick runs that share all three.",
    );
    expect(screen.queryByText(/Couldn.t load the matrix/)).toBeNull();
    // The picker still knows both runs are on the page.
    expect(screen.getByTestId("run-picker").dataset.exclude).toBe(`${A},${B}`);
  });

  it("reads the baseline's group and names the run it leaves out", () => {
    renderCompare([A, B, C]);
    expect(lastRead().runIds).toEqual([B, C]);
    expect(lastRead().baselineId).toBe(B);
    expect(
      screen.getByText(
        "Showing #2 and #3. #1 is left out: it uses a different dataset version, trial plan or evaluators.",
      ),
    ).toBeTruthy();
  });

  it("names the run it leaves out above the loaded matrix", () => {
    h.matrixData = idle({
      matrix: LOADED,
      runsById: LOADED_RUNS,
      inView: Object.values(LOADED_RUNS),
    });
    renderCompare([A, B, C]);
    expect(lastRead().runIds).toEqual([B, C]);
    // The matrix itself, not the empty state.
    expect(screen.getByTestId("matrix-view")).toBeTruthy();
    expect(screen.queryByTestId("matrix-left-out")).toBeNull();
    const grid = screen.getByTestId("matrix-grid");
    expect(grid).toHaveTextContent(`${B},${C}`);
    const notice = screen.getByText(
      "Showing #2 and #3. #1 is left out: it uses a different dataset version, trial plan or evaluators.",
    );
    expect(
      notice.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("sends no read while the runs' groups are still loading", () => {
    h.r1Loading = true;
    renderCompare([A, B]);
    expect(h.matrixArgs.length).toBeGreaterThan(0);
    for (const args of h.matrixArgs) expect(args.enabled).toBe(false);
    expect(screen.getByTestId("matrix-loading")).toBeTruthy();
  });

  it("says a refusal it could not foresee in plain words, with Retry", () => {
    h.records = [];
    h.matrixData = idle({ isError: true, error: MIXED });
    renderCompare([A, B]);
    expect(lastRead().runIds).toEqual([A, B]);
    expect(screen.getByText(MIXED_RUNS_WORDS)).toBeTruthy();
    expect(screen.queryByText(/Couldn.t load the matrix/)).toBeNull();
    expect(screen.queryByText(/mixed comparison is not supported/)).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("keeps the old words for any other failed read", () => {
    h.records = [];
    h.matrixData = idle({ isError: true, error: { statusCode: 500 } });
    renderCompare([A, B]);
    expect(screen.getByText("Couldn’t load the matrix.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

describe("Matrix on the experiment page, one run in its group", () => {
  it("says the experiment's runs can't be lined up, not 'Pick at least 2 runs'", () => {
    // Newest first: #2 re-ran the evaluation with other evaluators; #3 is
    // another experiment's run.
    h.records = [
      record(B, "#2", "exp-1", "g-new"),
      record(A, "#1", "exp-1", "g-old"),
      record(C, "#3", "exp-2", "g-old"),
    ];
    renderExperiment([B]);
    for (const args of h.matrixArgs) expect(args.runIds).toEqual([B]);
    expect(screen.getByTestId("matrix-left-out")).toHaveTextContent(
      "#2 and #1 can't be lined up row by row: they use different dataset versions, trial plans or evaluators. Pick runs that share all three.",
    );
    expect(
      screen.queryByText(/Matrix works with any runs on this dataset/),
    ).toBeNull();
    expect(screen.getByTestId("run-picker")).toHaveTextContent("Add runs");
  });

  it("keeps 'Pick at least 2 runs' for an experiment's first run", () => {
    h.records = [record(B, "#1", "exp-1", "g")];
    renderExperiment([B]);
    expect(screen.queryByTestId("matrix-left-out")).toBeNull();
    expect(
      screen.getByText(/Matrix works with any runs on this dataset/),
    ).toBeTruthy();
  });
});
