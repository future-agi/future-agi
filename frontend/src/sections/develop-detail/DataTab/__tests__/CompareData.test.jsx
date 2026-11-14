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

describe("CompareData expired comparison", () => {
  const EXPIRED_ID = "00000000-0000-4000-8000-00000000000c";
  const FRESH_ID = "00000000-0000-4000-8000-00000000000d";
  // CompareDatasetsView answers a compare_id it cannot resolve with 404 and
  // the error code compare_not_found.
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
