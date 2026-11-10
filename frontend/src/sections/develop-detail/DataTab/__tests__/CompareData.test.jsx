import React from "react";
import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { grid, post, deleteCompare, Null, Passthrough } = vi.hoisted(() => ({
  grid: { props: null },
  post: vi.fn(),
  deleteCompare: vi.fn(),
  Null: () => null,
  Passthrough: ({ children }) => children,
}));

// Exercise the component's real datasource and column builder; AG Grid owns
// viewport measurements, so capture the props it consumes rather than faking
// jsdom layout. The browser check covers the actual rendered grid.
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
  default: { post },
  endpoints: {
    dataset: {
      getCompareDataset: (id) => `/model-hub/datasets/${id}/compare-datasets/`,
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
vi.mock("../CompareDatasetDetailDrawer/CompareDatasetDetailDrawer", () => ({
  default: Null,
}));
vi.mock(
  "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog",
  () => ({ default: Null }),
);
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: Passthrough }),
);

import CompareData from "../CompareData";

// CompareDatasetsView / CompareDatasetResponseSerializer use snake_case.
// In particular, metadata contains pagination, not common-column/dataset info.
const COMPARE_ID = "00000000-0000-4000-8000-000000000001";
const column = (id, name, groupId, groupName, average = null) => ({
  id,
  name,
  data_type: "text",
  origin_type: "others",
  status: "Completed",
  group: { id: groupId, name: groupName, data_type: "text", origin: "Dataset" },
  average_score: average,
});
const columnConfig = [
  column("input-a", "input", "input-a", "input"),
  column("output-a", "Dataset A", "output-a", "output", 75),
  column("output-b", "Dataset B", "output-a", "output", 0),
];
const rows = Array.from({ length: 12 }, (_, index) => ({
  row_id: `joined-${index}`,
  "input-a": { cell_value: `input-${index}`, cell_row_id: `a-${index}` },
  "output-a": { cell_value: `left-${index}`, cell_row_id: `a-${index}` },
  "output-b": { cell_value: `right-${index}`, cell_row_id: `b-${index}` },
}));
const response = (page = 0) => ({
  data: {
    status: true,
    result: {
      column_config: columnConfig,
      table: rows.slice(page * 10, (page + 1) * 10),
      metadata: { compare_id: COMPARE_ID, total_rows: 12, total_pages: 2 },
    },
  },
});
const renderCompare = (overrides = {}) => {
  const props = {
    gridApiRef: { current: null },
    compareId: { current: null },
    baseColumn: "input",
    selectedDatasetsValues: ["dataset-a", "dataset-b"],
    columns: [],
    selectedColumns: ["input-a", "output-a"],
    setColumns: vi.fn(),
    setCommonColumn: vi.fn(),
    setDatasetInfo: vi.fn(),
    setCompareColumnDefs: vi.fn(),
    ...overrides,
  };
  render(<CompareData {...props} />);
  return props;
};
const getPage = async (startRow = 0) => {
  const success = vi.fn();
  await act(async () => {
    await grid.props.serverSideDatasource.getRows({
      request: { startRow, endRow: startRow + 10 },
      success,
    });
  });
  return success;
};

describe("CompareData API response", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    post.mockImplementation((_url, body) =>
      Promise.resolve(response(body.current_page_index)),
    );
  });

  it("delivers joined rows and grouped columns from the snake_case response", async () => {
    const props = renderCompare();
    const success = await getPage();

    expect(success).toHaveBeenCalledExactlyOnceWith({
      rowData: rows.slice(0, 10),
      rowCount: 12,
    });
    expect(props.setCompareColumnDefs).toHaveBeenCalledWith([
      expect.objectContaining({ field: "input-a", headerName: "input" }),
      expect.objectContaining({
        headerName: "output",
        children: [
          expect.objectContaining({
            field: "output-a",
            headerName: "Dataset A",
          }),
          expect.objectContaining({
            field: "output-b",
            headerName: "Dataset B",
          }),
        ],
      }),
    ]);
    const [input, output] = props.setCompareColumnDefs.mock.lastCall[0];
    expect(input.valueGetter({ data: rows[0] })).toBe("input-0");
    expect(
      output.children.map((col) => col.valueGetter({ data: rows[0] })),
    ).toEqual(["left-0", "right-0"]);
  });

  it("reuses the server compare_id when fetching the next ten-row block", async () => {
    const props = renderCompare();
    await getPage();
    expect(props.compareId.current).toBe(COMPARE_ID);
    const success = await getPage(10);

    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      {
        dataset_ids: ["dataset-b"],
        base_column_name: "input",
        page_size: 10,
        current_page_index: 1,
        compare_id: COMPARE_ID,
      },
    );
    expect(success).toHaveBeenCalledExactlyOnceWith({
      rowData: rows.slice(10),
      rowCount: 12,
    });
  });

  it("uses column average_score, including zero, for the pinned summary", async () => {
    renderCompare();
    await getPage();
    expect(grid.props.pinnedBottomRowData).toEqual([
      { "output-a": "Average: 75.00%", "output-b": "Average: 0.00%" },
    ]);
  });

  it("accepts the metadata-only response for datasets without common rows", async () => {
    post.mockResolvedValue({
      data: {
        status: true,
        result: {
          metadata: { compare_id: COMPARE_ID, total_rows: 0, total_pages: 0 },
        },
      },
    });
    const props = renderCompare();
    const success = await getPage();
    expect(success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(props.compareId.current).toBe(COMPARE_ID);
    expect(props.setCompareColumnDefs).not.toHaveBeenCalled();
  });

  it("does not request a comparison without a second dataset", async () => {
    renderCompare({ selectedDatasetsValues: ["dataset-a"] });
    const success = await getPage();
    expect(post).not.toHaveBeenCalled();
    expect(success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
  });
});
