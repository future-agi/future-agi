import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import SyntheticSummaryDrawer from "./SyntheticSummaryDrawer";
import { resetEditSyntheticStates, useEditSyntheticDataStore } from "./state";

const DATASET_ID = "5f0c8a52-0d3b-4c1e-9a7e-2b7f9f1d4c10";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  refreshGrid: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: mocks.get, put: mocks.put } };
});

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useParams: () => ({ dataset: DATASET_ID }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: mocks.refreshGrid }),
}));

vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

const SUMMARY = {
  dataset: { name: "Support Tickets", description: "", objective: "" },
  num_rows: 10,
  columns: [],
};

const renderDrawer = (datasetResult) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(
    getDatasetQueryOptions(DATASET_ID, 0, [], [], "").queryKey,
    { data: { result: datasetResult } },
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <SyntheticSummaryDrawer />
    </QueryClientProvider>,
  );
};

// The data tab overlay and this drawer must agree on whether the last
// synthetic generation failed: when the overlay says it failed, the drawer it
// opens has to offer the retry.
describe("SyntheticSummaryDrawer", () => {
  beforeEach(() => {
    mocks.get.mockResolvedValue({ data: { result: { data: SUMMARY } } });
    mocks.put.mockResolvedValue({
      data: { result: { message: "ok", data: { datasetId: DATASET_ID } } },
    });
    useEditSyntheticDataStore.setState({
      openSummaryDrawer: true,
      failedToGenerateData: false,
    });
  });

  afterEach(() => {
    resetEditSyntheticStates();
    vi.clearAllMocks();
  });

  it("offers the retry when the backend recorded a failed generation", async () => {
    renderDrawer({
      synthetic_dataset: true,
      dataset_config: {
        synthetic_generation_error: {
          code: "generation_failed",
          message: "Generation failed; try again or contact support",
        },
      },
    });

    expect(
      await screen.findByRole("button", {
        name: /Re-Generate same Configuration/,
      }),
    ).toBeInTheDocument();
  });

  it("does not offer the retry while nothing has failed", async () => {
    renderDrawer({ synthetic_dataset: true, dataset_config: {} });

    expect(
      await screen.findByRole("button", { name: /Edit Configuration/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Re-Generate same Configuration/ }),
    ).not.toBeInTheDocument();
  });

  it("clears the failed state once a retry has started", async () => {
    useEditSyntheticDataStore.setState({ failedToGenerateData: true });
    renderDrawer({ synthetic_dataset: true, dataset_config: {} });

    fireEvent.click(
      await screen.findByRole("button", {
        name: /Re-Generate same Configuration/,
      }),
    );

    await waitFor(() =>
      expect(useEditSyntheticDataStore.getState().failedToGenerateData).toBe(
        false,
      ),
    );
    expect(mocks.put).toHaveBeenCalledTimes(1);
  });
});
