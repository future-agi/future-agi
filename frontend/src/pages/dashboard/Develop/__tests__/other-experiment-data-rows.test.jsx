/* eslint-disable react/prop-types */
// An experiment made on the production build opens, as shipped (unreleased
// parts shown), on the original Data page, and that page's real Data view
// reads the experiment's rows from GET experiments/v2/<id>/rows/: its column
// layout (column_config_only), then the first page of rows the grid asks for
// (current_page_index 0), which reaches the grid. Only the header, the tabs,
// the grid canvas and the drawers are stand-ins; the experiment page switch,
// the original page and its Data view are the real ones.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: requests,
}));
// The grid canvas, drawers and audio cache, as the Data view's own tests
// stand them in (classic-eval-group-nohide.test.jsx). The canvas asks the
// Data view's row source for its first page once, as the grid does when it
// mounts, and keeps what it is given.
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
const grid = vi.hoisted(() => ({ pages: [] }));
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useEffect } = await import("react");
  return {
    AgGridReact: forwardRef(function GridCanvas(props, _ref) {
      const { serverSideDatasource } = props;
      useEffect(() => {
        serverSideDatasource?.getRows({
          request: { startRow: 0, endRow: 30 },
          success: (page) => grid.pages.push(page),
          fail: () => grid.pages.push("failed"),
        });
      }, [serverSideDatasource]);
      return null;
    }),
  };
});
vi.mock(
  "src/sections/experiment-detail-classic/ExperimentData/ExperimentDetailDrawer",
  () => ({ default: () => null }),
);
vi.mock("src/components/PdfPreviewDrawer", () => ({ default: () => null }));
vi.mock("src/hooks/use-wavesurfer-cache", () => ({
  default: () => ({ clearWaveSurferCache: () => {} }),
}));
vi.mock("src/sections/common/EvaluationDrawer/EvaluationDrawer", () => ({
  default: () => null,
}));
// The page header and tabs have their own tests.
vi.mock("src/sections/experiment-detail-classic/ExperimentRow", () => ({
  default: () => <div data-testid="experiment-header" />,
}));
vi.mock("src/sections/experiment-detail-classic/ExperimentBar", () => ({
  default: () => <div data-testid="experiment-tabs" />,
}));
// The new page is not under test: it only marks that it mounted.
vi.mock("src/sections/experiment-detail/ExperimentWrapperView", () => ({
  default: () => <div data-testid="new-experiment-page" />,
}));

import ExperimentWrapper from "src/pages/dashboard/Develop/ExperimentWrapper";

const ID = "0b5b7b9e-4444-4c8f-9a8e-0e2f8d1d2c3e";
const DETAIL_URL = `/model-hub/experiments/v2/${ID}/`;
const ROWS_URL = `/model-hub/experiments/v2/${ID}/rows/`;

// Made by the production create: a snapshot dataset, no runs recorded.
const PRODUCTION_EXPERIMENT = {
  data: {
    status: true,
    result: {
      id: ID,
      dataset_id: "11111111-1111-4111-8111-111111111111",
      name: "Support prompts, March",
      status: "Completed",
      snapshot_dataset_id: "22222222-2222-4222-8222-222222222222",
      runs: [],
      runs_total: 0,
      runs_has_more: false,
      runs_next: null,
      results_availability: {
        reason: "native_identity_not_recorded",
        classic_url: `/dashboard/develop/experiment/${ID}/data`,
      },
      opens_in_full_results: false,
    },
  },
};
// The first page of rows: one row of one page.
const ROW = { row_id: "row-1" };
const ROW_PAGE = {
  data: {
    status: true,
    result: {
      status: "Completed",
      column_config: [],
      table: [ROW],
      metadata: { total_rows: 1, total_pages: 1 },
    },
  },
};
// What the rows endpoint answers for the column layout.
const ROWS = {
  data: {
    status: true,
    result: { status: "Completed", column_config: [], table: [] },
  },
};

class RenderError extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return <pre data-testid="render-error">{String(this.state.error)}</pre>;
  }
}

let client;
function openData() {
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 5000 },
      mutations: { retry: false },
    },
  });
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <QueryClientProvider client={client}>
        <HelmetProvider>
          <RenderError>
            <MemoryRouter
              initialEntries={[`/dashboard/develop/experiment/${ID}/data`]}
            >
              <Routes>
                <Route
                  path="/dashboard/develop/experiment/:experimentId/*"
                  element={<ExperimentWrapper />}
                />
              </Routes>
            </MemoryRouter>
          </RenderError>
        </HelmetProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const readsSoFar = () =>
  requests.get.mock.calls.map(([url]) => String(url)).join(", ") || "none";
function whatIsShown() {
  const crashed = screen.queryByTestId("render-error");
  if (crashed) return `a render error: ${crashed.textContent}`;
  if (screen.queryByTestId("new-experiment-page")) return "the new page";
  if (screen.queryByTestId("experiment-page-loading")) return "the skeleton";
  if (screen.queryByTestId("classic-experiment-page")) return "the Data page";
  return "nothing";
}

beforeEach(() => {
  grid.pages = [];
  requests.get.mockReset();
  requests.post.mockReset();
  unreleased.shown = true;
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  requests.get.mockImplementation(async (url, config) => {
    if (url === DETAIL_URL) return PRODUCTION_EXPERIMENT;
    const page = config?.params?.current_page_index;
    if (url === ROWS_URL && page !== undefined) return ROW_PAGE;
    // The column layout, and any other read the Data view makes on load,
    // get the answer the Data view's own tests give.
    return ROWS;
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

it("opens a production experiment on the original Data page, which reads its column layout and its first page of rows into the grid", async () => {
  openData();
  try {
    await waitFor(
      () =>
        expect(requests.get).toHaveBeenCalledWith(
          ROWS_URL,
          expect.objectContaining({
            params: expect.objectContaining({ column_config_only: true }),
          }),
        ),
      { timeout: 20000 },
    );
    await waitFor(() => expect(grid.pages.length).toBeGreaterThan(0), {
      timeout: 10000,
    });
  } catch {
    throw new Error(
      `Rows not read; on screen: ${whatIsShown()}; reads: ${readsSoFar()}; ` +
        `pages given to the grid: ${JSON.stringify(grid.pages)}`,
    );
  }
  expect(requests.get).toHaveBeenCalledWith(
    ROWS_URL,
    expect.objectContaining({
      params: expect.objectContaining({ current_page_index: 0, page_size: 30 }),
    }),
  );
  expect(grid.pages[0]).toEqual({ rowData: [ROW], rowCount: 1 });
  expect(screen.getByTestId("classic-experiment-page")).toBeVisible();
  expect(screen.getByTestId("experiment-header")).toBeVisible();
  expect(screen.queryByTestId("new-experiment-page")).toBeNull();
  expect(requests.post).not.toHaveBeenCalled();
}, 30000);
