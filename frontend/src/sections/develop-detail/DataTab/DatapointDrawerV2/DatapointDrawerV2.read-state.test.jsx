import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import DatapointDrawerV2 from "./DatapointDrawerV2";

const axiosPostMock = vi.hoisted(() => vi.fn());
const setDatapointMock = vi.hoisted(() => vi.fn());
const setDrawerColumnMock = vi.hoisted(() => vi.fn());
const rowId = vi.hoisted(
  () => (number) =>
    `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`,
);

vi.mock("src/utils/axios", () => ({
  default: { post: axiosPostMock },
  endpoints: {
    develop: {
      getRowData: (datasetId) => `/datasets/${datasetId}/row-data/`,
      getCellData: "/datasets/cell-data/",
    },
  },
}));

vi.mock("react-router", () => ({
  useParams: () => ({ dataset: "dataset-1" }),
}));

vi.mock("../../states", () => ({
  useAddEvaluationFeebackStore: () => ({ setAddEvaluationFeeback: vi.fn() }),
  useImprovePromptStore: () => ({ setImprovePrompt: vi.fn() }),
  useDatapointDrawerStore: () => ({
    datapoint: { index: 10, rowData: { row_id: rowId(11) } },
    setDatapoint: setDatapointMock,
    setDrawerColumn: setDrawerColumnMock,
    column: null,
  }),
}));

vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    gridApi: {
      current: {
        getGridOption: () => 12,
        forEachNode: (callback) => {
          for (let index = 0; index < 11; index += 1) {
            callback({
              displayed: true,
              id: rowId(index + 1),
              data: { row_id: rowId(index + 1) },
            });
          }
        },
        ensureIndexVisible: vi.fn(),
      },
    },
  }),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => [],
}));
vi.mock("src/sections/common/EvaluationDrawer/getEvalsList", () => ({
  useEvalsList: () => ({ data: { evals: [] } }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span>{icon}</span>,
}));
vi.mock("src/components/loading-screen/LoadingOverlayDataPointDataset", () => ({
  default: () => null,
}));

const renderDrawer = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DatapointDrawerV2 />
    </QueryClientProvider>,
  );
};

describe("DatapointDrawerV2 row adjacency", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    setDatapointMock.mockReset();
    setDrawerColumnMock.mockReset();
  });

  it("keeps the current datapoint on failure and retries one bounded navigation", async () => {
    axiosPostMock
      .mockRejectedValueOnce(new Error("adjacency failed"))
      .mockResolvedValueOnce({
        data: {
          status: true,
          result: {
            current: { row_id: rowId(11) },
            next: { row_id: [rowId(12)] },
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          status: true,
          result: { [rowId(12)]: {} },
        },
      });

    renderDrawer();
    fireEvent.keyDown(window, { key: "j" });

    expect(
      await screen.findByText(/The current datapoint is still shown/i),
    ).toBeInTheDocument();
    expect(setDatapointMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(axiosPostMock).toHaveBeenCalledTimes(3));
    expect(axiosPostMock.mock.calls[0][0]).toBe(
      "/datasets/dataset-1/row-data/",
    );
    expect(axiosPostMock.mock.calls[1][0]).toBe(
      "/datasets/dataset-1/row-data/",
    );
    for (const call of axiosPostMock.mock.calls) {
      expect(call[2]).toMatchObject({
        timeout: INTERACTIVE_REQUEST_TIMEOUT_MS,
      });
      expect(call[2].signal).toBeInstanceOf(AbortSignal);
    }
    await waitFor(() =>
      expect(setDatapointMock).toHaveBeenCalledWith(
        expect.objectContaining({
          index: 11,
          rowData: { row_id: rowId(12) },
        }),
      ),
    );
  });
});
