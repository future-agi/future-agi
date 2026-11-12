/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const mixedToolbar = vi.hoisted(() => vi.fn());
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function GridProbe({ rowData, columnDefs }, ref) {
      return (
        <div ref={ref} data-testid="summary-grid">
          {JSON.stringify({
            rows: rowData,
            columns: columnDefs.map((col) => col.field),
          })}
        </div>
      );
    }),
  };
});
vi.mock(
  "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection",
  () => ({ default: mixedToolbar }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentEvaluationChart",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryChart/SpiderChartExperiment",
  () => ({ default: () => null }),
);

/* eslint-enable react/prop-types */

import ClassicExperimentSummary from "../ClassicExperimentSummary";

const experimentId = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3e";
const baseUrl = `/model-hub/experiments/v2/${experimentId}/`;
const clients = [];
let winnerSaved;
let savedWeights;

beforeEach(() => {
  winnerSaved = false;
  savedWeights = null;
  vi.clearAllMocks();
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  mixedToolbar.mockImplementation(() => (
    <div data-testid="mixed-dsx-toolbar" />
  ));
  requests.get.mockImplementation(async (url) => {
    if (url === `${baseUrl}stats/`)
      return {
        data: {
          result: {
            metadata: { is_winner_chosen: winnerSaved },
            table_data: [
              {
                experiment_dataset_name: "Prompt A",
                quality: 0.9,
                ...(winnerSaved ? { rank: 1 } : {}),
              },
              {
                experiment_dataset_name: "Prompt B",
                quality: 0.5,
                ...(winnerSaved ? { rank: 2 } : {}),
              },
            ],
            column_config: [{ id: "quality", name: "quality" }],
          },
        },
      };
    if (url === `${baseUrl}comparisons/`)
      return {
        data: {
          result: {
            comparisons: savedWeights ? [{ weights: savedWeights }] : [],
          },
        },
      };
    if (url === baseUrl)
      return {
        data: {
          status: true,
          result: {
            id: experimentId,
            status: "Completed",
            runs: [{ target_type: "prompt" }, { target_type: "prompt" }],
            runs_total: 2,
            runs_has_more: false,
            runs_next: null,
          },
        },
      };
    throw new Error(`Unexpected GET in classic chooser test: ${url}`);
  });
  requests.post.mockImplementation(async (url) => {
    if (url !== `${baseUrl}compare-experiments/`)
      throw new Error(`Unexpected POST in classic chooser test: ${url}`);
    winnerSaved = true;
    return { data: { result: { experiment_name: "Classic experiment" } } };
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
});

function mountSummary() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiment/${experimentId}/summary`]}>
        <Routes>
          <Route
            path="/experiment/:experimentId/summary"
            element={<ClassicExperimentSummary />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

function expectNewToolbarAbsent() {
  expect(mixedToolbar).not.toHaveBeenCalled();
  expect(screen.queryByTestId("mixed-dsx-toolbar")).toBeNull();
  for (const name of [
    /Ask Falcon/i,
    /Review/i,
    /Candidate/i,
    /Baseline/i,
    /Workbench/i,
    /Download…/,
  ])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.queryByText(/Weights start from this dataset/)).toBeNull();
}

it.each([false, true])(
  "chooses and refreshes classic rankings without the NEW toolbar (saved weights: %s)",
  async (hasSavedWeights) => {
    if (hasSavedWeights)
      savedWeights = {
        response_time: 2,
        completion_tokens: 3,
        total_tokens: 4,
        scores: { quality: 5 },
      };
    const client = mountSummary();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const choose = await screen.findByRole("button", { name: "Choose winner" });
    expect(choose).toBeVisible();
    expect(choose).toBeEnabled();
    expect(screen.getByTestId("summary-grid")).toHaveTextContent(
      '"quality":0.9',
    );
    expect(new Set(requests.get.mock.calls.map(([url]) => url))).toEqual(
      new Set([`${baseUrl}stats/`, baseUrl]),
    );
    expectNewToolbarAbsent();

    fireEvent.click(choose);
    expect(await screen.findByText("Winner Settings")).toBeVisible();
    const sliders = await screen.findAllByRole("slider");
    expect(sliders).toHaveLength(4);
    expect(
      sliders.map((slider) => Number(slider.getAttribute("aria-valuenow"))),
    ).toEqual(hasSavedWeights ? [2, 3, 4, 5] : [1, 1, 1, 1]);
    expectNewToolbarAbsent();
    fireEvent.change(sliders[3], { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Save & Run" }));
    await waitFor(() =>
      expect(requests.post).toHaveBeenCalledWith(
        `${baseUrl}compare-experiments/`,
        {
          weights: {
            response_time: hasSavedWeights ? 2 : 1,
            completion_tokens: hasSavedWeights ? 3 : 1,
            total_tokens: hasSavedWeights ? 4 : 1,
            quality: 6,
          },
        },
      ),
    );
    expect(requests.post).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["experiment-summary", experimentId],
    });
    await waitFor(() =>
      expect(screen.getByTestId("summary-grid")).toHaveTextContent('"rank":1'),
    );
    await waitFor(() =>
      expect(screen.queryByText("Winner Settings")).toBeNull(),
    );
    expectNewToolbarAbsent();
    expect(new Set(requests.get.mock.calls.map(([url]) => url))).toEqual(
      new Set([`${baseUrl}stats/`, `${baseUrl}comparisons/`, baseUrl]),
    );
  },
);

it("cancels the legacy chooser without mutating rankings or mounting NEW controls", async () => {
  mountSummary();
  fireEvent.click(await screen.findByRole("button", { name: "Choose winner" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByText("Winner Settings")).toBeNull());
  expect(requests.post).not.toHaveBeenCalled();
  expectNewToolbarAbsent();
});
