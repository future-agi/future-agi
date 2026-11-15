import React from "react";
import { act, render } from "src/utils/test-utils.jsx";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { grid, post, deleteCompare, snackbar, Null, Passthrough } = vi.hoisted(
  () => ({
    grid: { props: null, inRouterContext: false, spacing: null },
    post: vi.fn(),
    deleteCompare: vi.fn(),
    Null: () => null,
    snackbar: { enqueueSnackbar: vi.fn() },
    Passthrough: ({ children }) => children,
  }),
);

// Exercise the component's real datasource and column builder; AG Grid owns
// viewport measurements, so capture the props it consumes rather than faking
// jsdom layout. The browser check covers the actual rendered grid.
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  const { useInRouterContext } = await import("react-router-dom");
  const { useTheme } = await import("@mui/material/styles");
  return {
    AgGridReact: forwardRef(function Grid(props, _ref) {
      grid.props = props;
      grid.inRouterContext = useInRouterContext();
      grid.spacing = useTheme().spacing(1);
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
vi.mock("src/api/develop/dataset-compare", async (importOriginal) => ({
  ...(await importOriginal()),
  useDeleteCompare: () => ({ mutate: deleteCompare }),
}));
vi.mock("src/components/snackbar", () => snackbar);
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
  setMenuIcons: vi.fn(() => []),
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
  default: () => <div data-testid="compare-detail-drawer" />,
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
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
const withCompareId = (compareId, page = 0) => {
  const body = response(page);
  body.data.result.metadata.compare_id = compareId;
  return body;
};
const startPage = (startRow = 0) => {
  const handlers = { success: vi.fn(), fail: vi.fn() };
  let pending;
  act(() => {
    pending = grid.props.serverSideDatasource.getRows({
      request: { startRow, endRow: startRow + 10 },
      ...handlers,
    });
  });
  return { ...handlers, pending };
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

  it("renders the datasource with the shared router and theme providers", () => {
    renderCompare();

    expect(grid.inRouterContext).toBe(true);
    expect(grid.spacing).toBe("0.25rem");
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
    expect(grid.props.getRowId({ data: rows[0] })).toBe("joined-0");
    // Neither private dataset_info nor common_column_names is in the public
    // response. Do not feed undefined/partial row maps to the parent.
    expect(props.setCommonColumn).not.toHaveBeenCalled();
    expect(props.setDatasetInfo).not.toHaveBeenCalled();
    expect(input.valueGetter({ data: rows[0] })).toBe("input-0");
    expect(
      output.children.map((col) => col.valueGetter({ data: rows[0] })),
    ).toEqual(["left-0", "right-0"]);
  });

  it("keeps arbitrary user-data keys inside cells unchanged", async () => {
    const payload = {
      customer_key: { row_id: "user-row", compare_id: "user-comparison" },
      alreadyCamel: "unchanged",
    };
    const body = response();
    const row = {
      ...rows[0],
      "input-a": {
        ...rows[0]["input-a"],
        cell_value: payload,
        value_infos: {
          reason: "synthetic",
          metadata: { selected_input_key: "customer_key" },
        },
      },
    };
    body.data.result.table = [row];
    post.mockResolvedValueOnce(body);
    renderCompare();
    const success = await getPage();
    expect(success.mock.calls[0][0].rowData[0]).toBe(row);
    expect(row["input-a"].cell_value).toBe(payload);
    expect(row["input-a"].value_infos.metadata).toEqual({
      selected_input_key: "customer_key",
    });
    expect(Object.keys(row)).toEqual([
      "row_id",
      "input-a",
      "output-a",
      "output-b",
    ]);
  });

  it("uses origin_type on nested raw evaluation columns rather than the grid definition alias", async () => {
    const body = response();
    body.data.result.column_config = columnConfig.map((col) =>
      col.id === "input-a"
        ? col
        : {
            ...col,
            origin_type: "evaluation",
            group: { ...col.group, origin: "Evaluation" },
          },
    );
    post.mockResolvedValueOnce(body);
    const props = renderCompare();
    await getPage();
    const [, evaluation] = props.setCompareColumnDefs.mock.lastCall[0];
    expect(evaluation.children[0]).toMatchObject({
      originType: "evaluation",
      minWidth: 300,
      col: { origin_type: "evaluation" },
    });
    const menu = grid.props.getMainMenuItems({
      column: { colDef: evaluation.children[0] },
    });
    expect(menu).toContainEqual(
      expect.objectContaining({ name: "Show Summary" }),
    );
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
    expect(
      grid.props.statusBar.statusPanels[0].statusPanelParams.columnAvgs,
    ).toEqual([
      { id: "output-a", value: 75 },
      { id: "output-b", value: 0 },
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

describe("CompareData compare_id ownership", () => {
  const OLD_ID = "00000000-0000-4000-8000-00000000000a";
  const NEW_ID = "00000000-0000-4000-8000-00000000000b";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the current selection's compare_id when an earlier request resolves last", async () => {
    const first = deferred();
    const second = deferred();
    post
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
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
    };
    const { rerender } = render(<CompareData {...props} />);
    const stale = startPage();

    // The user picks another dataset while the first comparison is running.
    await act(async () => {
      rerender(
        <CompareData
          {...props}
          selectedDatasetsValues={["dataset-a", "dataset-c"]}
        />,
      );
    });
    const current = startPage();
    expect(post.mock.calls.map(([, body]) => body.dataset_ids)).toEqual([
      ["dataset-b"],
      ["dataset-c"],
    ]);

    await act(async () => {
      second.resolve(withCompareId(NEW_ID));
      await current.pending;
    });
    await act(async () => {
      first.resolve(withCompareId(OLD_ID));
      await stale.pending;
    });

    expect(props.compareId.current).toBe(NEW_ID);
    expect(current.success).toHaveBeenCalledTimes(1);
    expect(stale.success).not.toHaveBeenCalled();
    expect(stale.fail).toHaveBeenCalledTimes(1);
    expect(props.setCompareColumnDefs).toHaveBeenCalledTimes(1);
    // The orphaned comparison from the earlier selection is removed.
    expect(deleteCompare).toHaveBeenCalledExactlyOnceWith(OLD_ID);

    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(NEW_ID, 1)),
    );
    await getPage(10);
    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      expect.objectContaining({
        dataset_ids: ["dataset-c"],
        current_page_index: 1,
        compare_id: NEW_ID,
      }),
    );
  });

  it("does not show the empty overlay when an earlier request fails late", async () => {
    const first = deferred();
    post
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => Promise.resolve(withCompareId(NEW_ID)));
    const api = {
      refreshServerSide: vi.fn(),
      refreshCells: vi.fn(),
      showNoRowsOverlay: vi.fn(),
    };
    const props = {
      gridApiRef: { current: { api } },
      compareId: { current: null },
      baseColumn: "input",
      selectedDatasetsValues: ["dataset-a", "dataset-b"],
      columns: [],
      selectedColumns: ["input-a", "output-a"],
      setColumns: vi.fn(),
      setCommonColumn: vi.fn(),
      setDatasetInfo: vi.fn(),
      setCompareColumnDefs: vi.fn(),
    };
    const { rerender } = render(<CompareData {...props} />);
    const stale = startPage();
    await act(async () => {
      rerender(
        <CompareData
          {...props}
          selectedDatasetsValues={["dataset-a", "dataset-c"]}
        />,
      );
    });
    const current = startPage();
    await act(async () => {
      await current.pending;
    });
    await act(async () => {
      first.reject(new Error("comparison failed"));
      await stale.pending;
    });

    expect(props.compareId.current).toBe(NEW_ID);
    expect(stale.success).not.toHaveBeenCalled();
    expect(stale.fail).toHaveBeenCalledTimes(1);
    expect(api.showNoRowsOverlay).not.toHaveBeenCalled();
  });

  it("starts a new comparison and removes the old one when the base column changes", async () => {
    post.mockImplementation((_url, body) =>
      Promise.resolve(
        withCompareId(
          body.base_column_name === "input" ? OLD_ID : NEW_ID,
          body.current_page_index,
        ),
      ),
    );
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
    };
    const { rerender } = render(<CompareData {...props} />);
    await getPage();
    expect(props.compareId.current).toBe(OLD_ID);

    await act(async () => {
      rerender(<CompareData {...props} baseColumn="output" />);
    });
    await getPage();

    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      expect.objectContaining({
        base_column_name: "output",
        current_page_index: 0,
        compare_id: null,
      }),
    );
    expect(deleteCompare).toHaveBeenCalledExactlyOnceWith(OLD_ID);
    expect(props.compareId.current).toBe(NEW_ID);
  });
});

describe("CompareData successful ID rollover", () => {
  const OLD_ID = "00000000-0000-4000-8000-000000000011";
  const NEW_ID = "00000000-0000-4000-8000-000000000012";
  const OTHER_ID = "00000000-0000-4000-8000-000000000013";
  const generationResponse = (id, page = 0) => ({
    data: {
      status: true,
      result: {
        column_config: columnConfig,
        table: Array.from({ length: page === 3 ? 2 : 10 }, (_, offset) => ({
          row_id: `${id}-row-${page * 10 + offset}`,
          "input-a": {
            cell_value: `input-${page * 10 + offset}`,
            cell_row_id: `source-${page * 10 + offset}`,
          },
        })),
        metadata: { compare_id: id, total_rows: 32, total_pages: 4 },
      },
    },
  });
  const setup = () => {
    const api = {
      refreshServerSide: vi.fn(),
      refreshCells: vi.fn(),
      showNoRowsOverlay: vi.fn(),
      deselectAll: vi.fn(),
    };
    const props = renderCompare({ gridApiRef: { current: { api } } });
    api.refreshServerSide.mockClear();
    return { api, props };
  };

  beforeEach(() => {
    vi.clearAllMocks();
    post.mockReset();
  });

  it("adopts the first ID without a reload loop and preserves same-ID blocks", async () => {
    const { api, props } = setup();
    post.mockImplementation((_url, body) =>
      Promise.resolve(generationResponse(OLD_ID, body.current_page_index)),
    );
    const first = await getPage();
    await getPage(10);
    expect(first).toHaveBeenCalledTimes(1);
    expect(props.compareId.current).toBe(OLD_ID);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.lastCall[1].compare_id).toBe(OLD_ID);
    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(api.deselectAll).not.toHaveBeenCalled();
  });

  it("purges a nonempty later-block 200 rollover and sends its new ID on the next request", async () => {
    const { api, props } = setup();
    post.mockResolvedValueOnce(generationResponse(OLD_ID));
    await getPage();
    const laterResponse = generationResponse(NEW_ID, 1);
    expect(laterResponse.data.result.table).toHaveLength(10);
    post.mockResolvedValueOnce(laterResponse);
    const later = startPage(10);
    await act(async () => {
      await later.pending;
    });

    expect(post.mock.lastCall[1]).toMatchObject({
      compare_id: OLD_ID,
      current_page_index: 1,
    });
    expect(props.compareId.current).toBe(NEW_ID);
    // The response belongs to the old grid store; only the purged store may
    // install rows, all fetched using the ID that won the rollover.
    expect(later.success).not.toHaveBeenCalled();
    expect(later.fail).toHaveBeenCalledTimes(1);
    expect(api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({
      purge: true,
    });
    expect(api.deselectAll).toHaveBeenCalledTimes(1);
    expect(grid.props.pinnedBottomRowData).toEqual([]);
    expect(props.selectedDatasetsValues).toEqual(["dataset-a", "dataset-b"]);
    expect(props.selectedColumns).toEqual(["input-a", "output-a"]);

    post.mockImplementation((_url, body) =>
      Promise.resolve(generationResponse(NEW_ID, body.current_page_index)),
    );
    const reloaded = await getPage();
    expect(post.mock.lastCall[1]).toMatchObject({
      compare_id: NEW_ID,
      current_page_index: 0,
    });
    expect(reloaded.mock.calls[0][0].rowData[0].row_id).toBe(`${NEW_ID}-row-0`);
    await getPage(10);
    expect(post.mock.lastCall[1].compare_id).toBe(NEW_ID);
    expect(api.refreshServerSide).toHaveBeenCalledTimes(1);
    expect(deleteCompare).not.toHaveBeenCalled();
  });

  it.each([
    [0, 1],
    [1, 0],
  ])(
    "keeps one generation when concurrent rollovers resolve in order %i then %i",
    async (winner, loser) => {
      const { api, props } = setup();
      post.mockResolvedValueOnce(generationResponse(OLD_ID));
      await getPage();
      const requests = [deferred(), deferred()];
      const ids = [NEW_ID, OTHER_ID];
      post
        .mockImplementationOnce(() => requests[0].promise)
        .mockImplementationOnce(() => requests[1].promise);
      const blocks = [startPage(10), startPage(20)];
      expect(
        post.mock.calls.slice(-2).map(([, body]) => body.compare_id),
      ).toEqual([OLD_ID, OLD_ID]);
      await act(async () => {
        requests[winner].resolve(generationResponse(ids[winner], winner + 1));
        await blocks[winner].pending;
      });
      post.mockResolvedValueOnce(generationResponse(ids[winner]));
      const reload = await getPage();
      const columnsBeforeLateResponse = props.setColumns.mock.calls.length;
      await act(async () => {
        requests[loser].resolve(generationResponse(ids[loser], loser + 1));
        await blocks[loser].pending;
      });
      expect(props.compareId.current).toBe(ids[winner]);
      expect(api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({
        purge: true,
      });
      expect(reload.mock.calls[0][0].rowData[0].row_id).toBe(
        `${ids[winner]}-row-0`,
      );
      for (const block of blocks) {
        expect(block.success).not.toHaveBeenCalled();
        expect(block.fail).toHaveBeenCalledTimes(1);
      }
      expect(props.setColumns).toHaveBeenCalledTimes(columnsBeforeLateResponse);
      expect(deleteCompare).toHaveBeenCalledExactlyOnceWith(ids[loser]);
      post.mockResolvedValueOnce(generationResponse(ids[winner], 1));
      await getPage(10);
      expect(post.mock.lastCall[1].compare_id).toBe(ids[winner]);
    },
  );

  it.each(["success", "not-found", "storage-unavailable"])(
    "rejects an obsolete %s response after successful rollover",
    async (outcome) => {
      const { api, props } = setup();
      post.mockResolvedValueOnce(generationResponse(OLD_ID));
      await getPage();
      const late = deferred();
      post
        .mockImplementationOnce(() => late.promise)
        .mockResolvedValueOnce(generationResponse(NEW_ID, 2));
      const obsolete = startPage(10);
      await getPage(20);
      post.mockResolvedValueOnce(generationResponse(NEW_ID));
      await getPage();
      await act(async () => {
        if (outcome === "success") late.resolve(generationResponse(OLD_ID, 1));
        else
          late.reject({
            statusCode: outcome === "not-found" ? 404 : 503,
            code:
              outcome === "not-found"
                ? "compare_not_found"
                : "compare_storage_unavailable",
          });
        await obsolete.pending;
      });
      expect(obsolete.success).not.toHaveBeenCalled();
      expect(obsolete.fail).toHaveBeenCalledTimes(1);
      expect(props.compareId.current).toBe(NEW_ID);
      expect(api.refreshServerSide).toHaveBeenCalledTimes(1);
      expect(api.showNoRowsOverlay).not.toHaveBeenCalled();
      expect(snackbar.enqueueSnackbar).not.toHaveBeenCalled();
      expect(deleteCompare).not.toHaveBeenCalled();
    },
  );

  it.each([NEW_ID, OTHER_ID])(
    "rereads the losing initial block using the adopted ID when its response carries %s",
    async (lateId) => {
      const { props, api } = setup();
      const late = deferred();
      post
        .mockImplementationOnce(() => late.promise)
        .mockResolvedValueOnce(generationResponse(NEW_ID, 1))
        .mockResolvedValueOnce(generationResponse(NEW_ID));
      const initialBlock = startPage();
      await getPage(10);
      await act(async () => {
        late.resolve(generationResponse(lateId));
        await initialBlock.pending;
      });
      expect(props.compareId.current).toBe(NEW_ID);
      expect(post).toHaveBeenCalledTimes(3);
      expect(post.mock.lastCall[1]).toMatchObject({
        compare_id: NEW_ID,
        current_page_index: 0,
      });
      expect(initialBlock.success).toHaveBeenCalledTimes(1);
      expect(initialBlock.success.mock.calls[0][0].rowData[0].row_id).toBe(
        `${NEW_ID}-row-0`,
      );
      expect(initialBlock.fail).not.toHaveBeenCalled();
      if (lateId === NEW_ID) expect(deleteCompare).not.toHaveBeenCalled();
      else expect(deleteCompare).toHaveBeenCalledExactlyOnceWith(OTHER_ID);
      expect(api.refreshServerSide).not.toHaveBeenCalled();
    },
  );

  it("rejects a pending rollover after peer order changes and starts the new selection without an ID", async () => {
    const props = {
      gridApiRef: { current: null },
      compareId: { current: null },
      baseColumn: "input",
      selectedDatasetsValues: ["dataset-a", "dataset-b", "dataset-c"],
      columns: [],
      selectedColumns: ["input-a"],
      setColumns: vi.fn(),
      setCommonColumn: vi.fn(),
      setDatasetInfo: vi.fn(),
      setCompareColumnDefs: vi.fn(),
    };
    const { rerender } = render(<CompareData {...props} />);
    post.mockResolvedValueOnce(generationResponse(OLD_ID));
    await getPage();
    const late = deferred();
    post.mockImplementationOnce(() => late.promise);
    const obsolete = startPage(10);
    await act(async () => {
      rerender(
        <CompareData
          {...props}
          selectedDatasetsValues={["dataset-a", "dataset-c", "dataset-b"]}
        />,
      );
    });
    post.mockResolvedValueOnce(generationResponse(NEW_ID));
    await getPage();
    expect(post.mock.lastCall[1]).toMatchObject({
      dataset_ids: ["dataset-c", "dataset-b"],
      compare_id: null,
    });
    await act(async () => {
      late.resolve(generationResponse(OTHER_ID, 1));
      await obsolete.pending;
    });
    expect(props.compareId.current).toBe(NEW_ID);
    expect(obsolete.success).not.toHaveBeenCalled();
    expect(deleteCompare.mock.calls).toEqual([[OLD_ID], [OTHER_ID]]);
  });

  it("invalidates pending blocks when download recovery clears the shared ID", async () => {
    const { props } = setup();
    post.mockResolvedValueOnce(generationResponse(OLD_ID));
    await getPage();
    const late = deferred();
    post.mockImplementationOnce(() => late.promise);
    const obsolete = startPage(10);
    // DevelopDetailView's onComparisonExpired callback resets this ref.
    props.compareId.current = null;
    post.mockResolvedValueOnce(generationResponse(NEW_ID));
    await getPage();
    expect(post.mock.lastCall[1].compare_id).toBeNull();
    await act(async () => {
      late.resolve(generationResponse(OTHER_ID, 1));
      await obsolete.pending;
    });
    expect(props.compareId.current).toBe(NEW_ID);
    expect(obsolete.success).not.toHaveBeenCalled();
    expect(deleteCompare).toHaveBeenCalledExactlyOnceWith(OTHER_ID);
  });
});

describe("CompareData terminal zero snapshot", () => {
  const EMPTY_ID = "00000000-0000-4000-8000-000000000021";
  const OTHER_EMPTY_ID = "00000000-0000-4000-8000-000000000022";
  const empty = (id = EMPTY_ID) => ({
    data: {
      status: true,
      result: {
        metadata: { compare_id: id, total_rows: 0, total_pages: 0 },
      },
    },
  });
  const setup = () => {
    const api = {
      refreshServerSide: vi.fn(),
      refreshCells: vi.fn(),
      showNoRowsOverlay: vi.fn(),
      deselectAll: vi.fn(),
    };
    const props = renderCompare({ gridApiRef: { current: { api } } });
    api.refreshServerSide.mockClear();
    return { api, props };
  };
  const openFirstRow = () =>
    act(() =>
      grid.props.onRowClicked({
        data: rows[0],
        rowIndex: 0,
        event: { target: { closest: () => null } },
      }),
    );

  beforeEach(() => {
    vi.clearAllMocks();
    post.mockReset();
  });

  it("retires nonempty rows, selection, detail and summary once on a later-block zero rollover", async () => {
    const { api, props } = setup();
    post.mockResolvedValueOnce(response()).mockResolvedValue(empty());
    await getPage();
    openFirstRow();
    expect(
      document.querySelector('[data-testid="compare-detail-drawer"]'),
    ).not.toBeNull();
    expect(grid.props.pinnedBottomRowData).not.toEqual([]);
    // Model a synchronous new-store load as well as later refreshes. The
    // browser regression separately checks real AG Grid destroys its old rows.
    const replacementSuccess = vi.fn();
    api.refreshServerSide.mockImplementation(() => {
      grid.props.serverSideDatasource.getRows({
        request: { startRow: 0, endRow: 10 },
        success: replacementSuccess,
      });
    });
    const later = startPage(10);
    await act(async () => {
      await later.pending;
    });
    expect(props.compareId.current).toBe(EMPTY_ID);
    expect(later.fail).toHaveBeenCalledTimes(1);
    expect(later.success).not.toHaveBeenCalled();
    expect(api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({
      purge: true,
    });
    expect(replacementSuccess).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(api.deselectAll).toHaveBeenCalledTimes(1);
    expect(
      document.querySelector('[data-testid="compare-detail-drawer"]'),
    ).toBeNull();
    expect(grid.props.pinnedBottomRowData).toEqual([]);
    expect(
      grid.props.statusBar.statusPanels[0].statusPanelParams.columnAvgs,
    ).toEqual([]);
    expect(props.setCommonColumn).toHaveBeenLastCalledWith([]);
    expect(props.setDatasetInfo).toHaveBeenLastCalledWith({});
    openFirstRow();
    expect(
      document.querySelector('[data-testid="compare-detail-drawer"]'),
    ).toBeNull();
    for (const start of [0, 10, 0]) {
      expect(await getPage(start)).toHaveBeenCalledExactlyOnceWith({
        rowData: [],
        rowCount: 0,
      });
    }
    expect(post).toHaveBeenCalledTimes(2);
    expect(api.refreshServerSide).toHaveBeenCalledTimes(1);
  });

  it("ignores competing fresh zero IDs and never rerequests uncached empty metadata", async () => {
    const { api, props } = setup();
    post.mockResolvedValueOnce(response());
    await getPage();
    const late = deferred();
    post
      .mockImplementationOnce(() => late.promise)
      .mockResolvedValueOnce(empty());
    // Were another POST made, this backend would mint yet another empty ID.
    post.mockResolvedValue(empty(OTHER_EMPTY_ID));
    const obsolete = startPage(20);
    await getPage(10);
    await act(async () => {
      late.resolve(empty(OTHER_EMPTY_ID));
      await obsolete.pending;
    });
    for (let index = 0; index < 5; index += 1) await getPage();
    expect(props.compareId.current).toBe(EMPTY_ID);
    expect(post).toHaveBeenCalledTimes(3);
    expect(api.refreshServerSide).toHaveBeenCalledTimes(1);
    expect(obsolete.success).not.toHaveBeenCalled();
    expect(obsolete.fail).toHaveBeenCalledTimes(1);
    expect(deleteCompare).toHaveBeenCalledExactlyOnceWith(OTHER_EMPTY_ID);
  });

  it("refreshes an initially empty comparison locally and permits an explicit ID reset to rebuild", async () => {
    const { api, props } = setup();
    post
      .mockResolvedValueOnce(empty())
      .mockResolvedValue(empty(OTHER_EMPTY_ID));
    await getPage();
    for (let index = 0; index < 5; index += 1) {
      expect(await getPage()).toHaveBeenCalledExactlyOnceWith({
        rowData: [],
        rowCount: 0,
      });
    }
    expect(props.compareId.current).toBe(EMPTY_ID);
    expect(post).toHaveBeenCalledTimes(1);
    expect(api.refreshServerSide).not.toHaveBeenCalled();
    props.compareId.current = null; // The real download-expiry recovery callback.
    await getPage();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.lastCall[1].compare_id).toBeNull();
    expect(props.compareId.current).toBe(OTHER_EMPTY_ID);
    expect(api.refreshServerSide).not.toHaveBeenCalled();
  });

  it("completes a losing initial block locally after another block establishes zero", async () => {
    const { api, props } = setup();
    const late = deferred();
    post
      .mockImplementationOnce(() => late.promise)
      .mockResolvedValueOnce(empty());
    const obsolete = startPage();
    await getPage(10);
    await act(async () => {
      late.resolve(empty(OTHER_EMPTY_ID));
      await obsolete.pending;
    });
    expect(obsolete.success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(props.compareId.current).toBe(EMPTY_ID);
    expect(post).toHaveBeenCalledTimes(2);
    expect(api.refreshServerSide).toHaveBeenCalledTimes(1);
  });

  it.each([COMPARE_ID, OTHER_EMPTY_ID])(
    "does not make an empty out-of-range block terminal when total_rows is positive (ID %s)",
    async (id) => {
      const { api, props } = setup();
      post.mockResolvedValueOnce(response());
      await getPage();
      const outOfRange = withCompareId(id, 2);
      expect(outOfRange.data.result.table).toEqual([]);
      post.mockResolvedValueOnce(outOfRange);
      const result = await getPage(20);
      if (id === COMPARE_ID) {
        expect(result).toHaveBeenCalledExactlyOnceWith({
          rowData: [],
          rowCount: 12,
        });
        expect(api.refreshServerSide).not.toHaveBeenCalled();
      } else {
        expect(result).not.toHaveBeenCalled();
        expect(api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({
          purge: true,
        });
      }
      post.mockResolvedValueOnce(withCompareId(id));
      const first = await getPage();
      expect(first).toHaveBeenCalledExactlyOnceWith({
        rowData: rows.slice(0, 10),
        rowCount: 12,
      });
      expect(post).toHaveBeenCalledTimes(3);
      expect(props.compareId.current).toBe(id);
      expect(api.showNoRowsOverlay).not.toHaveBeenCalled();
    },
  );
});

describe("CompareData expired comparison", () => {
  const EXPIRED_ID = "00000000-0000-4000-8000-00000000000c";
  const FRESH_ID = "00000000-0000-4000-8000-00000000000d";
  // Retain bounded legacy typed-404 recovery. The landed compare POST instead
  // rebuilds cache misses with 200/fresh IDs, covered by the generation tests.
  const notFound = () => ({
    statusCode: 404,
    code: "compare_not_found",
    result: "This comparison was not found or has expired.",
  });
  const renderWithGrid = () => {
    const api = {
      refreshServerSide: vi.fn(),
      refreshCells: vi.fn(),
      showNoRowsOverlay: vi.fn(),
    };
    const props = renderCompare({ gridApiRef: { current: { api } } });
    return { api, props };
  };
  const failPage = async (startRow) => {
    const handlers = { success: vi.fn(), fail: vi.fn() };
    await act(async () => {
      await grid.props.serverSideDatasource.getRows({
        request: { startRow, endRow: startRow + 10 },
        ...handlers,
      });
    });
    return handlers;
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts over from the first block with a fresh comparison", async () => {
    const { api, props } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    expect(props.compareId.current).toBe(EXPIRED_ID);
    api.refreshServerSide.mockClear();
    api.showNoRowsOverlay.mockClear();

    post.mockImplementationOnce(() => Promise.reject(notFound()));
    const expired = await failPage(10);

    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      expect.objectContaining({
        current_page_index: 1,
        compare_id: EXPIRED_ID,
      }),
    );
    expect(expired.success).not.toHaveBeenCalled();
    expect(expired.fail).toHaveBeenCalledTimes(1);
    expect(props.compareId.current).toBeNull();
    expect(api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({
      purge: true,
    });
    expect(api.showNoRowsOverlay).not.toHaveBeenCalled();

    // The purged grid asks for the first block again, without the old id.
    post.mockImplementationOnce(() => Promise.resolve(withCompareId(FRESH_ID)));
    const success = await getPage();
    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      expect.objectContaining({ current_page_index: 0, compare_id: null }),
    );
    expect(success).toHaveBeenCalledExactlyOnceWith({
      rowData: rows.slice(0, 10),
      rowCount: 12,
    });
    expect(props.compareId.current).toBe(FRESH_ID);
    expect(deleteCompare).not.toHaveBeenCalled();
  });

  it("ignores a late block of the comparison that was already replaced", async () => {
    const { api, props } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    api.refreshServerSide.mockClear();

    const second = deferred();
    const third = deferred();
    post
      .mockImplementationOnce(() => second.promise)
      .mockImplementationOnce(() => third.promise);
    const block1 = startPage(10);
    const block2 = startPage(20);
    await act(async () => {
      second.reject(notFound());
      await block1.pending;
    });
    await act(async () => {
      third.reject(notFound());
      await block2.pending;
    });

    expect(block1.fail).toHaveBeenCalledTimes(1);
    expect(block2.fail).toHaveBeenCalledTimes(1);
    expect(block2.success).not.toHaveBeenCalled();
    expect(api.refreshServerSide).toHaveBeenCalledTimes(1);
    expect(props.compareId.current).toBeNull();
  });

  it("shows the empty grid when the fresh comparison is missing too", async () => {
    const { api } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    post.mockImplementationOnce(() => Promise.reject(notFound()));
    await failPage(10);
    post.mockImplementationOnce(() => Promise.resolve(withCompareId(FRESH_ID)));
    await getPage();
    api.refreshServerSide.mockClear();

    post.mockImplementationOnce(() => Promise.reject(notFound()));
    const again = await failPage(10);

    // Only one restart per selection, so a comparison that cannot be read
    // back never loops.
    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(again.fail).not.toHaveBeenCalled();
    expect(again.success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
  });

  it("does not start over for a 404 with another error code", async () => {
    const { api, props } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    api.refreshServerSide.mockClear();

    post.mockImplementationOnce(() =>
      Promise.reject({
        statusCode: 404,
        code: "not_found",
        result: "Not found.",
      }),
    );
    const failed = await failPage(10);

    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(failed.fail).not.toHaveBeenCalled();
    expect(failed.success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(props.compareId.current).toBe(EXPIRED_ID);
  });

  it("shows the empty grid when a compared dataset was deleted", async () => {
    const { api, props } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    api.refreshServerSide.mockClear();

    // The comparison is no longer served once one of its datasets is gone,
    // so the next block starts over once ...
    post.mockImplementationOnce(() => Promise.reject(notFound()));
    await failPage(10);
    expect(api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({
      purge: true,
    });
    api.refreshServerSide.mockClear();

    // ... and the fresh comparison reports the missing dataset, which is
    // shown as an empty grid rather than rebuilt again.
    post.mockImplementationOnce(() =>
      Promise.reject({
        statusCode: 404,
        code: "not_found",
        result: "Dataset not found.",
      }),
    );
    const fresh = await failPage(0);

    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      expect.objectContaining({ current_page_index: 0, compare_id: null }),
    );
    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(fresh.fail).not.toHaveBeenCalled();
    expect(fresh.success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(props.compareId.current).toBeNull();
  });

  it("keeps the comparison and asks to try again when its storage is unavailable", async () => {
    const { api, props } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    api.refreshServerSide.mockClear();
    api.showNoRowsOverlay.mockClear();

    post.mockImplementationOnce(() =>
      Promise.reject({
        statusCode: 503,
        code: "compare_storage_unavailable",
        result: "Comparison data is temporarily unavailable. Please try again.",
      }),
    );
    const failed = await failPage(10);

    // Not rebuilt, not emptied: the block fails and the user is told.
    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(failed.success).not.toHaveBeenCalled();
    expect(failed.fail).toHaveBeenCalledTimes(1);
    expect(props.compareId.current).toBe(EXPIRED_ID);
    expect(snackbar.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
      "Comparison data is temporarily unavailable. Please try again.",
      { variant: "error" },
    );

    // The next request still reads the same comparison.
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    expect(post).toHaveBeenLastCalledWith(
      "/model-hub/datasets/dataset-a/compare-datasets/",
      expect.objectContaining({ compare_id: EXPIRED_ID }),
    );
  });

  it("does not start over for other failures", async () => {
    const { api, props } = renderWithGrid();
    post.mockImplementationOnce(() =>
      Promise.resolve(withCompareId(EXPIRED_ID)),
    );
    await getPage();
    api.refreshServerSide.mockClear();

    post.mockImplementationOnce(() =>
      Promise.reject({
        statusCode: 400,
        result: "Unable to compare datasets.",
      }),
    );
    const failed = await failPage(10);

    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(failed.success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(props.compareId.current).toBe(EXPIRED_ID);
  });
});
