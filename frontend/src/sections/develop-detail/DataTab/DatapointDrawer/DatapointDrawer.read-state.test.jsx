import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import DatapointDrawer from "./DatapointDrawer";

const axiosPostMock = vi.hoisted(() => vi.fn());

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

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span>{icon}</span>,
}));

const rowId = (number) =>
  `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;

const renderDrawer = (props = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const allRows = Array.from({ length: 11 }, (_, index) => ({
    rowIndex: index,
    data:
      index === 10
        ? { rowId: rowId(index + 1) }
        : { rowId: rowId(index + 1), value: `row-${index + 1}` },
  }));

  return render(
    <QueryClientProvider client={client}>
      <DatapointDrawer
        open
        onClose={vi.fn()}
        datapoint={{ id: "column-1", rowData: allRows[9].data }}
        setDataPointDrawerData={vi.fn()}
        allColumns={[]}
        rowIndex={9}
        setEvalDrawer={vi.fn()}
        evalDrawer={false}
        setActiveRow={vi.fn()}
        totalCount={12}
        currentColumn={null}
        setRowNewData={vi.fn()}
        allRows={allRows}
        setAllRows={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
};

describe("DatapointDrawer row adjacency", () => {
  beforeEach(() => axiosPostMock.mockReset());

  it("keeps the current datapoint on failure and retries the same bounded action", async () => {
    const setDataPointDrawerData = vi.fn();
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
          result: { [rowId(11)]: {} },
        },
      });

    renderDrawer({ setDataPointDrawerData });
    fireEvent.click(screen.getByRole("button", { name: /Next/i }));

    expect(
      await screen.findByText(/The current datapoint is still shown/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Datapoint-10")).toBeInTheDocument();
    expect(setDataPointDrawerData).not.toHaveBeenCalled();

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
    await waitFor(() => expect(setDataPointDrawerData).toHaveBeenCalled());
  });
});
