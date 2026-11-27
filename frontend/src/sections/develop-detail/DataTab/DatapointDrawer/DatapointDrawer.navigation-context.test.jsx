import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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

const activeFilter = {
  column_id: "column-label",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "approved",
  },
};

const gridApiRefWithColumnState = (columnState) => ({
  current: { api: { getColumnState: () => columnState } },
});

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

const mockNextRowResponses = () =>
  axiosPostMock
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

describe("DatapointDrawer next-row request", () => {
  beforeEach(() => axiosPostMock.mockReset());

  it("asks for the next rows with the table's active filters, search and sort", async () => {
    mockNextRowResponses();

    renderDrawer({
      validatedFilters: [activeFilter],
      searchQuery: "needle",
      gridApiRef: gridApiRefWithColumnState([
        { colId: "column-name", sort: null, sortIndex: null },
        { colId: "column-latency", sort: "asc", sortIndex: 1 },
        { colId: "column-score", sort: "desc", sortIndex: 0 },
      ]),
    });
    fireEvent.click(screen.getByRole("button", { name: /Next/i }));

    await waitFor(() => expect(axiosPostMock).toHaveBeenCalled());
    expect(axiosPostMock.mock.calls[0][0]).toBe(
      "/datasets/dataset-1/row-data/",
    );
    expect(axiosPostMock.mock.calls[0][1]).toEqual({
      row_id: rowId(11),
      filters: [activeFilter],
      search: { key: "needle", type: ["text", "image", "audio"] },
      sort: [{ column_id: "column-score", type: "descending" }],
    });
  });

  it("sends empty filters and sort and no search when the table has none", async () => {
    mockNextRowResponses();

    renderDrawer({
      validatedFilters: [],
      searchQuery: "",
      gridApiRef: gridApiRefWithColumnState([
        { colId: "column-name", sort: null, sortIndex: null },
      ]),
    });
    fireEvent.click(screen.getByRole("button", { name: /Next/i }));

    await waitFor(() => expect(axiosPostMock).toHaveBeenCalled());
    expect(axiosPostMock.mock.calls[0][1]).toEqual({
      row_id: rowId(11),
      filters: [],
      sort: [],
    });
  });
});
