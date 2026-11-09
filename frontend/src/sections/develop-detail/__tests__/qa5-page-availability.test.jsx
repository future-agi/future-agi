import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { Null, net, mount } = vi.hoisted(() => ({
  Null: () => null,
  net: { get: vi.fn() },
  mount: vi.fn(),
}));
// The page and its existing scoped-read query are real; children only record
// whether the page allowed a grid to mount. No repair-only exports are used.
vi.mock("../DevelopBar/DevelopBar", () => ({ default: Null }));
vi.mock("../DevelopBar/DatasetHeaderBar", () => ({ default: Null }));
vi.mock("../DatasetsSelectRow", () => ({ default: Null }));
vi.mock("../RunPrompt/RunPrompt", () => ({ default: Null }));
vi.mock("../Optimization/RunOptimization", () => ({ default: Null }));
vi.mock("../OptimizationTab/OptimizeTab", () => ({ default: Null }));
vi.mock("../Evaluation/EditEvaluation", () => ({ default: Null }));
vi.mock("../ExperimentTabClassic/ExperimentTabSwitch", () => ({
  default: Null,
}));
vi.mock("../ExperimentTabClassic/ExperimentTabHeaderSwitch", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/DevelopDataRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/DevelopExperimentRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/CompareSummaryRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/CompareDataRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/DevelopBarLeftSection", () => ({
  default: Null,
}));
vi.mock("../DatasetSummaryTab/DatasetSummaryTab", () => ({ default: Null }));
vi.mock("../DatasetSummaryTab/CompareSummaryTab", () => ({ default: Null }));
vi.mock("../AddColumn/AddColumnApiCall/AddColumnApiCall", () => ({
  default: Null,
}));
vi.mock("../AddColumn/ExtractEntities/ExtractEntities", () => ({
  default: Null,
}));
vi.mock("../AddColumn/Classification/Classification", () => ({
  default: Null,
}));
vi.mock("../AddColumn/ExecuteCode/ExecuteCode", () => ({ default: Null }));
vi.mock("../AddColumn/ExtractJsonKey/ExtractJsonKey", () => ({
  default: Null,
}));
vi.mock("../AddColumn/Retrieval/Retrieval", () => ({ default: Null }));
vi.mock("../AddColumn/ConditionalNode/ConditionalNodeV2", () => ({
  default: Null,
}));
vi.mock("../DataTab/CompareData", () => ({ default: Null }));
vi.mock("../DataTab/DevelopDataV2", () => ({
  default: function GridProbe() {
    mount();
    return <div data-testid="mounted-grid" />;
  },
}));
vi.mock("../DataTab/DevelopEvaluationDrawer", () => ({ default: Null }));
vi.mock("../DataTab/Versions/PendingChangesGate", () => ({ default: Null }));
vi.mock("../Evaluation/RunCompareEvaluation", () => ({ default: Null }));
vi.mock("../Experiment/RunExperiment", () => ({ default: Null }));
vi.mock("../DevelopDetailProvider", () => ({
  default: ({ children }) => children,
}));
vi.mock("../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [], isLoading: false }),
}));
vi.mock("src/api/develop/dataset-compare", () => ({
  useDeleteCompare: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ dataset: null }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: (...args) => net.get(...args) },
  endpoints: {
    annotation: {},
    develop: { getDatasetDetail: (id) => `/datasets/${id}` },
  },
}));
import DevelopDetailView from "../DevelopDetailView";
let client;
beforeEach(() => {
  mount.mockClear();
  net.get.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup();
  client.clear();
});
function page() {
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/dashboard/develop/unknown?tab=data"]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={<DevelopDetailView />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}
it("never mounts the grid while an unlisted dataset is unresolved, then admits a successful scoped read", async () => {
  let finish;
  net.get.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  page();
  await waitFor(() => expect(net.get).toHaveBeenCalled());
  expect(mount).not.toHaveBeenCalled();
  expect(screen.getByRole("status", { name: "Loading dataset" })).toBeVisible();
  await act(async () => {
    finish({ data: { result: {} } });
  });
  expect(await screen.findByTestId("mounted-grid")).toBeInTheDocument();
});
it("never exposes the grid for a denied unlisted dataset and offers neutral navigation", async () => {
  net.get.mockRejectedValue({ statusCode: 403 });
  page();
  expect(await screen.findByText("Dataset not found")).toBeVisible();
  expect(mount).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Back to Datasets" })).toBeVisible();
  expect(document.body.textContent).not.toMatch(
    /foreign|another organization/i,
  );
});
it("retries a failed availability read before admitting the grid", async () => {
  net.get
    .mockRejectedValueOnce({ statusCode: 500 })
    .mockResolvedValue({ data: { result: {} } });
  page();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not load dataset",
  );
  expect(mount).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry", exact: true }));
  expect(await screen.findByTestId("mounted-grid")).toBeInTheDocument();
  expect(net.get).toHaveBeenCalledTimes(2);
});
