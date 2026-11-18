import React from "react";
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { gridState, Null, Passthrough, summary } = vi.hoisted(() => ({
  gridState: { props: null },
  Null: () => null,
  Passthrough: ({ children }) => children,
  summary: { vals: [] },
}));

// Capture the public grid props, not the datasource's unused getRowId member.
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef((props, _ref) => {
      gridState.props = props;
      return null;
    }),
  };
});
vi.mock("src/utils/ag-grid-setup", () => ({}));
vi.mock("src/components/empty-content", () => ({ default: Null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("src/theme/ag-theme", () => ({
  AG_THEME_OVERRIDES: { dataGridPadded: {} },
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { dataset: { getCompareDataset: (id) => `/compare/${id}/` } },
}));
vi.mock("src/utils/MenuIconSet/setMeniIcons", () => ({
  setMenuIcons: () => [],
}));
vi.mock("src/utils/MenuIconSet/svgIcons", () => ({ menuIcons: {} }));
vi.mock("src/hooks/use-local-storage", () => ({
  useLocalStorage: () => ({ state: summary, update: vi.fn() }),
}));
vi.mock("src/utils/utils", () => ({
  preventHeaderSelection: vi.fn(),
  resetDLabels: vi.fn(),
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
vi.mock("../compareTableConfig", () => ({
  getEachCompareColumnDef: vi.fn(),
}));
vi.mock("../CompareDatasetDetailDrawer/CompareDatasetDetailDrawer", () => ({
  default: Null,
}));
vi.mock("src/api/develop/dataset-compare", () => ({
  useDeleteCompare: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/pages/dashboard/Develop/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ diffMode: false }),
}));
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: Passthrough }),
);
vi.mock("src/utils/constants", () => ({
  APP_CONSTANTS: { AG_GRID_SELECTION_COLUMN: "selection" },
}));
vi.mock(
  "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog",
  () => ({ default: Null }),
);

import CompareData from "../CompareData";

// The compare API preserves row_id and UUID-keyed cells; there is no rowId alias.
const rows = [
  {
    row_id: "bb1456c3-5ec7-491c-8ac7-e90c878dfdeb",
    "6a476f0f-e506-4b0f-ad55-b5dc5b7e7b79": { cell_value: "first" },
  },
  {
    row_id: "cff77890-428d-4d7d-b1d5-9f6e36b52c88",
    "6a476f0f-e506-4b0f-ad55-b5dc5b7e7b79": { cell_value: "second" },
  },
];

const renderCompare = () =>
  render(
    <CompareData
      gridApiRef={{ current: null }}
      baseColumn="input"
      selectedDatasetsValues={["base-dataset", "other-dataset"]}
      columns={[]}
      selectedColumns={[]}
      setColumns={vi.fn()}
      setCommonColumn={vi.fn()}
      setDatasetInfo={vi.fn()}
      setCompareColumnDefs={vi.fn()}
      compareId={{ current: null }}
    />,
  );

describe("CompareData row identity", () => {
  beforeEach(() => {
    gridState.props = null;
  });

  it("gives the grid stable canonical comparison row IDs", () => {
    renderCompare();
    const { getRowId, serverSideDatasource } = gridState.props;

    expect(getRowId).toEqual(expect.any(Function));
    expect(rows.map((data) => getRowId({ data, rowPinned: null }))).toEqual(
      rows.map((row) => row.row_id),
    );
    // A refreshed response contains new objects for the same comparison rows.
    expect(getRowId({ data: { ...rows[0] }, rowPinned: null })).toBe(
      rows[0].row_id,
    );
    expect(serverSideDatasource).not.toHaveProperty("getRowId");
  });

  it("gives the pinned summary its own stable ID without a backend row_id", () => {
    renderCompare();
    const { getRowId } = gridState.props;

    expect(getRowId).toEqual(expect.any(Function));
    const summaryId = getRowId({
      data: { score: "Average: 75.00%" },
      rowPinned: "bottom",
    });
    expect(summaryId).toBe("compare-summary-bottom");
    expect(
      getRowId({ data: { score: "Average: 80.00%" }, rowPinned: "bottom" }),
    ).toBe(summaryId);
    expect(rows.map((row) => row.row_id)).not.toContain(summaryId);
  });
});
