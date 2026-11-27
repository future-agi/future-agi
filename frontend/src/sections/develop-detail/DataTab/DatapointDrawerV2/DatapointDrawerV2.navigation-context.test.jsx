import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DefaultFilter, transformFilter } from "../DevelopFilters/common";
import DatapointDrawerV2 from "./DatapointDrawerV2";

const axiosPostMock = vi.hoisted(() => vi.fn());
const tableState = vi.hoisted(() => ({
  filters: [],
  search: "",
  columnState: [],
}));
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
    setDatapoint: vi.fn(),
    setDrawerColumn: vi.fn(),
    column: null,
  }),
  useDevelopFilterStore: {
    getState: () => ({ filters: tableState.filters }),
  },
  useDevelopSearchStore: {
    getState: () => ({ search: tableState.search }),
  },
}));

vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    gridApi: {
      current: {
        getGridOption: (key) =>
          key === "context" ? { totalRowCount: 12 } : undefined,
        getColumnState: () => tableState.columnState,
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

const panelFilter = {
  id: "filter-label",
  columnId: "column-label",
  filterConfig: {
    filterType: "text",
    filterOp: "equals",
    filterValue: "approved",
  },
};
const unfinishedPanelFilter = { ...DefaultFilter, id: "filter-unfinished" };

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
        result: { [rowId(12)]: {} },
      },
    });

describe("DatapointDrawerV2 next-row request", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    tableState.filters = [];
    tableState.search = "";
    tableState.columnState = [];
  });

  it("asks for the next rows with the table's active filters, search and sort", async () => {
    tableState.filters = [panelFilter, unfinishedPanelFilter];
    tableState.search = "needle";
    tableState.columnState = [
      { colId: "column-name", sort: null, sortIndex: null },
      { colId: "column-latency", sort: "asc", sortIndex: 1 },
      { colId: "column-score", sort: "desc", sortIndex: 0 },
    ];
    mockNextRowResponses();

    renderDrawer();
    fireEvent.keyDown(window, { key: "j" });

    await waitFor(() => expect(axiosPostMock).toHaveBeenCalled());
    expect(axiosPostMock.mock.calls[0][0]).toBe(
      "/datasets/dataset-1/row-data/",
    );
    expect(axiosPostMock.mock.calls[0][1]).toEqual({
      row_id: rowId(11),
      filters: [transformFilter(panelFilter)],
      search: { key: "needle", type: ["text", "image", "audio"] },
      sort: [{ column_id: "column-score", type: "descending" }],
    });
  });

  it("sends empty filters and sort and no search when the table has none", async () => {
    tableState.filters = [unfinishedPanelFilter];
    tableState.columnState = [
      { colId: "column-name", sort: null, sortIndex: null },
    ];
    mockNextRowResponses();

    renderDrawer();
    fireEvent.keyDown(window, { key: "j" });

    await waitFor(() => expect(axiosPostMock).toHaveBeenCalled());
    expect(axiosPostMock.mock.calls[0][1]).toEqual({
      row_id: rowId(11),
      filters: [],
      sort: [],
    });
  });
});
