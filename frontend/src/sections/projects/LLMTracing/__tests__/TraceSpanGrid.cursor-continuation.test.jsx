import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

const {
  getMock,
  gridState,
  resetMetricIds,
  themeParamReferences,
  traceGridSetState,
  spanGridSetState,
} = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { api: null, props: null },
  resetMetricIds: vi.fn(),
  themeParamReferences: [],
  traceGridSetState: vi.fn(),
  spanGridSetState: vi.fn(),
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, ref) {
      gridState.props = props;
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          get api() {
            return gridState.api;
          },
        }),
        [],
      );
      return <div data-testid="list-grid" />;
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: (params) => {
    themeParamReferences.push(params);
    return {};
  },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: {
      getTracesForObserveProject: () => "/traces/list/",
      getSpansForObserveProject: () => "/spans/list/",
    },
  },
}));
vi.mock("src/utils/utils", () => ({
  getRandomId: () => "column",
  safeParse: (value) => value,
}));
vi.mock("src/routes/hooks", () => ({
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => ["day", vi.fn()],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "viewer" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE_EDIT_PROJECT: "edit" },
  RolePermission: { OBSERVABILITY: { edit: { viewer: false } } },
}));
vi.mock("src/utils/constants", () => ({
  APP_CONSTANTS: { AG_GRID_SELECTION_COLUMN: "ag-Grid-SelectionColumn" },
}));
vi.mock(
  "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: (content) => content,
}));
vi.mock("src/components/run-insights/traces-tab/common", () => ({
  statusBar: {},
}));
vi.mock("src/components/table/utils", () => ({
  isCellValueEmpty: (value) => value == null || value === "",
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { observeSpanidClicked: "span" },
  trackEvent: vi.fn(),
}));
vi.mock("../../UsersView/common", () => ({
  userTraceRowHeightMapping: { Short: { height: 40 } },
}));
vi.mock("../../SessionsView/ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      currentStep: 0,
      validatedSteps: [],
    }),
}));
vi.mock("../../SessionsView/ReplaySessions/configurations", () => ({
  REPLAY_MODULES: { TRACES: "traces" },
}));
vi.mock("../../../agents/store", () => ({
  useShallowToggleAnnotationsStore: (selector) =>
    selector({ showMetricsIds: [], reset: resetMetricIds }),
}));
vi.mock("../states", () => {
  const traceState = {
    traceDetailDrawerOpen: null,
    setTraceDetailDrawerOpen: vi.fn(),
    setVisibleTraceIds: vi.fn(),
    setSpanDetailDrawerOpen: vi.fn(),
  };
  return {
    useLLMTracingStoreShallow: (selector) => selector(traceState),
    useTraceGridStore: { setState: traceGridSetState },
    useSpanGridStore: { setState: spanGridSetState },
  };
});
vi.mock("../common", () => ({
  AllowedGroups: [],
  FILTER_FOR_HAS_EVAL: {},
  SPAN_DEFAULT_COLUMNS: [],
  TRACE_DEFAULT_COLUMNS: [],
  applyQuickFilters: () => vi.fn(),
  generateAnnotationColumnsForTracing: () => [],
  getTraceListColumnDefs: (column) => ({ field: column.id }),
  mergeCellStyle: () => ({}),
  normalizeConfigKeys: (config) => config || [],
  toBackendFilters: (filters) => filters,
}));
vi.mock("../Renderers/common", () => ({
  RENDERER_CONFIG: { nameColumns: [], tagColumns: [] },
}));
vi.mock("../Renderers", () => ({ NameCell: () => null }));
vi.mock("../Renderers/CustomTraceRenderer", () => ({
  default: () => null,
}));
vi.mock("../Renderers/CustomTraceHeaderRenderer", () => ({
  default: () => null,
}));
vi.mock("../Renderers/IPOPTooltipComponent", () => ({
  default: () => null,
}));
vi.mock("../Renderers/IPOPCell", () => ({ default: () => null }));
vi.mock("../LLMTracingTraceDetailDrawer", () => ({ default: () => null }));
vi.mock("../LLMTracingSpanDetailDrawer", () => ({ default: () => null }));

import SpanGrid from "../SpanGrid";
import TraceGrid from "../TraceGrid";
import { paintedGridRowSignature } from "../useCursorGridPagination";
import {
  OBSERVE_LIST_REFRESH_EVENT,
  OBSERVE_PAGE_CHANGED_EVENT,
} from "../../observeEvents";

const listResponse = ({
  rows = [],
  hasMore = false,
  nextCursor = null,
  totalRows = rows.length,
  lowerBound = false,
} = {}) => ({
  data: {
    status: true,
    result: {
      config: [],
      table: rows,
      metadata: {
        has_more: hasMore,
        next_cursor: nextCursor,
        total_rows: totalRows,
        total_rows_is_lower_bound: lowerBound,
      },
      query_complete: !hasMore,
      query_status: hasMore ? "degraded" : "complete",
    },
  },
});

const makeParams = (startRow = 0, endRow = startRow + 25) => {
  let currentPage = Math.floor(startRow / (endRow - startRow));
  let renderedNodes = [];
  let paintedRows = true;
  let paintedSignature = `page-${currentPage + 1}`;
  const api = {
    deselectAll: vi.fn(),
    forEachNode: vi.fn(),
    getGui: vi.fn(() => ({
      querySelectorAll: vi.fn(() =>
        paintedRows
          ? [
              {
                getAttribute: vi.fn(() => "0"),
                textContent: paintedSignature,
              },
            ]
          : [],
      ),
    })),
    getRenderedNodes: vi.fn(() => renderedNodes),
    hideOverlay: vi.fn(),
    paginationGetCurrentPage: vi.fn(() => currentPage),
    paginationGoToFirstPage: vi.fn(),
    paginationGoToPage: vi.fn((nextPage) => {
      currentPage = nextPage;
    }),
    refreshServerSide: vi.fn(),
    retryServerSideLoads: vi.fn(),
    showNoRowsOverlay: vi.fn(),
    setPaintedRows: (nextPaintedRows) => {
      paintedRows = nextPaintedRows;
      if (nextPaintedRows) paintedSignature = `page-${currentPage + 1}`;
    },
    setPaintedText: (text) => {
      paintedRows = true;
      paintedSignature = text;
    },
  };
  return {
    request: { startRow, endRow, sortModel: [] },
    api,
    success: vi.fn(({ rowData = [] }) => {
      renderedNodes = rowData.map((data, index) => ({
        data,
        id: data.span_id ?? data.trace_id ?? String(startRow + index),
      }));
      paintedSignature = `page-${currentPage + 1}`;
    }),
    fail: vi.fn(),
  };
};

const baseProps = () => ({
  columns: [],
  filters: [{ column_id: "created_at" }],
  extraFilters: [],
  metricFilters: [],
  hasEvalFilter: false,
  cellHeight: "Short",
  setColumns: vi.fn(),
  setExtraFilters: vi.fn(),
  setFilterOpen: vi.fn(),
  setFilters: vi.fn(),
  setLoading: vi.fn(),
});

const renderGrid = (kind) => {
  const ref = React.createRef();
  const props = baseProps();
  if (kind === "trace") {
    render(<TraceGrid ref={ref} {...props} projectId="project-1" />);
  } else {
    render(<SpanGrid ref={ref} {...props} />);
  }
  return props;
};

const getRows = async (params) => {
  gridState.api = params.api;
  await act(async () => {
    await gridState.props.serverSideDatasource.getRows(params);
  });
};

const selectionApi = () => ({
  deselectAll: vi.fn(),
  forEachNode: vi.fn(),
  getSelectedNodes: vi.fn(() => []),
  getServerSideSelectionState: vi.fn(() => ({
    selectAll: true,
    toggledNodes: ["excluded-row"],
  })),
  hideOverlay: vi.fn(),
  refreshServerSide: vi.fn(),
  retryServerSideLoads: vi.fn(),
  selectAll: vi.fn(),
  setServerSideSelectionState: vi.fn(),
  showNoRowsOverlay: vi.fn(),
});

const renderGridSubject = ({ kind, ref, props, filters }) =>
  kind === "trace" ? (
    <TraceGrid
      ref={ref}
      {...props}
      filters={filters}
      projectId="project-1"
      compareType="primary"
    />
  ) : (
    <SpanGrid ref={ref} {...props} filters={filters} compareType="primary" />
  );

describe.each(["trace", "span"])("%s grid theme retention", (kind) => {
  beforeEach(() => {
    themeParamReferences.length = 0;
  });

  it("reuses one AG Grid theme parameter object across rerenders", () => {
    const ref = React.createRef();
    const props = baseProps();
    const filters = [{ column_id: "created_at" }];
    const subject = renderGridSubject({ kind, ref, props, filters });
    const { rerender } = render(subject);
    const firstParams = themeParamReferences.at(-1);

    rerender(renderGridSubject({ kind, ref, props, filters }));

    expect(firstParams).toBeDefined();
    expect(themeParamReferences.at(-1)).toBe(firstParams);
  });
});

describe("painted grid row detection", () => {
  it("uses the owning grid element when AG Grid does not expose getGui", () => {
    const gridElement = document.createElement("div");
    gridElement.innerHTML = `
      <div class="ag-center-cols-container">
        <div class="ag-row" row-index="0" row-id="trace-1">Trace one</div>
      </div>
    `;

    expect(paintedGridRowSignature({}, { current: gridElement })).toContain(
      "trace-1:Trace one",
    );

    gridElement.querySelector(".ag-row").textContent = "   ";
    expect(paintedGridRowSignature({}, { current: gridElement })).toBeNull();
  });

  it("prefers the visible owning grid over a stale AG Grid GUI", () => {
    const staleGridElement = document.createElement("div");
    staleGridElement.innerHTML = `
      <div class="ag-center-cols-container">
        <div class="ag-row" row-index="0" row-id="trace-old">Old page</div>
      </div>
    `;
    const visibleGridElement = document.createElement("div");
    visibleGridElement.innerHTML = `
      <div class="ag-center-cols-container">
        <div class="ag-row" row-index="0" row-id="trace-new">   </div>
      </div>
    `;
    const api = { getGui: vi.fn(() => staleGridElement) };

    expect(
      paintedGridRowSignature(api, { current: visibleGridElement }),
    ).toBeNull();
    expect(api.getGui).not.toHaveBeenCalled();

    visibleGridElement.querySelector(".ag-row").textContent = "New page";
    expect(
      paintedGridRowSignature(api, { current: visibleGridElement }),
    ).toContain("trace-new:New page");
  });
});

describe.each(["trace", "span"])("%s grid explicit pagination", (kind) => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    resetMetricIds.mockReset();
  });

  it("loads 25 rows by default and advances only from page controls", async () => {
    const pageChanged = vi.fn();
    window.addEventListener(OBSERVE_PAGE_CHANGED_EVENT, pageChanged, {
      once: true,
    });
    const rows = Array.from({ length: 25 }, (_, index) =>
      kind === "trace"
        ? { trace_id: `trace-${index}`, project_id: "project-1" }
        : {
            span_id: `span-${index}`,
            trace_id: `trace-${index}`,
            project_id: "project-1",
            start_time: `2026-08-08T00:00:${String(index).padStart(2, "0")}Z`,
          },
    );
    getMock.mockResolvedValueOnce(
      listResponse({
        rows,
        hasMore: true,
        nextCursor: "page-2",
        totalRows: 26,
        lowerBound: true,
      }),
    );
    const finalRows = rows.slice(0, 3).map((row, index) => ({
      ...row,
      ...(kind === "trace"
        ? { trace_id: `trace-final-${index}` }
        : { span_id: `span-final-${index}` }),
    }));
    getMock.mockResolvedValueOnce(listResponse({ rows: finalRows }));

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    expect(gridState.props.pagination).toBe(true);
    expect(gridState.props.paginationPageSize).toBe(25);
    expect(gridState.props.cacheBlockSize).toBe(25);
    expect(gridState.props.suppressPaginationPanel).toBe(true);
    expect(gridState.props.onPaginationChanged).toBeUndefined();
    expect(screen.getByLabelText("Results per page")).toHaveTextContent("25");

    const params = makeParams();
    gridState.api = params.api;
    await getRows(params);

    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock.mock.calls[0][1].params.page_size).toBe(25);
    expect(params.success).toHaveBeenCalledWith({
      rowData: rows,
      rowCount: 26,
    });

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Go to page 2" }),
      ).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    expect(params.api.paginationGoToPage).toHaveBeenCalledWith(1);
    expect(pageChanged).toHaveBeenCalledOnce();
    expect(pageChanged.mock.calls[0][0].detail).toEqual({ page: 2 });
    params.api.refreshServerSide.mockClear();
    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(screen.getByRole("button", { name: "page 2" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByRole("button", { name: "page 2" })).toBeDisabled();
    // The page button itself does not prefetch; AG Grid requests page two only
    // after performing the explicit pagination transition.
    expect(getMock).toHaveBeenCalledTimes(1);

    const finalPageParams = makeParams(25, 50);
    await getRows(finalPageParams);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(finalPageParams.success).toHaveBeenCalledWith({
      rowData: finalRows,
      rowCount: 28,
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "page 2" })).toHaveAttribute(
        "aria-current",
        "true",
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("Loading page…")).not.toBeInTheDocument(),
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Go to previous page" }),
    );
    expect(finalPageParams.api.paginationGoToPage).toHaveBeenCalledWith(0);
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(getMock).toHaveBeenCalledTimes(2);

    // Returning to a cached page does not invoke the datasource again. The
    // transition still settles from the actual rendered row swap.
    finalPageParams.api.getRenderedNodes.mockReturnValue(
      rows.map((data) => ({
        data,
        id: data.span_id ?? data.trace_id,
      })),
    );
    finalPageParams.api.setPaintedRows(true);
    await waitFor(() =>
      expect(screen.queryByText("Loading page…")).not.toBeInTheDocument(),
    );
  });

  it("shows a loading state while an explicitly requested page is pending", async () => {
    const firstPageRows = Array.from({ length: 25 }, (_, index) =>
      kind === "trace"
        ? { trace_id: `trace-${index}`, project_id: "project-1" }
        : {
            span_id: `span-${index}`,
            trace_id: `trace-${index}`,
            project_id: "project-1",
            start_time: `2026-08-08T00:00:${String(index).padStart(2, "0")}Z`,
          },
    );
    let resolveSecondPage;
    getMock
      .mockResolvedValueOnce(
        listResponse({
          rows: firstPageRows,
          hasMore: true,
          nextCursor: "page-2",
          totalRows: 26,
          lowerBound: true,
        }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecondPage = resolve;
          }),
      );

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams();
    await getRows(firstPage);
    await userEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(screen.getByRole("button", { name: "page 2" })).toBeDisabled();

    const secondPage = makeParams(25, 50);
    gridState.api = secondPage.api;
    let pendingRead;
    await act(async () => {
      pendingRead = gridState.props.serverSideDatasource.getRows(secondPage);
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
      expect(gridState.props.loading).toBe(false);
    });
    expect(screen.getByRole("button", { name: "page 2" })).toBeDisabled();

    const renderedSecondPageRow =
      kind === "trace"
        ? { trace_id: "trace-page-2", project_id: "project-1" }
        : {
            span_id: "span-page-2",
            trace_id: "trace-page-2",
            project_id: "project-1",
            start_time: "2026-08-08T00:01:00Z",
          };
    // AG Grid may expose speculative target RowNodes before the datasource
    // promise settles. That model state must not dismiss the visible loader.
    secondPage.api.getRenderedNodes.mockReturnValue([
      {
        id: renderedSecondPageRow.span_id ?? renderedSecondPageRow.trace_id,
        data: renderedSecondPageRow,
      },
    ]);
    secondPage.api.setPaintedRows(true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");

    // Model the real AG Grid handoff: the API response can settle before its
    // target row nodes replace the previous page in the viewport.
    secondPage.success.mockImplementation(() => {});
    secondPage.api.setPaintedRows(false);
    resolveSecondPage(listResponse({ rows: [renderedSecondPageRow] }));
    await act(async () => pendingRead);

    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(gridState.props.loading).toBe(false);

    secondPage.api.getRenderedNodes.mockReturnValue([
      {
        id: renderedSecondPageRow.span_id ?? renderedSecondPageRow.trace_id,
        data: renderedSecondPageRow,
      },
    ]);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");

    // AG Grid briefly retains row shells whose cells have been cleared. Empty
    // shells are not a painted target page and must not dismiss the loader.
    secondPage.api.setPaintedText("   ");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");

    secondPage.api.setPaintedRows(true);
    await waitFor(() => {
      expect(screen.queryByText("Loading page…")).not.toBeInTheDocument();
      expect(gridState.props.loading).toBe(false);
    });
  });

  it("offers a bounded page-size selector and resets to 50 rows", async () => {
    getMock.mockResolvedValueOnce(listResponse({ rows: [] }));
    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());
    await getRows(makeParams());

    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(screen.getByRole("option", { name: "50" }));

    await waitFor(() => {
      expect(gridState.props.paginationPageSize).toBe(50);
      expect(gridState.props.cacheBlockSize).toBe(50);
    });
    // The selector remounts the grid at page one; the mocked grid does not
    // auto-request, so no request beyond the settled 25-row page is made here.
    expect(getMock).toHaveBeenCalledTimes(1);
  });
});

describe.each([
  { kind: "trace", storeSetState: traceGridSetState },
  { kind: "span", storeSetState: spanGridSetState },
])("$kind grid query-bound selection", ({ kind, storeSetState }) => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    resetMetricIds.mockReset();
    traceGridSetState.mockReset();
    spanGridSetState.mockReset();
  });

  it("clears select-all exclusions when the list query changes", async () => {
    const ref = React.createRef();
    const props = baseProps();
    const filtersA = [
      {
        column_id: "status",
        filter_config: { filter_op: "equals", filter_value: "error" },
      },
    ];
    const filtersB = [
      {
        column_id: "status",
        filter_config: { filter_op: "equals", filter_value: "ok" },
      },
    ];
    const api = selectionApi();
    const view = render(
      renderGridSubject({ kind, ref, props, filters: filtersA }),
    );
    await waitFor(() => expect(gridState.props).not.toBeNull());
    gridState.api = api;

    act(() => {
      gridState.props.onColumnHeaderClicked({
        api,
        column: { colId: "ag-Grid-SelectionColumn" },
      });
      gridState.props.onSelectionChanged({ api });
    });
    expect(api.selectAll).toHaveBeenCalledOnce();
    expect(storeSetState).toHaveBeenLastCalledWith({
      toggledNodes: ["excluded-row"],
      selectAll: true,
    });

    storeSetState.mockClear();
    view.rerender(renderGridSubject({ kind, ref, props, filters: filtersB }));

    await waitFor(() => expect(api.deselectAll).toHaveBeenCalledOnce());
    expect(api.setServerSideSelectionState).toHaveBeenCalledWith({
      selectAll: false,
      toggledNodes: [],
    });
    expect(storeSetState).toHaveBeenCalledWith({
      selectAll: false,
      toggledNodes: [],
    });

    api.selectAll.mockClear();
    act(() => {
      gridState.props.onColumnHeaderClicked({
        api,
        column: { colId: "ag-Grid-SelectionColumn" },
      });
    });
    expect(api.selectAll).toHaveBeenCalledOnce();
  });

  it("preserves selection while the same query advances its cursor", async () => {
    const rows = Array.from({ length: 25 }, (_, index) =>
      kind === "trace"
        ? {
            trace_id: `trace-${index}`,
            project_id: "project-1",
          }
        : {
            span_id: `span-${index}`,
            trace_id: `trace-${index}`,
            project_id: "project-1",
            start_time: `2026-08-08T00:00:${String(index).padStart(2, "0")}Z`,
          },
    );
    const nextRow =
      kind === "trace"
        ? { trace_id: "trace-25", project_id: "project-1" }
        : {
            span_id: "span-25",
            trace_id: "trace-25",
            project_id: "project-1",
            start_time: "2026-08-08T00:00:25Z",
          };
    getMock
      .mockResolvedValueOnce(
        listResponse({
          rows,
          hasMore: true,
          nextCursor: "same-query-page-2",
          totalRows: 26,
          lowerBound: true,
        }),
      )
      .mockResolvedValueOnce(listResponse({ rows: [nextRow], totalRows: 26 }));

    const ref = React.createRef();
    const props = baseProps();
    render(renderGridSubject({ kind, ref, props, filters: props.filters }));
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const api = selectionApi();
    gridState.api = api;
    act(() => gridState.props.onSelectionChanged({ api }));
    storeSetState.mockClear();

    const firstPage = makeParams(0, 25);
    firstPage.api = api;
    await getRows(firstPage);
    // Cursor pages are sequential. Settling page one must not issue an eager
    // page-two request while the grid is idle.
    expect(getMock).toHaveBeenCalledTimes(1);

    const secondPage = makeParams(25, 50);
    secondPage.api = api;
    await getRows(secondPage);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        cursor: "same-query-page-2",
        cursor_mode: true,
      }),
    );
    expect(api.deselectAll).not.toHaveBeenCalled();
    expect(api.setServerSideSelectionState).not.toHaveBeenCalled();
    expect(storeSetState).not.toHaveBeenCalledWith({
      selectAll: false,
      toggledNodes: [],
    });
  });
});

describe.each([
  {
    kind: "trace",
    endpoint: "/traces/list/",
    row: { trace_id: "trace-88", project_id: "project-1" },
    emptyText: "No traces found",
  },
  {
    kind: "span",
    endpoint: "/spans/list/",
    row: {
      span_id: "span-88",
      trace_id: "trace-88",
      project_id: "project-1",
      start_time: "2026-08-08T00:00:00Z",
    },
    emptyText: "No spans found",
  },
])("$kind grid cursor continuation", ({ kind, endpoint, row, emptyText }) => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    resetMetricIds.mockReset();
  });

  it("shares simultaneous reads for one block and stays idle after settlement", async () => {
    const rows = Array.from({ length: 25 }, (_, index) =>
      kind === "trace"
        ? { trace_id: `trace-${index}`, project_id: "project-1" }
        : {
            span_id: `span-${index}`,
            trace_id: `trace-${index}`,
            project_id: "project-1",
            start_time: `2026-08-08T00:00:${String(index).padStart(2, "0")}Z`,
          },
    );
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const first = makeParams();
    const duplicate = makeParams();
    let firstRead;
    let duplicateRead;
    act(() => {
      firstRead = gridState.props.serverSideDatasource.getRows(first);
      duplicateRead = gridState.props.serverSideDatasource.getRows(duplicate);
    });

    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveResponse(
        listResponse({
          rows,
          hasMore: true,
          nextCursor: "next-block",
          totalRows: 26,
          lowerBound: true,
        }),
      );
      await Promise.all([firstRead, duplicateRead]);
    });

    expect(first.success).toHaveBeenCalledOnce();
    expect(duplicate.success).toHaveBeenCalledOnce();
    expect(first.fail).not.toHaveBeenCalled();
    expect(duplicate.fail).not.toHaveBeenCalled();
    // No eager cursor prefetch means an idle grid cannot start a request loop.
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(gridState.props.maxConcurrentDatasourceRequests).toBe(1);
    expect(gridState.props.maxBlocksInCache).toBe(5);
  });

  it("drops a response after its grid is destroyed", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    let destroyed = false;
    params.api.isDestroyed = () => destroyed;
    const read = gridState.props.serverSideDatasource.getRows(params);
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    destroyed = true;
    await act(async () => {
      resolveResponse(listResponse({ rows: [row] }));
      await read;
    });

    expect(params.success).not.toHaveBeenCalled();
    expect(params.fail).not.toHaveBeenCalled();
    expect(params.api.forEachNode).not.toHaveBeenCalled();
  });

  it("pauses neutrally and resumes the retained checkpoint after one click", async () => {
    Array.from({ length: 13 }, (_, index) =>
      listResponse({
        hasMore: true,
        nextCursor: `checkpoint-${index}`,
        lowerBound: true,
      }),
    ).forEach((response) => getMock.mockResolvedValueOnce(response));
    getMock.mockResolvedValueOnce(listResponse({ rows: [row] }));

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const boundedRound = makeParams();
    await getRows(boundedRound);

    expect(getMock).toHaveBeenCalledTimes(13);
    expect(getMock.mock.calls.every(([url]) => url === endpoint)).toBe(true);
    expect(boundedRound.success).not.toHaveBeenCalled();
    expect(boundedRound.fail).toHaveBeenCalledOnce();
    expect(boundedRound.api.showNoRowsOverlay).not.toHaveBeenCalled();
    expect(boundedRound.api.retryServerSideLoads).not.toHaveBeenCalled();
    expect(boundedRound.api.refreshServerSide).not.toHaveBeenCalled();
    expect(gridState.props.className).toContain("ag-grid-cursor-paused");
    expect(gridState.props.noRowsOverlayComponent()).toBeNull();
    expect(screen.queryByText(emptyText)).not.toBeInTheDocument();
    expect(screen.queryByText("ERR")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Preparing exact results. Refresh or retry to continue.",
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );

    expect(boundedRound.api.retryServerSideLoads).toHaveBeenCalledOnce();
    expect(boundedRound.api.refreshServerSide).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
    expect(gridState.props.className).not.toContain("ag-grid-cursor-paused");

    const resumedRound = makeParams();
    await getRows(resumedRound);

    expect(getMock.mock.calls[13][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-12",
        page_size: 25,
      }),
    );
    expect(getMock.mock.calls[13][1].params).not.toHaveProperty("page_number");
    expect(resumedRound.success).toHaveBeenCalledWith({
      rowData: [row],
      rowCount: 1,
    });
    expect(resumedRound.api.refreshServerSide).not.toHaveBeenCalled();
  });

  it("retries the first page once as numbered against a strict legacy API", async () => {
    getMock
      .mockRejectedValueOnce({
        response: {
          status: 400,
          data: {
            attr: "cursor_mode",
            detail: "cursor_mode: Unknown field.",
            details: { cursor_mode: ["Unknown field."] },
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          status: true,
          result: {
            config: [],
            table: [row],
            metadata: { total_rows: 1 },
          },
        },
      });

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock).toHaveBeenNthCalledWith(
      1,
      endpoint,
      expect.objectContaining({
        params: expect.objectContaining({
          cursor_mode: true,
          page_number: 0,
        }),
      }),
    );
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({ page_number: 0 }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor_mode");
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor");
    expect(params.success).toHaveBeenCalledWith({
      rowData: [row],
      rowCount: 1,
    });
    expect(params.fail).not.toHaveBeenCalled();
  });
});

describe.each(["trace", "span"])("%s grid loading lifecycle", (kind) => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    resetMetricIds.mockReset();
  });

  it("settles an empty first page across an equivalent-filter rerender", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );

    const ref = React.createRef();
    const props = baseProps();
    const renderSubject = (filters) =>
      kind === "trace" ? (
        <TraceGrid
          ref={ref}
          {...props}
          filters={filters}
          projectId="project-1"
        />
      ) : (
        <SpanGrid ref={ref} {...props} filters={filters} />
      );
    const view = render(renderSubject(props.filters));
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const initialDataSource = gridState.props.serverSideDatasource;
    const params = makeParams();
    let pendingRead;
    act(() => {
      pendingRead = initialDataSource.getRows(params);
    });
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    view.rerender(renderSubject([{ column_id: "created_at" }]));

    expect(gridState.props.serverSideDatasource).toBe(initialDataSource);

    await act(async () => {
      resolveResponse(listResponse());
      await pendingRead;
    });

    await waitFor(() => expect(gridState.props.loading).toBe(false));
    expect(params.success).toHaveBeenCalledWith({
      rowData: [],
      rowCount: 0,
    });
    expect(params.fail).not.toHaveBeenCalled();
    if (kind === "trace") {
      expect(params.api.showNoRowsOverlay).not.toHaveBeenCalled();
      expect(gridState.props.noRowsOverlayComponent().props.children).toBe(
        "No traces found",
      );
    }
  });

  it("lets a replacement datasource own semantic filter refreshes", async () => {
    getMock.mockResolvedValueOnce(listResponse());

    const ref = React.createRef();
    const props = baseProps();
    const renderSubject = (filters) =>
      kind === "trace" ? (
        <TraceGrid
          ref={ref}
          {...props}
          filters={filters}
          projectId="project-1"
        />
      ) : (
        <SpanGrid ref={ref} {...props} filters={filters} />
      );
    const view = render(renderSubject(props.filters));
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const initialDataSource = gridState.props.serverSideDatasource;
    const params = makeParams();
    gridState.api = params.api;
    view.rerender(
      renderSubject([
        {
          column_id: "created_at",
          filter_config: { filter_op: "between", filter_value: [1, 2] },
        },
      ]),
    );

    await waitFor(() =>
      expect(gridState.props.serverSideDatasource).not.toBe(initialDataSource),
    );
    expect(params.api.refreshServerSide).not.toHaveBeenCalled();

    await getRows(params);

    await waitFor(() => expect(gridState.props.loading).toBe(false));
    expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
    expect(params.fail).not.toHaveBeenCalled();
  });

  it("settles a superseded latest read until its replacement starts", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );

    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    gridState.api = params.api;
    let pendingRead;
    act(() => {
      pendingRead = gridState.props.serverSideDatasource.getRows(params);
    });
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    act(() => window.dispatchEvent(new Event("observe-refresh")));
    expect(params.api.refreshServerSide).toHaveBeenCalledWith({ purge: false });

    await act(async () => {
      resolveResponse(listResponse());
      await pendingRead;
    });

    expect(params.fail).toHaveBeenCalledOnce();
    expect(params.success).not.toHaveBeenCalled();
    await waitFor(() => expect(gridState.props.loading).toBe(false));
  });

  it("shows replacement loading immediately and hands it to the first read", async () => {
    let resolveReplacement;
    getMock.mockResolvedValueOnce(listResponse()).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveReplacement = resolve;
        }),
    );

    const ref = React.createRef();
    const props = baseProps();
    const renderSubject = (filters) =>
      kind === "trace" ? (
        <TraceGrid
          ref={ref}
          {...props}
          filters={filters}
          projectId="project-1"
        />
      ) : (
        <SpanGrid ref={ref} {...props} filters={filters} />
      );
    const view = render(renderSubject(props.filters));
    await waitFor(() => expect(gridState.props).not.toBeNull());

    await getRows(makeParams());
    await waitFor(() => expect(gridState.props.loading).toBe(false));

    const initialDataSource = gridState.props.serverSideDatasource;
    view.rerender(
      renderSubject([
        {
          column_id: "conversation.transcript.0.message.content",
          col_type: "SPAN_ATTRIBUTE",
          filter_config: {
            filter_op: "in",
            filter_type: "text",
            filter_value: ["Hello"],
          },
        },
      ]),
    );
    await waitFor(() =>
      expect(gridState.props.serverSideDatasource).not.toBe(initialDataSource),
    );
    expect(gridState.props.loading).toBe(true);

    const replacementParams = makeParams();
    let replacementRead;
    act(() => {
      replacementRead =
        gridState.props.serverSideDatasource.getRows(replacementParams);
    });
    await waitFor(() => expect(resolveReplacement).toBeTypeOf("function"));
    expect(gridState.props.loading).toBe(true);

    await act(async () => {
      resolveReplacement(listResponse());
      await replacementRead;
    });
    await waitFor(() => expect(gridState.props.loading).toBe(false));
  });

  it("clears immediate replacement loading if AG Grid never starts the read", async () => {
    getMock.mockResolvedValueOnce(listResponse());

    const ref = React.createRef();
    const props = baseProps();
    const renderSubject = (filters) =>
      kind === "trace" ? (
        <TraceGrid
          ref={ref}
          {...props}
          filters={filters}
          projectId="project-1"
        />
      ) : (
        <SpanGrid ref={ref} {...props} filters={filters} />
      );
    const view = render(renderSubject(props.filters));
    await waitFor(() => expect(gridState.props).not.toBeNull());

    await getRows(makeParams());
    await waitFor(() => expect(gridState.props.loading).toBe(false));

    const initialDataSource = gridState.props.serverSideDatasource;
    view.rerender(
      renderSubject([
        {
          column_id: "prompt_slug",
          col_type: "SPAN_ATTRIBUTE",
          filter_config: {
            filter_op: "in",
            filter_type: "text",
            filter_value: ["agent"],
          },
        },
      ]),
    );
    await waitFor(() =>
      expect(gridState.props.serverSideDatasource).not.toBe(initialDataSource),
    );
    expect(gridState.props.loading).toBe(true);

    await waitFor(() => expect(gridState.props.loading).toBe(false), {
      timeout: 1500,
    });
    expect(getMock).toHaveBeenCalledOnce();
  });

  it("uses the preserve-rows path for list-only auto refresh", async () => {
    getMock.mockResolvedValueOnce(listResponse());
    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    gridState.api = params.api;
    await getRows(params);
    await waitFor(() => expect(gridState.props.loading).toBe(false));

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));

    expect(params.api.refreshServerSide).toHaveBeenCalledWith({ purge: false });
    expect(gridState.props.loading).toBe(false);
  });

  it("does not stack an auto refresh on an active list read", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );
    renderGrid(kind);
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    gridState.api = params.api;
    let pendingRead;
    act(() => {
      pendingRead = gridState.props.serverSideDatasource.getRows(params);
    });
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).not.toHaveBeenCalled();

    await act(async () => {
      resolveResponse(listResponse());
      await pendingRead;
    });
  });
});

describe("trace custom-property request pagination", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    resetMetricIds.mockReset();
  });

  it("keeps the searched property filter on p1 and its opaque p2 cursor", async () => {
    const propertyFilter = {
      column_id: "prompt_slug",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_op: "equals",
        filter_value: "rejected",
      },
    };
    const firstRows = Array.from({ length: 25 }, (_, index) => ({
      trace_id: `trace-${index + 1}`,
      project_id: "project-whatfix",
    }));
    const secondRows = [
      { trace_id: "trace-26", project_id: "project-whatfix" },
    ];
    getMock
      .mockResolvedValueOnce(
        listResponse({
          rows: firstRows,
          hasMore: true,
          nextCursor: "signed-property-page-2",
          totalRows: 26,
          lowerBound: true,
        }),
      )
      .mockResolvedValueOnce(listResponse({ rows: secondRows, totalRows: 26 }));

    const ref = React.createRef();
    render(
      <TraceGrid
        ref={ref}
        {...baseProps()}
        filters={[propertyFilter]}
        projectId="project-whatfix"
      />,
    );
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams(0, 25);
    await getRows(firstPage);
    const secondPage = makeParams(25, 50);
    await getRows(secondPage);

    const expectedFilters = JSON.stringify([propertyFilter]);
    expect(getMock.mock.calls[0][1].params).toEqual(
      expect.objectContaining({
        project_id: "project-whatfix",
        filters: expectedFilters,
        cursor_mode: true,
        page_number: 0,
        page_size: 25,
      }),
    );
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        project_id: "project-whatfix",
        filters: expectedFilters,
        cursor_mode: true,
        cursor: "signed-property-page-2",
        page_size: 25,
      }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("page_number");
    expect(firstPage.success).toHaveBeenCalledWith(
      expect.objectContaining({ rowData: firstRows }),
    );
    expect(secondPage.success).toHaveBeenCalledWith(
      expect.objectContaining({ rowData: secondRows }),
    );
    expect(
      new Set(firstRows.map(({ trace_id }) => trace_id)).has(
        secondRows[0].trace_id,
      ),
    ).toBe(false);
  });
});

describe("trace grid empty-state lifecycle", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    resetMetricIds.mockReset();
  });

  it("does not publish a false empty state while the first exact read is pending", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );

    renderGrid("trace");
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    let pendingRead;
    act(() => {
      pendingRead = gridState.props.serverSideDatasource.getRows(params);
    });
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    expect(gridState.props.loading).toBe(true);
    expect(gridState.props.noRowsOverlayComponent()).toBeNull();
    expect(params.api.showNoRowsOverlay).not.toHaveBeenCalled();

    await act(async () => {
      resolveResponse(listResponse());
      await pendingRead;
    });

    await waitFor(() => expect(gridState.props.loading).toBe(false));
    expect(gridState.props.noRowsOverlayComponent().props.children).toBe(
      "No traces found",
    );
    expect(params.api.showNoRowsOverlay).not.toHaveBeenCalled();
  });
});
