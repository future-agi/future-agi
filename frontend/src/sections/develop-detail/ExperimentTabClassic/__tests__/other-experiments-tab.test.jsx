/* eslint-disable react/prop-types */
// The dataset's Experiments tab lists every experiment the dataset has.
// As shipped (unreleased parts shown) the tab shows the runs view;
// experiments it cannot show in full (for example created before runs were
// recorded, or run with a prompt that has no saved version) are listed under
// it from the experiments list's own rows (opens_in_full_results false), with
// the list's table, Search by name, row actions and selection actions, and
// open at the page the list always opened. "No experiments yet" is said only when the dataset has
// neither. With unreleased parts hidden the tab is the experiments list, as
// before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

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

const http = vi.hoisted(() => ({
  list: vi.fn(),
  other: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const listUrl = actual.endpoints.develop.experiment.experimentListPaginated;
  const get = (url, ...rest) =>
    (url === listUrl ? http.list : http.other)(url, ...rest);
  return {
    ...actual,
    default: { get, post: http.post, delete: http.delete },
  };
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
// The runs view's first-run cards and doors have their own tests.
vi.mock(
  "src/sections/common/RunSources/SourceCards",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => <div data-testid="ways-to-run" />,
  }),
);
vi.mock(
  "src/sections/develop-detail/ExperimentTab/useOpenSource",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => vi.fn(),
  }),
);
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({
    kind: "rows",
    dataset: { kind: "rows", row_count: 3 },
  }),
}));

// The runs view is the real LeaderboardShell over the runs this test sets;
// its grid only names them. (Its hooks and grid have their own tests.)
const runsTab = vi.hoisted(() => ({ runs: [] }));
vi.mock("src/sections/develop-detail/ExperimentTab/ExperimentTab", async () => {
  const R = await import("react");
  const { LeaderboardShell } = await import(
    "src/sections/develop-detail/ExperimentTab/LeaderboardStates"
  );
  return {
    default: R.forwardRef(function RunsView() {
      const view = {
        query: { data: {}, isLoading: false, isError: false, refetch() {} },
        datasetId: "11111111-1111-4111-8111-111111111111",
        allRuns: runsTab.runs,
        runs: runsTab.runs,
        version: "all",
        q: "",
        activeFilters: [],
        setQ() {},
        setFilters() {},
        setVersion() {},
      };
      const names = runsTab.runs.map((run) => run.name).join(", ");
      return (
        <LeaderboardShell
          view={view}
          onClearFilters={() => {}}
          grid={<div data-testid="runs-grid">{names}</div>}
        />
      );
    }),
  };
});

// MUI DataGrid renders no rows in jsdom (no layout). A plain table with the
// same props keeps the rows, their cells (status, row actions), selection,
// row click and paging real.
vi.mock("src/components/data-table", () => {
  function TableRow({ row, columns, rowSelection, onSelect, onOpen }) {
    return (
      <div data-testid={`row-${row.id}`} onClick={() => onOpen?.(row)}>
        <input
          type="checkbox"
          aria-label={`Select ${row.name}`}
          checked={Boolean(rowSelection?.[row.id])}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onSelect(row, e.target.checked)}
        />
        {columns.map((c) => (
          <span key={c.id}>
            {c.cell
              ? c.cell({
                  getValue: () => row[c.accessorKey],
                  row: { original: row },
                })
              : row[c.accessorKey]}
          </span>
        ))}
      </div>
    );
  }
  return {
    DataTable: (props) => {
      const { data, isLoading, emptyMessage, rowSelection } = props;
      const onSelect = (row, on) => {
        const next = { ...(rowSelection ?? {}) };
        if (on) next[row.id] = true;
        else delete next[row.id];
        const chosen = data.filter((r) => next[r.id]);
        props.onRowSelectionChange?.(next, chosen);
      };
      if (!data.length) {
        return (
          <div data-testid="experiments-table">
            {!isLoading && <p>{emptyMessage}</p>}
          </div>
        );
      }
      return (
        <div data-testid="experiments-table">
          {data.map((row) => (
            <TableRow
              key={row.id}
              row={row}
              columns={props.columns}
              rowSelection={rowSelection}
              onSelect={onSelect}
              onOpen={props.onRowClick}
            />
          ))}
        </div>
      );
    },
    DataTablePagination: ({ page, pageSize, total, onPageChange }) => {
      const from = total ? page * pageSize + 1 : 0;
      const to = Math.min((page + 1) * pageSize, total);
      return (
        <div data-testid="pagination">
          <span>{`${from}–${to} of ${total}`}</span>
          <button
            type="button"
            disabled={to >= total}
            onClick={() => onPageChange(page + 1)}
          >
            Next page
          </button>
        </div>
      );
    },
  };
});
/* eslint-enable react/prop-types */

import ExperimentTabSwitch from "../ExperimentTabSwitch";

const DATASET = "11111111-1111-4111-8111-111111111111";
const LIST_URL = endpoints.develop.experiment.experimentListPaginated;
const DETAIL_URL = endpoints.develop.getDatasetDetail(DATASET);
const TAB_URL = `/dashboard/develop/${DATASET}?tab=experiments`;

const HEADING = "Other experiments";
const LINE =
  "These experiments open in the original results view, where all of their variants show. This includes experiments created before this update.";
const NO_MATCH = "No experiments match your search";
const NO_EXPERIMENTS =
  "No experiments yet. Pick one way to run; add another later to see which wins.";
const RUN_LINE = "Pick one way to run; add another later to see which wins.";
const COMPARE_REASON =
  "Compare works on runs; these experiments open in the original results view instead.";
const FIRST_RUN = "Ready to run your first experiment";

// Rows of experiments/v2/list/ as the list serializer writes them.
const experiment = (n, { full, status = "Completed", ...rest } = {}) => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
  name: `Experiment ${n}`,
  description: "",
  status,
  experiment_type: "llm",
  eval_templates_count: 1,
  created_at: "2026-03-02T10:00:00Z",
  models_count: 2,
  agents_count: 0,
  dataset: DATASET,
  ...(full === undefined ? {} : { opens_in_full_results: full }),
  ...rest,
});
const listPage = (results, next = null) => ({
  data: { count: results.length, next, previous: null, results },
});
const RUN = {
  run_id: "r1",
  experiment_id: "e-new",
  name: "Support prompt run",
  label: "#1A",
};
const DATASET_PAGE = {
  data: {
    result: { table: [{ row_id: "row-1" }], metadata: { total_rows: 1 } },
  },
};

const LocationProbe = () => {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{`${pathname}${search}`}</output>;
};

const theme = createTheme({ palette: palette("light") });
let client;
const setRowSelected = vi.fn();
function openTab() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tab = (
    <ExperimentTabSwitch experimentSearch="" setRowSelected={setRowSelected} />
  );
  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[TAB_URL]}>
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={tab} />
            <Route
              path="/dashboard/develop/experiment/:experimentId/*"
              element={<LocationProbe />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const group = () => screen.getByRole("region", { name: HEADING });
const inGroup = (testId) => within(group()).queryByTestId(testId);

beforeEach(() => {
  vi.clearAllMocks();
  // mockReset also drops answers a previous case queued but never used.
  for (const fn of Object.values(http)) fn.mockReset();
  runsTab.runs = [];
  http.list.mockResolvedValue(listPage([]));
  // Every other read (the dataset's first page, its access and so on).
  http.other.mockImplementation(async (url) =>
    url === DETAIL_URL ? DATASET_PAGE : { data: { count: 0, results: [] } },
  );
  http.post.mockResolvedValue({ data: {} });
  http.delete.mockResolvedValue({ data: {} });
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

describe("Experiments tab with unreleased parts shown (as shipped)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  });

  it("lists a dataset's other experiments under the runs view when it has no runs", async () => {
    const others = [
      experiment(1, { full: false }),
      experiment(2, { full: false }),
    ];
    http.list.mockResolvedValue(listPage(others));
    openTab();

    const heading = await screen.findByRole("heading", {
      level: 3,
      name: HEADING,
    });
    expect(heading).toBeVisible();
    expect(screen.getByText(LINE)).toBeVisible();
    for (const row of others) {
      expect(inGroup(`row-${row.id}`)).toHaveTextContent(row.name);
    }
    // The runs view still offers the ways to run, without "No experiments yet".
    expect(screen.getByTestId("ways-to-run")).toBeVisible();
    const line = screen.getByTestId("experiments-empty-line");
    expect(line).toHaveTextContent(RUN_LINE);
    expect(screen.queryByText(/No experiments yet/)).toBeNull();
    // Read as the list reads: this dataset, in pages.
    const params = { dataset_id: DATASET, page: 1, limit: 200 };
    expect(http.list).toHaveBeenCalledWith(
      LIST_URL,
      expect.objectContaining({ params }),
    );
  });

  it("shows only the runs view when the runs view can show every listed experiment", async () => {
    runsTab.runs = [RUN];
    const full = experiment(3, { full: true, id: "e-new" });
    http.list.mockResolvedValue(listPage([full]));
    openTab();

    const grid = await screen.findByTestId("runs-grid");
    expect(grid).toHaveTextContent("Support prompt run");
    await waitFor(() => expect(http.list).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByRole("region", { name: HEADING })).toBeNull();
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(screen.queryByText(/No experiments yet/)).toBeNull();
  });

  it("lists both: the runs view, and under it only the experiments it cannot show in full", async () => {
    runsTab.runs = [RUN];
    const before = experiment(4, { full: false, name: "March prompts" });
    const withRuns = experiment(5, { full: true, name: "April prompts" });
    // A row the list does not mark is never guessed into the group.
    const unmarked = experiment(6, { name: "Unmarked row" });
    http.list.mockResolvedValue(listPage([withRuns, before, unmarked]));
    openTab();

    expect(await screen.findByTestId("runs-grid")).toBeVisible();
    expect(await screen.findByTestId(`row-${before.id}`)).toBeVisible();
    expect(within(group()).getByText("March prompts")).toBeVisible();
    expect(inGroup(`row-${withRuns.id}`)).toBeNull();
    expect(inGroup(`row-${unmarked.id}`)).toBeNull();
    expect(screen.queryByText(/No experiments yet/)).toBeNull();
  });

  it("says No experiments yet only when the dataset has neither", async () => {
    http.list.mockResolvedValue(listPage([]));
    openTab();

    expect(await screen.findByText(NO_EXPERIMENTS)).toBeVisible();
    expect(screen.queryByText(HEADING)).toBeNull();
  });

  it("shows skeletons, and no No experiments yet, while the other experiments load", async () => {
    http.list.mockImplementation(() => new Promise(() => {}));
    openTab();

    const loading = await screen.findByTestId("other-experiments-loading");
    expect(loading).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText(/No experiments yet/)).toBeNull();
    expect(screen.queryByTestId("experiments-empty")).toBeNull();
  });

  it("says the other experiments could not load, never No experiments yet, and Retry lists them", async () => {
    const other = experiment(7, { full: false });
    const unavailable = Object.assign(new Error("Request failed"), {
      response: { status: 503 },
    });
    http.list
      .mockRejectedValueOnce(unavailable)
      .mockResolvedValueOnce(listPage([other]));
    openTab();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load other experiments.");
    expect(alert).toHaveTextContent("503");
    expect(screen.queryByText(/No experiments yet/)).toBeNull();

    fireEvent.click(within(alert).getByRole("button", { name: /Retry/ }));
    expect(await screen.findByTestId(`row-${other.id}`)).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("opens one of the other experiments at the page the list always opened", async () => {
    const other = experiment(8, { full: false, name: "March prompts" });
    http.list.mockResolvedValue(listPage([other]));
    openTab();

    fireEvent.click(await screen.findByTestId(`row-${other.id}`));
    const payload = encodeURIComponent(
      JSON.stringify({ id: other.id, name: "March prompts" }),
    );
    const query = `datasetId=${DATASET}&payload=${payload}`;
    const location = await screen.findByTestId("location");
    expect(location).toHaveTextContent(
      `/dashboard/develop/experiment/${other.id}/data?${query}`,
    );
  });

  it("reads every page of the list and shows the group 20 rows a page", async () => {
    const others = Array.from({ length: 25 }, (_, i) =>
      experiment(100 + i, { full: false }),
    );
    const newer = experiment(200, { full: true });
    const firstPage = listPage([newer, ...others.slice(0, 15)], "page=2");
    const secondPage = listPage(others.slice(15));
    http.list.mockImplementation(async (url, { params }) =>
      params.page === 1 ? firstPage : secondPage,
    );
    openTab();

    await screen.findByTestId(`row-${others[0].id}`);
    const pages = http.list.mock.calls.map(([, { params }]) => params.page);
    expect(pages).toEqual([1, 2]);
    const pager = within(group()).getByTestId("pagination");
    expect(pager).toHaveTextContent("1–20 of 25");
    expect(inGroup(`row-${others[20].id}`)).toBeNull();

    fireEvent.click(within(pager).getByRole("button", { name: "Next page" }));
    const later = await within(group()).findByTestId(`row-${others[20].id}`);
    expect(later).toBeVisible();
    const pagerAfter = within(group()).getByTestId("pagination");
    expect(pagerAfter).toHaveTextContent("21–25 of 25");
    expect(inGroup(`row-${others[0].id}`)).toBeNull();
  });

  it("searches the other experiments by name, as the Experiments list did, and clearing shows them all", async () => {
    const others = [
      experiment(31, { full: false, name: "March support prompts" }),
      experiment(32, { full: false, name: "April billing prompts" }),
      experiment(33, { full: false, name: "Support tone check" }),
    ];
    http.list.mockResolvedValue(listPage(others));
    openTab();

    await screen.findByTestId(`row-${others[0].id}`);
    const reads = http.list.mock.calls.length;
    const search = within(group()).getByPlaceholderText("Search");
    fireEvent.change(search, { target: { value: "SUPPORT" } });
    expect(inGroup(`row-${others[0].id}`)).toBeVisible();
    expect(inGroup(`row-${others[2].id}`)).toBeVisible();
    expect(inGroup(`row-${others[1].id}`)).toBeNull();
    const pager = within(group()).getByTestId("pagination");
    expect(pager).toHaveTextContent("1–2 of 2");
    // It filters the rows already read; it reads nothing more.
    expect(http.list.mock.calls.length).toBe(reads);

    fireEvent.change(search, { target: { value: "" } });
    for (const row of others) expect(inGroup(`row-${row.id}`)).toBeVisible();
    expect(within(group()).getByTestId("pagination")).toHaveTextContent(
      "1–3 of 3",
    );
  });

  it("matches every word of the search, in any order, as the Experiments list did", async () => {
    const others = [
      experiment(41, { full: false, name: "Support prompts, March" }),
      experiment(42, { full: false, name: "March billing prompts" }),
    ];
    http.list.mockResolvedValue(listPage(others));
    openTab();

    await screen.findByTestId(`row-${others[0].id}`);
    const search = within(group()).getByPlaceholderText("Search");
    fireEvent.change(search, { target: { value: "march support" } });
    expect(inGroup(`row-${others[0].id}`)).toBeVisible();
    expect(inGroup(`row-${others[1].id}`)).toBeNull();

    // A word that is in no name finds nothing, whatever the other words.
    fireEvent.change(search, { target: { value: "march nowhere" } });
    expect(inGroup(`row-${others[0].id}`)).toBeNull();
    expect(inGroup(`row-${others[1].id}`)).toBeNull();
    expect(within(group()).getByText(NO_MATCH)).toBeVisible();
  });

  it("goes back to the first page when the search changes, and says when nothing matches", async () => {
    const others = Array.from({ length: 25 }, (_, i) =>
      experiment(300 + i, { full: false }),
    );
    http.list.mockResolvedValue(listPage(others));
    openTab();

    await screen.findByTestId(`row-${others[0].id}`);
    const pager = within(group()).getByTestId("pagination");
    fireEvent.click(within(pager).getByRole("button", { name: "Next page" }));
    await within(group()).findByTestId(`row-${others[20].id}`);

    const search = within(group()).getByPlaceholderText("Search");
    // Experiment 300 to Experiment 309.
    fireEvent.change(search, { target: { value: "Experiment 30" } });
    expect(inGroup(`row-${others[0].id}`)).toBeVisible();
    expect(inGroup(`row-${others[9].id}`)).toBeVisible();
    expect(inGroup(`row-${others[10].id}`)).toBeNull();
    expect(within(group()).getByTestId("pagination")).toHaveTextContent(
      "1–10 of 10",
    );

    fireEvent.change(search, { target: { value: "no such experiment" } });
    expect(within(group()).getByText(NO_MATCH)).toBeVisible();
    expect(within(group()).getByText(HEADING)).toBeVisible();
    expect(screen.queryByText(NO_EXPERIMENTS)).toBeNull();
    expect(inGroup(`row-${others[0].id}`)).toBeNull();
  });

  it("keeps the list's row actions and selection actions; Compare is disabled with its reason", async () => {
    const other = experiment(9, { full: false, name: "March prompts" });
    http.list.mockResolvedValue(listPage([other]));
    openTab();

    const row = await screen.findByTestId(`row-${other.id}`);
    // Row action of a finished experiment, as on the list.
    const rerun = within(row).getByRole("button", { name: "Rerun Experiment" });
    expect(rerun).toBeEnabled();

    fireEvent.click(within(row).getByRole("checkbox"));
    expect(within(group()).getByText("1 selected")).toBeVisible();
    for (const name of ["Re-Run", "Delete", "Cancel"]) {
      expect(within(group()).getByRole("button", { name })).toBeEnabled();
    }
    const compare = within(group()).getByRole("button", { name: "Compare" });
    expect(compare).toBeDisabled();
    expect(compare).toHaveAccessibleDescription(COMPARE_REASON);
    // The runs view's selection is runs only: this selection stays here.
    const ours = expect.objectContaining({ id: other.id });
    expect(setRowSelected).not.toHaveBeenCalledWith(
      expect.arrayContaining([ours]),
    );

    const cancel = within(group()).getByRole("button", { name: "Cancel" });
    fireEvent.click(cancel);
    expect(within(group()).queryByText("1 selected")).toBeNull();
  });

  it("deletes a selected other experiment through the list's own delete", async () => {
    const other = experiment(10, { full: false, name: "March prompts" });
    http.list
      .mockResolvedValueOnce(listPage([other]))
      .mockResolvedValue(listPage([]));
    openTab();

    const row = await screen.findByTestId(`row-${other.id}`);
    fireEvent.click(within(row).getByRole("checkbox"));
    fireEvent.click(within(group()).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    const body = { data: { experiment_ids: [other.id] } };
    await waitFor(() =>
      expect(http.delete).toHaveBeenCalledWith(
        endpoints.develop.experiment.delete,
        body,
      ),
    );
    await waitFor(() =>
      expect(screen.queryByTestId(`row-${other.id}`)).toBeNull(),
    );
  });
});

describe("Experiments tab with unreleased parts hidden", () => {
  beforeEach(() => {
    unreleased.shown = false;
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
    });
  });

  it("is the experiments list, with every experiment in one list as before", async () => {
    const before = experiment(11, { full: false });
    const withRuns = experiment(12, { full: true });
    http.list.mockResolvedValue(listPage([withRuns, before]));
    openTab();

    expect(await screen.findByTestId(`row-${before.id}`)).toBeVisible();
    expect(screen.getByTestId(`row-${withRuns.id}`)).toBeVisible();
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(screen.queryByTestId("runs-grid")).toBeNull();
    const params = { dataset_id: DATASET, page: 1, limit: 20 };
    expect(http.list).toHaveBeenCalledWith(
      LIST_URL,
      expect.objectContaining({ params: expect.objectContaining(params) }),
    );
  });

  it("keeps the first-run state when the dataset has no experiment", async () => {
    http.list.mockResolvedValue(listPage([]));
    openTab();

    expect(await screen.findByText(FIRST_RUN)).toBeVisible();
    expect(screen.queryByText(HEADING)).toBeNull();
  });
});
