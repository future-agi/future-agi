// First run on a new database: when the classic Experiments list read fails,
// the tab says so and offers Retry. It never shows the first-run empty state
// (or a "no match" line) for a read that failed; the first-run state follows
// only a successful read that came back empty.
//
// The first-run state carries the New experiment action (FR-EXPERIMENTS-03),
// which reads this dataset on its own: its access view, its first page and
// the dataset list. Only the list read reaches `http.get`; every other GET
// goes to `http.actionGet`. A list answer queued with mock...Once therefore
// reaches the list read it is meant for, never one of the action's reads.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import ExperimentTab from "../ExperimentTab";

const http = vi.hoisted(() => ({
  // The Experiments list read (experiments/v2/list/) only.
  get: vi.fn(),
  // Every other GET: the first-run action's own reads.
  actionGet: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const listUrl = actual.endpoints.develop.experiment.experimentListPaginated;
  const get = (url, ...rest) =>
    (url === listUrl ? http.get : http.actionGet)(url, ...rest);
  return {
    ...actual,
    default: { get, post: http.post, delete: http.delete },
  };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
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
/* eslint-disable react/prop-types */
// MUI DataGrid renders no rows in jsdom (no layout); a plain list with the
// same props keeps the rows and the table's own empty message visible.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, isLoading, emptyMessage }) => (
    <div data-testid="classic-table">
      {data.length
        ? data.map((row) => (
            <div key={row.id} data-testid={`row-${row.id}`}>
              {row.name}
            </div>
          ))
        : !isLoading && <p>{emptyMessage}</p>}
    </div>
  ),
  DataTablePagination: () => null,
}));
/* eslint-enable react/prop-types */

const DATASET = "11111111-1111-4111-8111-111111111111";
const DONE = "22222222-2222-4222-8222-222222222222";

const FIRST_RUN = "Ready to run your first experiment";
const LOAD_ERROR = "Couldn't load experiments.";
const NO_MATCH = "No experiments match your search";
const NEW_EXPERIMENT = "New experiment";
// This dataset's first page, as the first-run action reads it.
const DETAIL_URL = endpoints.develop.getDatasetDetail(DATASET);

const listPage = {
  data: {
    count: 1,
    results: [
      { id: DONE, name: "done-exp", status: "Completed", dataset: DATASET },
    ],
  },
};
const emptyPage = { data: { count: 0, results: [] } };
// One row on this dataset's first page: the first-run action can start an
// experiment.
const datasetPage = {
  data: {
    result: { table: [{ row_id: "row-1" }], metadata: { total_rows: 1 } },
  },
};
const readError = (status) =>
  Object.assign(new Error("Request failed"), { response: { status } });
const actionReadUrls = () => http.actionGet.mock.calls.map(([url]) => url);

let client;

const renderTab = (props = {}) =>
  renderWithRouter(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path="/dashboard/develop/:dataset"
          element={
            <ExperimentTab
              experimentSearch=""
              setRowSelected={vi.fn()}
              {...props}
            />
          }
        />
      </Routes>
    </QueryClientProvider>,
    { route: `/dashboard/develop/${DATASET}` },
  );

beforeEach(() => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  http.get.mockReset();
  http.actionGet.mockReset();
  http.post.mockReset();
  http.delete.mockReset();
  http.get.mockResolvedValue(emptyPage);
  http.actionGet.mockImplementation((url) =>
    Promise.resolve(url === DETAIL_URL ? datasetPage : emptyPage),
  );
  http.post.mockResolvedValue({ data: {} });
  http.delete.mockResolvedValue({ data: {} });
});

afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
});

describe("classic Experiments list read states", () => {
  it("shows a load error with Retry, not the first-run state, when the list read fails", async () => {
    http.get
      .mockRejectedValueOnce(readError(503))
      .mockResolvedValueOnce(listPage);
    renderTab();

    expect(await screen.findByRole("alert")).toHaveTextContent(LOAD_ERROR);
    expect(screen.queryByText(FIRST_RUN)).toBeNull();
    expect(screen.queryByTestId("classic-experiments-list")).toBeNull();
    const failedRequest = http.get.mock.calls[0];

    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));

    expect(await screen.findByTestId(`row-${DONE}`)).toBeInTheDocument();
    expect(http.get).toHaveBeenCalledTimes(2);
    expect(http.get.mock.calls[1]).toEqual(failedRequest);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(FIRST_RUN)).toBeNull();
  });

  it("shows the load error, not a no-match line, when a searched read fails", async () => {
    http.get.mockRejectedValueOnce(readError(500));
    renderTab({ experimentSearch: "baseline" });

    expect(await screen.findByRole("alert")).toHaveTextContent(LOAD_ERROR);
    expect(http.get.mock.calls[0][1].params.search).toBe("baseline");
    expect(screen.queryByText(NO_MATCH)).toBeNull();
    expect(screen.queryByText(FIRST_RUN)).toBeNull();
  });

  it("shows the load error when a refresh of an empty list fails", async () => {
    http.get
      .mockResolvedValueOnce(emptyPage)
      .mockRejectedValueOnce(readError(503));
    renderTab();
    expect(await screen.findByText(FIRST_RUN)).toBeInTheDocument();

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiment-list"] });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(LOAD_ERROR);
    expect(http.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(FIRST_RUN)).toBeNull();
  });

  it("keeps the first-run state for a successful empty read", async () => {
    http.get.mockResolvedValueOnce(emptyPage);
    renderTab();

    expect(await screen.findByText(FIRST_RUN)).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });

  it("shows the load error and Retry, not the first-run action, when a refresh fails after the action has read this dataset", async () => {
    http.get
      .mockResolvedValueOnce(emptyPage)
      .mockRejectedValueOnce(readError(503))
      .mockResolvedValueOnce(emptyPage);
    renderTab();
    expect(await screen.findByText(FIRST_RUN)).toBeVisible();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: NEW_EXPERIMENT }),
      ).toBeEnabled(),
    );
    expect(actionReadUrls()).toContain(DETAIL_URL);

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiment-list"] });
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(LOAD_ERROR);
    expect(alert).toHaveTextContent("503");
    expect(screen.getByRole("button", { name: /Retry/ })).toBeVisible();
    expect(screen.queryByText(FIRST_RUN)).toBeNull();
    expect(screen.queryByRole("button", { name: NEW_EXPERIMENT })).toBeNull();
    expect(http.get).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));

    expect(await screen.findByText(FIRST_RUN)).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: NEW_EXPERIMENT })).toBeVisible();
    expect(http.get).toHaveBeenCalledTimes(3);
  });

  it("shows the first-run New experiment action after a successful empty read", async () => {
    http.get.mockResolvedValueOnce(emptyPage);
    renderTab();

    expect(await screen.findByText(FIRST_RUN)).toBeVisible();
    expect(screen.getByRole("button", { name: NEW_EXPERIMENT })).toBeVisible();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: NEW_EXPERIMENT }),
      ).toBeEnabled(),
    );
    expect(actionReadUrls()).toContain(DETAIL_URL);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("classic-experiments-list")).toBeNull();
    expect(http.get).toHaveBeenCalledTimes(1);
  });
});
