/* eslint-disable react/prop-types */
// A link that names some of an experiment's runs (a dataset row's Compare
// side by side) opens the Data tab on those runs. The page header reads the
// same runs while the Data tab is open: its verdict is about the runs the
// grid shows, never a run the link left out, and its live counts are the
// grid's. The other tabs keep the Run history group, as their reads do. The
// Rerun menu acts on the whole experiment, so its numbers keep reading the
// Run history group whatever the link names. Linked runs of two set-ups are
// never read together: the page keeps a Run history group and says why.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const h = vi.hoisted(() => ({
  experiment: null,
  matrixReads: [],
  grids: [],
  useResultsUrlState: null,
  usePublishLiveCounts: null,
  sortForView: null,
}));

vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentDetail: () => ({ data: h.experiment }),
  useDatasetRuns: () => ({ data: { results: [], pinned_run_id: null } }),
}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Support questions" }],
  }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: (params, options) => {
    h.matrixReads.push({ params, options });
    // One row: #3 fails what #1 and #2 pass.
    const passed = { a: 1, b: 1, c: 0 };
    const cells = Object.fromEntries(
      params.runIds.map((id) => [id, { trials: [passed[id]] }]),
    );
    return { data: { rows: [{ id: "row-1", n: 1, cells }] } };
  },
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: true } }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/x?iterate=1",
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Runs/RunStatusChip", async (importOriginal) => ({
  ...(await importOriginal()),
  StatusChip: ({ status, label }) => (
    <div data-testid="experiment-status">{label ?? status}</div>
  ),
}));
vi.mock("../DevelopExperimentSelect", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span>Prompt sweep</span>,
}));
vi.mock("src/sections/common/EvaluationDrawer/EvaluationDrawer", () => ({
  default: () => null,
}));
vi.mock("../experiment-context", () => ({
  useExperimentDetailContext: () => ({
    evaluateOpen: false,
    setEvaluateOpen: () => {},
  }),
}));
// The Data tab's grid: it reads the view RunResultsView builds for the runs
// it is given and shares the counts it shows (each run three rows in).
vi.mock("../results/RunResultsView", () => ({
  default: function DataGrid({ datasetId, runIds, baselineId, experimentId }) {
    const url = h.useResultsUrlState();
    h.grids.push({ runIds, baselineId });
    const view = {
      runIds,
      baselineId,
      pills: url.pills,
      show: runIds.length > 1 ? url.show : "all",
      search: "",
      sort: h.sortForView(null, { runIds, baselineId }),
      agg: url.agg === "run" || url.agg === "pass" ? undefined : url.agg,
      mixVersions: url.mixVersions,
    };
    const counts = Object.fromEntries(
      runIds.map((id) => [id, { done: 3, total: 4 }]),
    );
    h.usePublishLiveCounts(experimentId, counts, {
      datasetId,
      view,
      blocked: false,
    });
    return (
      <div data-testid="data-grid">{`${runIds.join(",")}|${baselineId}`}</div>
    );
  },
}));

import ExperimentRow from "../ExperimentRow";
import ExperimentDataView from "../ExperimentData/ExperimentDataView";
import useResultsUrlState from "../results/useResultsUrlState";
import { sortForView } from "../results/sortModel";
import { useLiveCounts, usePublishLiveCounts } from "../results/liveCountShare";

h.useResultsUrlState = useResultsUrlState;
h.usePublishLiveCounts = usePublishLiveCounts;
h.sortForView = sortForView;

// The catalog lists runs newest first; all three line up in one group
// unless a case names another.
const run = (id, label, status, progress, group = "same-plan") => ({
  run_id: id,
  label,
  name: "Prompt",
  status,
  comparison_group: group,
  progress,
});
const catalog = (status, progress) => ({
  id: "e1",
  ordinal: 1,
  name: "Prompt sweep",
  dataset_id: "ds",
  runs: [
    run("c", "#3", status, progress),
    run("b", "#2", status, progress),
    run("a", "#1", status, progress),
  ],
});
// What a dataset row's Compare side by side opens for #1 and #2.
const LINK = "?runs=a,b&baseline=a&history=a";

function renderPage(tab, search) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[`/dashboard/develop/experiment/e1/${tab}${search}`]}
      >
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/data"
            element={
              <>
                {/* The page mounts the header before the tab's outlet. */}
                <ExperimentRow />
                <ExperimentDataView />
              </>
            }
          />
          <Route
            path="/dashboard/develop/experiment/:experimentId/summary"
            element={<ExperimentRow />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastMatrixRead = () => h.matrixReads[h.matrixReads.length - 1].params;
// Every distinct scope the header read, as "runs|baseline".
const matrixScopes = () =>
  [
    ...new Set(
      h.matrixReads.map(
        ({ params }) => `${params.runIds.join(",")}|${params.baselineId}`,
      ),
    ),
  ].sort();
const lastGrid = () => h.grids[h.grids.length - 1];

beforeEach(() => {
  window.innerWidth = 1600;
  h.matrixReads = [];
  h.grids = [];
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  cleanup();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
});

describe("the experiment header with a link that names some runs", () => {
  const done = { total: 1, done: 1, errors: 0 };

  it("judges the newest run against the baseline when no runs are named", () => {
    h.experiment = catalog("Completed", done);
    renderPage("data", "?baseline=a");
    expect(lastGrid()).toEqual({ runIds: ["c", "b", "a"], baselineId: "a" });
    expect(lastMatrixRead()).toMatchObject({
      runIds: ["c", "b", "a"],
      baselineId: "a",
    });
    // The verdict and the Rerun menu read one scope.
    expect(matrixScopes()).toEqual(["c,b,a|a"]);
    expect(screen.getByTestId("exp-primary-fix")).toBeInTheDocument();
  });

  it("reads the linked runs for its verdict, as the Data tab shows them", () => {
    h.experiment = catalog("Completed", done);
    renderPage("data", LINK);
    expect(lastGrid()).toEqual({ runIds: ["a", "b"], baselineId: "a" });
    // The verdict reads the linked runs; the Rerun menu reads the whole Run
    // history group. Nothing else is read, so no read mixes groups.
    expect(matrixScopes()).toEqual(["a,b|a", "c,b,a|a"]);
    expect(screen.queryByTestId("exp-primary-fix")).toBeNull();
  });

  it("keeps the failed rows of a run the link leaves out in the Rerun menu", async () => {
    // Three prompt runs, one per configuration, in one group: the link
    // compares #1 and #2, which pass, and leaves out #3, which failed a check.
    h.experiment = catalog("Completed", done);
    renderPage("data", LINK);
    expect(screen.queryByTestId("exp-primary-fix")).toBeNull();
    fireEvent.click(
      within(screen.getByTestId("exp-rerun-anchor")).getByRole("button", {
        name: /^Rerun/,
      }),
    );
    await screen.findByTestId("exp-rerun-menu");
    const failed = screen.getByTestId("exp-rerun-failed");
    expect(failed).not.toHaveAttribute("aria-disabled", "true");
    expect(failed).toHaveTextContent("Failed rows (1)");
    expect(failed).not.toHaveTextContent("no row failed a check");
  });

  it("never reads linked runs of two set-ups together, keeps a history group and says why", () => {
    // #3 is a later subset retry: it sits in another comparison group than
    // #1 and #2. A dataset row's Compare side by side for #1 and #3 opened
    // this link before the row knew the groups.
    h.experiment = {
      ...catalog("Completed", done),
      runs: [
        run("c", "#3", "Completed", done, "subset-retry"),
        run("b", "#2", "Completed", done),
        run("a", "#1", "Completed", done),
      ],
    };
    renderPage("data", "?runs=a,c&baseline=a&history=a");
    const mixes = (ids) => ids.includes("c") && ids.includes("a");
    expect(h.grids.length).toBeGreaterThan(0);
    expect(h.grids.filter(({ runIds }) => mixes(runIds))).toEqual([]);
    expect(h.matrixReads.filter(({ params }) => mixes(params.runIds))).toEqual(
      [],
    );
    // The grid, the verdict and the Rerun menu read #1's history group.
    expect(lastGrid()).toEqual({ runIds: ["b", "a"], baselineId: "a" });
    expect(matrixScopes()).toEqual(["b,a|a"]);
    expect(
      screen.getByRole("combobox", { name: "Run history" }),
    ).toHaveDisplayValue("Runs #2, #1");
    expect(
      screen.getByText(
        "Runs #1 and #3 can't be compared side by side because their set-ups differ.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the Run history group on a tab that does not show the linked runs", () => {
    h.experiment = catalog("Completed", done);
    renderPage("summary", LINK);
    expect(lastMatrixRead()).toMatchObject({
      runIds: ["c", "b", "a"],
      baselineId: "a",
    });
    expect(screen.getByTestId("exp-primary-fix")).toBeInTheDocument();
  });
});

describe("the experiment header's live counts", () => {
  const started = { total: 4, done: 1, errors: 0 };

  it("takes every run's count from the grid when no runs are named", () => {
    h.experiment = catalog("Running", started);
    renderPage("data", "?baseline=a");
    expect(screen.getByTestId("experiment-status")).toHaveTextContent(
      "Running · 9/12",
    );
  });

  it("takes the Run history group's counts from the grid when the runs are in two groups", () => {
    h.experiment = {
      ...catalog("Running", started),
      runs: [
        run("c", "#3", "Running", started, "rescored"),
        run("b", "#2", "Running", started, "first"),
        run("a", "#1", "Running", started, "first"),
      ],
    };
    renderPage("data", "");
    expect(lastGrid()).toEqual({ runIds: ["c"], baselineId: "c" });
    expect(screen.getByTestId("experiment-status")).toHaveTextContent(
      "Running · 5/12",
    );
  });

  it("takes the linked runs' counts from the grid and the other run's from its poll", () => {
    h.experiment = catalog("Running", started);
    renderPage("data", LINK);
    expect(lastGrid()).toEqual({ runIds: ["a", "b"], baselineId: "a" });
    expect(screen.getByTestId("experiment-status")).toHaveTextContent(
      "Running · 7/12",
    );
  });
});

describe("the Summary header with only accepted Results owners", () => {
  it("hides both primary and menu Iterate doors without losing the header", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      showUnreleased: false,
    });
    expect(isSurfaceShown("exp.results.summary")).toBe(true);
    expect(isSurfaceShown("exp.results.iterateDock")).toBe(false);
    h.experiment = catalog("Completed", { total: 1, done: 1, errors: 0 });
    renderPage("summary", LINK);
    expect(screen.getByTestId("experiment-status")).toBeTruthy();
    expect(screen.queryByTestId("exp-primary-fix")).toBeNull();
    const anchor = screen.getByTestId("exp-rerun-anchor");
    fireEvent.click(within(anchor).getByRole("button", { name: /^Rerun/ }));
    await screen.findByTestId("exp-rerun-menu");
    expect(screen.queryByText(/Test a fix/)).toBeNull();
  });
});
