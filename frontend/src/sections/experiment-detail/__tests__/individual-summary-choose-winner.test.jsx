/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { launchRedirect } from "src/launch/launchRoutes";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
// Only layout-heavy/unrelated boundaries are replaced. The routed wrapper,
// context, header, summary, legacy toolbar, drawer, sliders and queries are real.
vi.mock("../IndividualDevelopExperimentSelect", () => ({
  default: () => null,
}));
vi.mock("../ExperimentSummary/ExperimentEvaluationChart", () => ({
  default: () => null,
}));
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function GridProbe({ rowData, columnDefs }, ref) {
      return (
        <table ref={ref} aria-label="Summary rankings">
          <thead>
            <tr>
              {columnDefs.map((col) => (
                <th key={col.field}>{col.headerName}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowData?.map((row) => (
              <tr key={row.dataset_id}>
                {columnDefs.map((col) => (
                  <td key={col.field}>{row[col.field] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }),
  };
});

import IndividualExperimentWrapperView from "../IndividualExperimentWrapperView";
import IndividualExperimentSummaryView from "../ExperimentSummary/IndividualExperimentSummaryView";

const parentId = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3e";
const individualId = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a91";
const pathname = `/dashboard/develop/individual-experiment/${individualId}/summary`;
const metadataUrl = `/model-hub/develops/${individualId}/get-experiment-dataset-table/`;
const parentUrl = `/model-hub/experiments/v2/${parentId}/`;
const clients = [];
let metadataKey;
let savedWeights;
let winnerSaved;

beforeEach(() => {
  vi.clearAllMocks();
  metadataKey = "experiment_id";
  savedWeights = null;
  winnerSaved = false;
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  requests.get.mockImplementation(async (url) => {
    if (url === metadataUrl)
      return { data: { result: { metadata: { [metadataKey]: parentId } } } };
    if (url === `${parentUrl}stats/`)
      return {
        data: {
          result: {
            metadata: { is_winner_chosen: winnerSaved },
            table_data: [
              {
                dataset_id: individualId,
                experiment_dataset_name: "Prompt A",
                quality: 90,
                ...(winnerSaved ? { rank: 1 } : {}),
              },
              {
                dataset_id: "other-variant",
                experiment_dataset_name: "Prompt B",
                quality: 50,
                ...(winnerSaved ? { rank: 2 } : {}),
              },
            ],
            column_config: [{ id: "quality", name: "quality" }],
          },
        },
      };
    if (url === `${parentUrl}comparisons/`)
      return {
        data: {
          result: {
            comparisons: savedWeights ? [{ weights: savedWeights }] : [],
          },
        },
      };
    if (url === parentUrl)
      return {
        data: {
          result: {
            id: parentId,
            runs: [{ target_type: "prompt" }, { target_type: "prompt" }],
          },
        },
      };
    throw new Error(`Unexpected individual Summary GET: ${url}`);
  });
  requests.post.mockImplementation(async (url) => {
    if (url !== `${parentUrl}compare-experiments/`)
      throw new Error(`Unexpected individual Summary POST: ${url}`);
    winnerSaved = true;
    return { data: { result: { experiment_name: "Parent experiment" } } };
  });
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
});

function mountSummary() {
  expect(launchRedirect({ pathname })).toBeNull();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[pathname]}>
        <Routes>
          <Route
            path="/dashboard/develop/individual-experiment/:individualExperimentId"
            element={<IndividualExperimentWrapperView />}
          >
            <Route
              path="summary"
              element={<IndividualExperimentSummaryView />}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

function expectLegacyOnly() {
  for (const name of [/Ask Falcon/i, /Review/i, /Baseline/i, /Workbench/i])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.queryByText(/Weights start from this dataset/)).toBeNull();
}

// v1.56.1: ExperimentSummaryTable.jsx:77-83 and
// ExperimentBarSummaryRightSection.jsx:78-88 open the context-owned chooser;
// ChooseWinnerDrawer.jsx:25-35,74-89,216-221 loads weights, posts them and
// invalidates the experiment's stats. The individual URL has no experimentId.
it.each([
  { entry: "header", index: 0, key: "experiment_id", saved: false },
  { entry: "table toolbar", index: 1, key: "experiment_id", saved: true },
  { entry: "header", index: 0, key: "experimentId", saved: true },
  { entry: "table toolbar", index: 1, key: "experimentId", saved: false },
])(
  "saves and refreshes parent rankings from the individual Summary $entry ($key, saved=$saved)",
  async ({ index, key, saved }) => {
    metadataKey = key;
    if (saved)
      savedWeights = {
        response_time: 2,
        completion_tokens: 3,
        total_tokens: 4,
        scores: { quality: 5 },
      };
    const client = mountSummary();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const grid = await screen.findByRole("table", { name: "Summary rankings" });
    expect(within(grid).getByText("Prompt A")).toBeVisible();
    expect(
      within(grid).queryByRole("columnheader", { name: "Ranking" }),
    ).toBeNull();
    const buttons = screen.getAllByRole("button", { name: "Choose winner" });
    expect(buttons).toHaveLength(2);
    buttons.forEach((button) => expect(button).toBeEnabled());
    expect(requests.get.mock.calls.map(([url]) => url)).toEqual([
      metadataUrl,
      `${parentUrl}stats/`,
    ]);
    expect(requests.get).toHaveBeenCalledWith(metadataUrl, {
      params: { page_size: 1, current_page_index: 0 },
    });
    expectLegacyOnly();

    fireEvent.click(buttons[index]);
    expect(await screen.findByText("Winner Settings")).toBeVisible();
    expect(screen.getAllByText("Winner Settings")).toHaveLength(1);
    const sliders = await screen.findAllByRole("slider");
    expect(sliders).toHaveLength(4);
    expect(
      sliders.map((slider) => Number(slider.getAttribute("aria-valuenow"))),
    ).toEqual(saved ? [2, 3, 4, 5] : [1, 1, 1, 1]);
    expectLegacyOnly();
    fireEvent.change(sliders[3], { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Save & Run" }));
    await waitFor(() =>
      expect(requests.post).toHaveBeenCalledWith(
        `${parentUrl}compare-experiments/`,
        {
          weights: {
            response_time: saved ? 2 : 1,
            completion_tokens: saved ? 3 : 1,
            total_tokens: saved ? 4 : 1,
            quality: 6,
          },
        },
      ),
    );
    expect(requests.post).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["experiment-summary", parentId],
    });
    await waitFor(() =>
      expect(screen.queryByText("Winner Settings")).toBeNull(),
    );
    await waitFor(() =>
      expect(
        within(grid).getByRole("columnheader", { name: "Ranking" }),
      ).toBeVisible(),
    );
    for (const [name, rank] of [
      ["Prompt A", "1"],
      ["Prompt B", "2"],
    ]) {
      const row = within(grid).getByText(name).closest("tr");
      expect(
        within(row).getByRole("cell", { name: rank, exact: true }),
      ).toBeVisible();
    }
    expect(
      requests.get.mock.calls.filter(([url]) => url === `${parentUrl}stats/`),
    ).toHaveLength(2);
    expect(new Set(requests.get.mock.calls.map(([url]) => url))).toEqual(
      new Set([
        metadataUrl,
        `${parentUrl}stats/`,
        `${parentUrl}comparisons/`,
        parentUrl,
      ]),
    );
    expectLegacyOnly();
  },
);

it.each([0, 1])(
  "cancels the individual Summary chooser from entry %s without saving",
  async (index) => {
    mountSummary();
    const grid = await screen.findByRole("table", { name: "Summary rankings" });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Choose winner" })[index],
    );
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByText("Winner Settings")).toBeNull(),
    );
    expect(requests.post).not.toHaveBeenCalled();
    expect(
      within(grid).queryByRole("columnheader", { name: "Ranking" }),
    ).toBeNull();
    expectLegacyOnly();
  },
);
