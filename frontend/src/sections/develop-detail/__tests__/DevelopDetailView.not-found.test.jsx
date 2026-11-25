import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { renderWithRouter, screen, waitFor } from "src/utils/test-utils";

// Every child of the dataset page is a stub: this test is about the page-level
// not-found gate, not about what each tab renders. vi.mock is hoisted above
// module scope, so each factory is written out in full.
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
    },
    annotation: { deleteAnnotations: "/model-hub/annotations/delete/" },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/routes/hooks", () => ({
  useSearchParams: () => [{ tab: "data" }],
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: (_key, defaultValue) => [defaultValue, vi.fn()],
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("src/api/develop/dataset-compare", () => ({
  useDeleteCompare: () => ({ mutate: vi.fn() }),
}));
vi.mock("../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("../DevelopDetailProvider", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ children }) => <>{children}</>,
}));
vi.mock("../ExperimentTab/ExperimentTab", async () => {
  const { forwardRef } = await vi.importActual("react");
  return { default: forwardRef(() => null) };
});

vi.mock("../DatasetsSelectRow", () => ({
  default: () => <div>dataset select row</div>,
}));
vi.mock("../DevelopBar/DevelopBar", () => ({
  default: () => <div>develop bar</div>,
}));
vi.mock("../DataTab/DevelopDataV2", () => ({
  default: () => <div>dataset grid</div>,
}));
vi.mock("../RunPrompt/RunPrompt", () => ({ default: () => null }));
vi.mock("../Optimization/RunOptimization", () => ({ default: () => null }));
vi.mock("../OptimizationTab/OptimizeTab", () => ({ default: () => null }));
vi.mock("../Evaluation/EditEvaluation", () => ({ default: () => null }));
vi.mock("../Evaluation/RunCompareEvaluation", () => ({ default: () => null }));
vi.mock("../DevelopBarRightSection/DevelopDataRightSection", () => ({
  default: () => null,
}));
vi.mock("../DevelopBarRightSection/DevelopExperimentRightSection", () => ({
  default: () => null,
}));
vi.mock("../DevelopBarRightSection/CompareSummaryRightSection", () => ({
  default: () => null,
}));
vi.mock("../DevelopBarRightSection/CompareDataRightSection", () => ({
  default: () => null,
}));
vi.mock("../DevelopBarRightSection/DevelopBarLeftSection", () => ({
  default: () => null,
}));
vi.mock("../DatasetSummaryTab/DatasetSummaryTab", () => ({
  default: () => null,
}));
vi.mock("../DatasetSummaryTab/CompareSummaryTab", () => ({
  default: () => null,
}));
vi.mock("../DataTab/CompareData", () => ({ default: () => null }));
vi.mock("../DataTab/DevelopEvaluationDrawer", () => ({ default: () => null }));
vi.mock("../Experiment/RunExperiment", () => ({ default: () => null }));
vi.mock("../ExperimentTab/ExperimentTabHeader", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/AddColumnApiCall/AddColumnApiCall", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/ExtractEntities/ExtractEntities", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/ConditionalNode/ConditionalNodeV2", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/Classification/Classification", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/ExecuteCode/ExecuteCode", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/ExtractJsonKey/ExtractJsonKey", () => ({
  default: () => null,
}));
vi.mock("../AddColumn/Retrieval/Retrieval", () => ({ default: () => null }));

import axios from "src/utils/axios";
import DevelopDetailView from "../DevelopDetailView";

const DATASET_ID = "0b6f4e0e-3c1a-4a59-9d55-6a3f2d1b7c11";
const MISSING_DATASET =
  "The specified dataset does not exist or has been deleted.";

// The app's axios rejects with the server's envelope plus statusCode.
const refusal = (statusCode, message, code) => ({
  status: false,
  code,
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
});

const newQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

const renderDatasetPage = (queryClient = newQueryClient()) => {
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route
          path="/dashboard/develop/:dataset"
          element={<DevelopDetailView />}
        />
      </Routes>
    </QueryClientProvider>,
    { route: `/dashboard/develop/${DATASET_ID}` },
  );
  return queryClient;
};

const availabilityStatus = (queryClient) =>
  queryClient.getQueryState(["dataset-availability", DATASET_ID])?.status;

const NOT_FOUND_TITLE = "This dataset doesn't exist or you don't have access";

describe("DevelopDetailView not-found gate", () => {
  beforeEach(() => {
    axios.get.mockReset();
  });

  it("asks for the dataset by its id", async () => {
    axios.get.mockResolvedValue({ data: { result: {} } });

    renderDatasetPage();

    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(axios.get).toHaveBeenCalledWith(
      `/model-hub/develops/${DATASET_ID}/get-dataset-table/`,
      { params: { column_config_only: true } },
    );
  });

  it("shows not-found for the current 400 on a deleted dataset", async () => {
    axios.get.mockRejectedValue(refusal(400, MISSING_DATASET, "invalid"));

    renderDatasetPage();

    expect(await screen.findByText(NOT_FOUND_TITLE)).toBeInTheDocument();
    expect(screen.queryByText("dataset grid")).not.toBeInTheDocument();
    expect(screen.queryByText("develop bar")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /back to datasets/i }),
    ).toHaveAttribute("href", "/dashboard/develop");
  });

  it("shows not-found for a 404", async () => {
    axios.get.mockRejectedValue(refusal(404, MISSING_DATASET, "not_found"));

    renderDatasetPage();

    expect(await screen.findByText(NOT_FOUND_TITLE)).toBeInTheDocument();
    expect(screen.queryByText("dataset grid")).not.toBeInTheDocument();
  });

  it("shows not-found when the dataset was deleted after a cached load", async () => {
    // Configure > Delete only navigates away; the earlier successful read
    // stays in the cache, so Back or reopening the link must read again.
    const queryClient = newQueryClient();
    queryClient.setQueryData(["dataset-availability", DATASET_ID], {
      data: { result: { column_config: [] } },
    });
    axios.get.mockRejectedValue(refusal(400, MISSING_DATASET, "invalid"));

    renderDatasetPage(queryClient);

    expect(await screen.findByText(NOT_FOUND_TITLE)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("dataset grid")).not.toBeInTheDocument();
  });

  it("shows no-access for a 403", async () => {
    axios.get.mockRejectedValue(
      refusal(403, "Permission denied", "permission_denied"),
    );

    renderDatasetPage();

    expect(
      await screen.findByText("You don't have access to this dataset"),
    ).toBeInTheDocument();
    expect(screen.queryByText("dataset grid")).not.toBeInTheDocument();
  });

  it("keeps the dataset page for a server fault", async () => {
    axios.get.mockRejectedValue(refusal(500, "Something went wrong", "error"));

    const queryClient = renderDatasetPage();

    await waitFor(() => expect(availabilityStatus(queryClient)).toBe("error"));
    expect(screen.getByText("dataset grid")).toBeInTheDocument();
    expect(screen.queryByText(NOT_FOUND_TITLE)).not.toBeInTheDocument();
  });

  it("keeps the dataset page when the dataset loads", async () => {
    axios.get.mockResolvedValue({ data: { result: { column_config: [] } } });

    const queryClient = renderDatasetPage();

    await waitFor(() =>
      expect(availabilityStatus(queryClient)).toBe("success"),
    );
    expect(screen.getByText("dataset grid")).toBeInTheDocument();
    expect(screen.getByText("dataset select row")).toBeInTheDocument();
    expect(screen.queryByText(NOT_FOUND_TITLE)).not.toBeInTheDocument();
  });
});
