import React from "react";
import {
  act,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils.jsx";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { grid, post, get, deleteCompare, probe, Null, Passthrough } = vi.hoisted(
  () => ({
    grid: { props: null },
    post: vi.fn(),
    get: vi.fn(),
    deleteCompare: vi.fn(),
    probe: { props: null },
    Null: () => null,
    Passthrough: ({ children }) => children,
  }),
);

// CompareData's grid is replaced by a prop-capturing stub so its real
// datasource and row-click handler run; the drawer is real and its content is
// a probe recording what the drawer passes down.
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function Grid(props, _ref) {
      grid.props = props;
      return null;
    }),
  };
});
vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    dataset: {
      getCompareDataset: (id) => `/model-hub/datasets/${id}/compare-datasets/`,
      getCompareDatasetRow: (compareId, rowId) =>
        `/model-hub/datasets/get-compare-row/${compareId}/${rowId}/`,
    },
  },
}));
vi.mock("src/api/develop/dataset-compare", () => ({
  useDeleteCompare: () => ({ mutate: deleteCompare }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("src/hooks/use-local-storage", () => ({
  useLocalStorage: () => ({ state: { vals: [] }, update: vi.fn() }),
}));
vi.mock("src/pages/dashboard/Develop/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ diffMode: false }),
}));
vi.mock("src/utils/utils", () => ({
  preventHeaderSelection: vi.fn(),
  resetDLabels: vi.fn(),
}));
vi.mock("src/utils/MenuIconSet/setMeniIcons", () => ({
  setMenuIcons: vi.fn(),
}));
vi.mock("../CustomCompareColumnHeader", () => ({ default: Null }));
vi.mock("src/sections/common/DevelopCellRenderer/CustomCellRender", () => ({
  default: Null,
}));
vi.mock("src/sections/experiment-detail/ExperimentData/tableConfig", () => ({
  ExperimentDataDefaultColDef: {},
}));
vi.mock(
  "src/sections/experiment-detail/ExperimentData/CustomExperimentGroupHeader",
  () => ({ default: Null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentData/ExperimentStatusBar",
  () => ({ default: Null }),
);
vi.mock(
  "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog",
  () => ({ default: Null }),
);
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: Passthrough }),
);
vi.mock("../CompareDatasetDetailDrawer/CompareDatasetDetailContent", () => ({
  default: function ContentProbe(props) {
    probe.props = props;
    return (
      <div>
        <span data-testid="detail-row">{String(props.row?.row_id)}</span>
        <button
          type="button"
          disabled={!props.prevRowId || props.isPending}
          onClick={props.handleFetchPrevRow}
        >
          Prev
        </button>
        <button
          type="button"
          disabled={!props.nextRowId || props.isPending}
          onClick={props.handleFetchNextRow}
        >
          Next
        </button>
      </div>
    );
  },
}));

import CompareDatasetDetailDrawer from "../CompareDatasetDetailDrawer/CompareDatasetDetailDrawer";
import CompareData from "../CompareData";

// GetCompareDatasetRow / CompareDatasetsView shapes (snake_case).
const COMPARE_ID = "00000000-0000-4000-8000-000000000001";
const ROW_IDS = ["joined-0", "joined-1", "joined-2"];
const rowUrl = (compareId, rowId) =>
  `/model-hub/datasets/get-compare-row/${compareId}/${rowId}/`;
const columnConfig = [
  {
    id: "input-a",
    name: "input",
    data_type: "text",
    origin_type: "others",
    dataset_id: "dataset-a",
    status: "Completed",
    group: {
      id: "input-a",
      name: "input",
      data_type: "text",
      origin: "Dataset",
    },
    average_score: null,
  },
];
const tableRow = (rowId) => ({
  row_id: rowId,
  "input-a": { cell_value: `value-${rowId}`, cell_row_id: `source-${rowId}` },
});
const withQuery = (ui) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {ui}
  </QueryClientProvider>
);

describe("Compare row detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    probe.props = null;
    get.mockImplementation((url) => {
      const rowId = url.split("/").filter(Boolean).pop();
      const index = ROW_IDS.indexOf(rowId);
      return Promise.resolve({
        data: {
          status: true,
          result: {
            table: [tableRow(rowId)],
            prev_row_id: ROW_IDS[index - 1] ?? null,
            next_row_id: ROW_IDS[index + 1] ?? null,
          },
        },
      });
    });
  });

  it("loads the clicked row and follows next_row_id and prev_row_id", async () => {
    const user = userEvent.setup();
    render(
      withQuery(
        <CompareDatasetDetailDrawer
          open
          compareId={COMPARE_ID}
          row={{ ...tableRow("joined-1"), index: 1 }}
          columnConfig={columnConfig}
          onClose={vi.fn()}
        />,
      ),
    );

    await waitFor(() =>
      expect(screen.getByTestId("detail-row")).toHaveTextContent("joined-1"),
    );
    expect(get).toHaveBeenCalledWith(rowUrl(COMPARE_ID, "joined-1"));
    expect(probe.props).toMatchObject({
      prevRowId: "joined-0",
      nextRowId: "joined-2",
      isPending: false,
    });

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(screen.getByTestId("detail-row")).toHaveTextContent("joined-2"),
    );
    // The last joined row has no next row.
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Prev" }));
    await waitFor(() =>
      expect(screen.getByTestId("detail-row")).toHaveTextContent("joined-1"),
    );
  });

  it("opens the clicked grid row in the detail drawer", async () => {
    post.mockResolvedValue({
      data: {
        status: true,
        result: {
          column_config: columnConfig,
          table: ROW_IDS.map(tableRow),
          metadata: { compare_id: COMPARE_ID, total_rows: 3, total_pages: 1 },
        },
      },
    });
    render(
      withQuery(
        <CompareData
          gridApiRef={{ current: null }}
          compareId={{ current: null }}
          baseColumn="input"
          selectedDatasetsValues={["dataset-a", "dataset-b"]}
          columns={[]}
          selectedColumns={["input-a"]}
          setColumns={vi.fn()}
          setCommonColumn={vi.fn()}
          setDatasetInfo={vi.fn()}
          setCompareColumnDefs={vi.fn()}
        />,
      ),
    );
    await act(async () => {
      await grid.props.serverSideDatasource.getRows({
        request: { startRow: 0, endRow: 10 },
        success: vi.fn(),
        fail: vi.fn(),
      });
    });

    // An ordinary data cell, as AG Grid passes it in params.event.target.
    const rowElement = document.createElement("div");
    rowElement.className = "ag-row";
    const cell = document.createElement("div");
    cell.className = "ag-cell";
    cell.setAttribute("col-id", "input-a");
    rowElement.appendChild(cell);
    document.body.appendChild(rowElement);
    const node = { isSelected: vi.fn(() => false), setSelected: vi.fn() };

    act(() => {
      grid.props.onRowClicked({
        data: tableRow("joined-2"),
        rowIndex: 2,
        event: { target: cell },
        node,
      });
    });

    await waitFor(() =>
      expect(screen.getByTestId("detail-row")).toHaveTextContent("joined-2"),
    );
    expect(get).toHaveBeenCalledWith(rowUrl(COMPARE_ID, "joined-2"));
    expect(node.setSelected).not.toHaveBeenCalled();
    expect(probe.props).toMatchObject({
      prevRowId: "joined-1",
      nextRowId: null,
    });
    rowElement.remove();
  });
});
