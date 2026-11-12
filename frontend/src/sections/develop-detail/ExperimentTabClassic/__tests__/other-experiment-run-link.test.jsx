/* eslint-disable react/prop-types */
// A run of an experiment listed under "Other experiments" stays in the runs
// view: a run with data never leaves a list because its experiment is also
// listed elsewhere. Its row says which experiment it belongs to, as a link
// that opens that experiment at the page the "Other experiments" row opens.
// Runs of experiments the runs view shows in full, and runs view rows where
// the tab lists no such group, are unchanged.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
const http = vi.hoisted(() => ({ list: vi.fn(), other: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const listUrl = actual.endpoints.develop.experiment.experimentListPaginated;
  const get = (url, ...rest) =>
    (url === listUrl ? http.list : http.other)(url, ...rest);
  return { ...actual, default: { get, post: vi.fn(), delete: vi.fn() } };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: { id: "u1" } }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  resetUser: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
// The run row's identity block, chips and tags have their own tests; here
// the identity block shows the row's name link and its pills.
vi.mock("src/sections/develop-detail/ExperimentTab/RunIdentity", () => ({
  default: ({ link, pills }) => (
    <span>
      {link()}
      {pills}
    </span>
  ),
}));
vi.mock("src/sections/common/Runs/RunsCountChip", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Tags/ExperimentTagChips", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/common/RunSources/SourceLogo",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock(
  "src/sections/common/RunSources/SourceCards",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => <div data-testid="ways-to-run" />,
  }),
);
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data }) => (
    <div>
      {data.map((row) => (
        <div key={row.id} data-testid={`row-${row.id}`}>
          {row.name}
        </div>
      ))}
    </div>
  ),
  DataTablePagination: () => null,
}));

// The runs view: the real LeaderboardShell, whose grid renders each run's
// real Experiment cell (ExperimentNameCell) the way the grid gives it a row.
const runsTab = vi.hoisted(() => ({ runs: [], group: null }));
vi.mock("src/sections/develop-detail/ExperimentTab/ExperimentTab", async () => {
  const R = await import("react");
  const { LeaderboardShell } = await import(
    "src/sections/develop-detail/ExperimentTab/LeaderboardStates"
  );
  const { default: ExperimentNameCell } = await import(
    "src/sections/develop-detail/ExperimentTab/ExperimentNameCell"
  );
  return {
    default: R.forwardRef(function RunsView() {
      const runs = runsTab.runs;
      const visibleByExperiment = {};
      for (const run of runs) {
        (visibleByExperiment[run.experiment_id] ??= []).push(run);
      }
      const context = {
        datasetId: "11111111-1111-4111-8111-111111111111",
        group: runsTab.group,
        q: "",
        allRuns: runs,
        visibleByExperiment,
        latestExperimentId: null,
        baselineId: null,
        numbers: null,
      };
      const view = {
        query: { data: {}, isLoading: false, isError: false, refetch() {} },
        datasetId: context.datasetId,
        allRuns: runs,
        runs,
        version: "all",
        q: "",
        activeFilters: [],
        setQ() {},
        setFilters() {},
        setVersion() {},
      };
      const grid = (
        <div data-testid="runs-grid">
          {runs.map((run) => (
            <div key={run.run_id} data-testid={`run-${run.run_id}`}>
              <ExperimentNameCell
                node={{ group: false, data: run }}
                data={run}
                context={context}
              />
            </div>
          ))}
        </div>
      );
      return (
        <LeaderboardShell view={view} onClearFilters={() => {}} grid={grid} />
      );
    }),
  };
});
/* eslint-enable react/prop-types */

import ExperimentTabSwitch from "../ExperimentTabSwitch";

const DATASET = "11111111-1111-4111-8111-111111111111";
const MIXED = "00000000-0000-4000-8000-00000000000a";
const FULL = "00000000-0000-4000-8000-00000000000b";
const HEADING = "Other experiments";

const run = (runId, experimentId, experimentName, label) => ({
  run_id: runId,
  experiment_id: experimentId,
  experiment_name: experimentName,
  label,
  name: label,
  status: "completed",
});
// A mixed experiment: two recorded runs (its versioned variants); its third
// variant used a prompt with no saved version and has no run.
const MIXED_RUNS = [
  run("r1", MIXED, "Support prompts, mixed", "#1A"),
  run("r2", MIXED, "Support prompts, mixed", "#1B"),
];
const FULL_RUN = run("r3", FULL, "Support prompts, recorded", "#2A");
const listRow = (id, name, full) => ({
  id,
  name,
  status: "Completed",
  dataset: DATASET,
  opens_in_full_results: full,
});
const LIST = {
  data: {
    count: 2,
    next: null,
    results: [
      listRow(FULL, "Support prompts, recorded", true),
      listRow(MIXED, "Support prompts, mixed", false),
    ],
  },
};
const payloadOf = (id, name) =>
  encodeURIComponent(JSON.stringify({ id, name }));
const ORIGINAL_PAGE =
  `/dashboard/develop/experiment/${MIXED}/data?datasetId=${DATASET}` +
  `&payload=${payloadOf(MIXED, "Support prompts, mixed")}`;

const theme = createTheme({ palette: palette("light") });
let client;
function openTab() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tab = (
    <ExperimentTabSwitch experimentSearch="" setRowSelected={() => {}} />
  );
  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET}`]}>
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={tab} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  http.list.mockReset();
  http.other.mockReset();
  http.list.mockResolvedValue(LIST);
  http.other.mockResolvedValue({ data: { count: 0, results: [] } });
  runsTab.runs = [...MIXED_RUNS, FULL_RUN];
  runsTab.group = null;
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

describe("runs of an experiment listed under Other experiments (as shipped)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  });

  it("keeps each recorded run in the runs view with a link to its experiment, and lists the experiment once under Other experiments", async () => {
    openTab();
    const group = await screen.findByRole("region", { name: HEADING });
    for (const { run_id: runId } of MIXED_RUNS) {
      const row = screen.getByTestId(`run-${runId}`);
      const link = await within(row).findByTestId("other-experiment-run-link");
      expect(link).toHaveTextContent("In Support prompts, mixed");
      const anchor = within(link).getByRole("link", {
        name: "Support prompts, mixed",
      });
      expect(anchor).toHaveAttribute("href", ORIGINAL_PAGE);
    }
    expect(within(group).getAllByTestId(`row-${MIXED}`)).toHaveLength(1);
    expect(within(group).queryByTestId(`row-${FULL}`)).toBeNull();
    // The runs read is not filtered: all three runs are in the runs view.
    const grid = screen.getByTestId("runs-grid");
    expect(within(grid).getAllByTestId(/^run-/)).toHaveLength(3);
  });

  it("adds nothing to a run of an experiment the runs view shows in full", async () => {
    openTab();
    await screen.findByRole("region", { name: HEADING });
    const row = screen.getByTestId(`run-${FULL_RUN.run_id}`);
    expect(within(row).queryByTestId("other-experiment-run-link")).toBeNull();
  });

  it("links the run the same way when runs are grouped by experiment", async () => {
    runsTab.group = "experiment";
    openTab();
    await screen.findByRole("region", { name: HEADING });
    const row = screen.getByTestId(`run-${MIXED_RUNS[0].run_id}`);
    const link = await within(row).findByTestId("other-experiment-run-link");
    const anchor = within(link).getByRole("link", {
      name: "Support prompts, mixed",
    });
    expect(anchor).toHaveAttribute("href", ORIGINAL_PAGE);
  });
});

describe("runs view rows with unreleased parts hidden or no such group", () => {
  it("adds no link where the tab lists no Other experiments (no provider)", async () => {
    const { default: ExperimentNameCell } = await import(
      "src/sections/develop-detail/ExperimentTab/ExperimentNameCell"
    );
    const context = {
      datasetId: DATASET,
      group: null,
      q: "",
      allRuns: MIXED_RUNS,
      visibleByExperiment: { [MIXED]: MIXED_RUNS },
      latestExperimentId: null,
      baselineId: null,
      numbers: null,
    };
    render(
      <MemoryRouter>
        <ExperimentNameCell
          node={{ group: false, data: MIXED_RUNS[0] }}
          data={MIXED_RUNS[0]}
          context={context}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("other-experiment-run-link")).toBeNull();
  });
});
