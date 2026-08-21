import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

const { getMock, gridState, resetMetricIds } = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { api: null, props: null },
  resetMetricIds: vi.fn(),
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
  useAgTheme: () => ({ withParams: () => ({}) }),
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
    useTraceGridStore: { setState: vi.fn() },
    useSpanGridStore: { setState: vi.fn() },
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

const makeParams = (startRow = 0, endRow = startRow + 25) => ({
  request: { startRow, endRow, sortModel: [] },
  api: {
    deselectAll: vi.fn(),
    forEachNode: vi.fn(),
    hideOverlay: vi.fn(),
    refreshServerSide: vi.fn(),
    retryServerSideLoads: vi.fn(),
    showNoRowsOverlay: vi.fn(),
  },
  success: vi.fn(),
  fail: vi.fn(),
});

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

  it("shows replacement loading only after AG Grid starts that read", async () => {
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
    view.rerender(renderSubject([{ column_id: "status" }]));
    await waitFor(() =>
      expect(gridState.props.serverSideDatasource).not.toBe(initialDataSource),
    );
    expect(gridState.props.loading).toBe(false);

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
