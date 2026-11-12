/* eslint-disable react/prop-types */
// The Datasets list's Experiments count counts every experiment of a dataset
// (those that open in the original results view included). With unreleased
// parts shown, following that number reaches the dataset's Experiments tab
// with those experiments listed, not a tab that says "No experiments yet".
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
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
  return {
    ...actual,
    default: { get, post: vi.fn(), delete: vi.fn() },
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
// The runs view: the real LeaderboardShell with no runs on this dataset.
vi.mock("src/sections/develop-detail/ExperimentTab/ExperimentTab", async () => {
  const R = await import("react");
  const { LeaderboardShell } = await import(
    "src/sections/develop-detail/ExperimentTab/LeaderboardStates"
  );
  return {
    default: R.forwardRef(function RunsView() {
      return (
        <LeaderboardShell
          view={{
            query: { data: {}, isLoading: false, isError: false, refetch() {} },
            datasetId: "11111111-1111-4111-8111-111111111111",
            allRuns: [],
            runs: [],
            version: "all",
            q: "",
            activeFilters: [],
            setQ() {},
            setFilters() {},
            setVersion() {},
          }}
          onClearFilters={() => {}}
        />
      );
    }),
  };
});
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data }) => (
    <div data-testid="experiments-table">
      {data.map((row) => (
        <div key={row.id} data-testid={`row-${row.id}`}>
          {row.name}
        </div>
      ))}
    </div>
  ),
  DataTablePagination: () => null,
}));
/* eslint-enable react/prop-types */

import { ExperimentsCell } from "src/sections/develop/DatasetListCells";
import ExperimentTabSwitch from "../ExperimentTabSwitch";

const DATASET = "11111111-1111-4111-8111-111111111111";
const DETAIL_URL = endpoints.develop.getDatasetDetail(DATASET);
const HEADING = "Other experiments";

// Two experiments the production build made: counted on the Datasets list
// (number_of_experiments), marked opens_in_full_results false on the
// experiments list.
const OLDER = [
  {
    id: "00000000-0000-4000-8000-000000000001",
    name: "March prompts",
    status: "Completed",
    experiment_type: "llm",
    dataset: DATASET,
    opens_in_full_results: false,
  },
  {
    id: "00000000-0000-4000-8000-000000000002",
    name: "February prompts",
    status: "Completed",
    experiment_type: "llm",
    dataset: DATASET,
    opens_in_full_results: false,
  },
];

const ExperimentsTabAtUrl = () => {
  const { search } = useLocation();
  return new URLSearchParams(search).get("tab") === "experiments" ? (
    <ExperimentTabSwitch experimentSearch="" setRowSelected={() => {}} />
  ) : (
    <div>Data tab</div>
  );
};

const theme = createTheme({ palette: palette("light") });
let client;

beforeEach(() => {
  vi.clearAllMocks();
  http.list.mockReset();
  http.other.mockReset();
  unreleased.shown = true;
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  http.list.mockResolvedValue({
    data: { count: 2, next: null, previous: null, results: OLDER },
  });
  http.other.mockImplementation((url) =>
    Promise.resolve(
      url === DETAIL_URL
        ? { data: { result: { table: [{ row_id: "r" }], metadata: {} } } }
        : { data: { count: 0, results: [] } },
    ),
  );
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

describe("Datasets list Experiments count", () => {
  it("opens the Experiments tab with the experiments it counted listed", async () => {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <ThemeProvider theme={theme}>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={["/dashboard/develop"]}>
            <Routes>
              <Route
                path="/dashboard/develop"
                element={
                  <ExperimentsCell
                    row={{ id: DATASET, numberOfExperiments: 2 }}
                  />
                }
              />
              <Route
                path="/dashboard/develop/:dataset"
                element={<ExperimentsTabAtUrl />}
              />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </ThemeProvider>,
    );

    const count = screen.getByRole("link", { name: "2" });
    expect(count).toHaveAttribute(
      "href",
      `/dashboard/develop/${DATASET}?tab=experiments`,
    );
    fireEvent.click(count);

    const group = await screen.findByRole("region", { name: HEADING });
    for (const experiment of OLDER) {
      const row = within(group).getByTestId(`row-${experiment.id}`);
      expect(row).toHaveTextContent(experiment.name);
    }
    expect(screen.queryByText(/No experiments yet/)).toBeNull();
  });
});
