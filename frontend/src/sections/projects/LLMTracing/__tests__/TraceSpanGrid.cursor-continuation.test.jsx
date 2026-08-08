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

const makeParams = () => ({
  request: { startRow: 0, endRow: 25, sortModel: [] },
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
});
