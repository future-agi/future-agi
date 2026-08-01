import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "src/utils/test-utils";

const { getMock, enqueueSnackbarMock, successMock, gridApi } = vi.hoisted(
  () => ({
    getMock: vi.fn(),
    enqueueSnackbarMock: vi.fn(),
    successMock: vi.fn(),
    gridApi: {
      hideOverlay: vi.fn(),
      showNoRowsOverlay: vi.fn(),
      forEachNode: vi.fn(),
    },
  }),
);

function MockAgGridReact({ serverSideDatasource }) {
  React.useEffect(() => {
    serverSideDatasource?.getRows?.({
      request: { startRow: 0, endRow: 25 },
      api: gridApi,
      success: successMock,
      fail: vi.fn(),
    });
  }, [serverSideDatasource]);
  return <div data-testid="ag-grid" />;
}

MockAgGridReact.propTypes = {
  serverSideDatasource: PropTypes.object,
};

vi.mock("ag-grid-react", () => ({ AgGridReact: MockAgGridReact }));
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("notistack", () => ({ enqueueSnackbar: enqueueSnackbarMock }));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: {
      getTracesForObserveProject: () => "/tracer/trace/list_traces_of_session/",
      getSpansForObserveProject: () =>
        "/tracer/observation-span/list_spans_observe/",
    },
  },
}));
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgTheme: () => ({ withParams: () => ({}) }),
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => ["day"],
}));
vi.mock("src/routes/hooks", () => ({
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "viewer" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE_EDIT_PROJECT: "edit" },
  RolePermission: { OBSERVABILITY: { edit: { viewer: false } } },
}));
vi.mock("src/sections/agents/store", () => ({
  useShallowToggleAnnotationsStore: (selector) =>
    selector({ showMetricsIds: false, reset: vi.fn() }),
}));
vi.mock("src/sections/projects/SessionsView/ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      currentStep: 0,
      validatedSteps: [],
    }),
}));
vi.mock(
  "src/sections/projects/SessionsView/ReplaySessions/configurations",
  () => ({ REPLAY_MODULES: { TRACES: "traces" } }),
);
vi.mock("../states", () => {
  const useTraceGridStore = () => ({});
  const useSpanGridStore = () => ({});
  useTraceGridStore.setState = vi.fn();
  useSpanGridStore.setState = vi.fn();
  return {
    useTraceGridStore,
    useSpanGridStore,
    useLLMTracingStoreShallow: (selector) =>
      selector({
        traceDetailDrawerOpen: null,
        setTraceDetailDrawerOpen: vi.fn(),
        setSpanDetailDrawerOpen: vi.fn(),
        setVisibleTraceIds: vi.fn(),
      }),
  };
});
vi.mock("../common", () => ({
  AllowedGroups: [],
  applyQuickFilters: () => vi.fn(),
  TRACE_DEFAULT_COLUMNS: [],
  SPAN_DEFAULT_COLUMNS: [],
  getTraceListColumnDefs: () => ({}),
  FILTER_FOR_HAS_EVAL: {},
  generateAnnotationColumnsForTracing: () => [],
  mergeCellStyle: (_column, overrides) => overrides,
  normalizeConfigKeys: (config) => config,
  toBackendFilters: (filters) => filters,
}));
vi.mock("../Renderers/common", () => ({
  RENDERER_CONFIG: { tagColumns: [], nameColumns: [] },
}));
vi.mock("../Renderers", () => ({ NameCell: () => null }));
vi.mock("../Renderers/CustomTraceRenderer", () => ({ default: () => null }));
vi.mock("../Renderers/CustomTraceHeaderRenderer", () => ({
  default: () => null,
}));
vi.mock("../Renderers/IPOPCell", () => ({ default: () => null }));
vi.mock("../Renderers/IPOPTooltipComponent", () => ({
  default: () => null,
}));
vi.mock("../LLMTracingTraceDetailDrawer", () => ({ default: () => null }));
vi.mock("../LLMTracingSpanDetailDrawer", () => ({ default: () => null }));
vi.mock(
  "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover",
  () => ({ default: () => null }),
);
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: () => null,
}));
vi.mock("src/components/run-insights/traces-tab/common", () => ({
  statusBar: {},
}));
vi.mock("src/sections/projects/UsersView/common", () => ({
  userTraceRowHeightMapping: { Short: { height: 40 } },
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { observeSpanidClicked: "observe-span-clicked" },
  trackEvent: vi.fn(),
}));

import SpanGrid from "../SpanGrid";
import TraceGrid from "../TraceGrid";

const sharedProps = {
  columns: [],
  filters: [],
  extraFilters: [],
  metricFilters: [],
  setColumns: vi.fn(),
  setFilters: vi.fn(),
  setExtraFilters: vi.fn(),
  setFilterOpen: vi.fn(),
  setLoading: vi.fn(),
  hasEvalFilter: false,
  cellHeight: "Short",
};

describe("Observe list grids degraded response contract", () => {
  beforeEach(() => {
    getMock.mockReset();
    enqueueSnackbarMock.mockReset();
    successMock.mockReset();
    delete gridApi.totalRowCount;
    delete gridApi.totalRowCountIsLowerBound;
  });

  it("surfaces a safe warning for an incomplete trace-list response", async () => {
    getMock.mockResolvedValue({
      data: {
        result: {
          metadata: {
            total_rows: 0,
            query_complete: false,
            query_status: "degraded",
            query_error_code: "read_budget_exceeded",
            internal_detail: "DB::Exception: private ClickHouse stack",
          },
          table: [],
          config: [],
        },
      },
    });

    render(<TraceGrid {...sharedProps} projectId="project-1" />);

    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        "Some matching traces could not be loaded. Narrow the time range and retry.",
        { variant: "warning" },
      ),
    );
    expect(JSON.stringify(enqueueSnackbarMock.mock.calls)).not.toContain(
      "DB::Exception",
    );
  });

  it("surfaces a safe warning for a degraded span-list response", async () => {
    getMock.mockResolvedValue({
      data: {
        result: {
          metadata: {
            total_rows: 0,
            query_complete: false,
            query_status: "degraded",
            query_error_code: "read_budget_exceeded",
            internal_detail: "DB::Exception: private ClickHouse stack",
          },
          table: [],
          config: [],
        },
      },
    });

    render(<SpanGrid {...sharedProps} />);

    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        "Some matching spans could not be loaded. Narrow the time range and retry.",
        { variant: "warning" },
      ),
    );
    expect(JSON.stringify(enqueueSnackbarMock.mock.calls)).not.toContain(
      "DB::Exception",
    );
  });

  it.each([
    ["trace", TraceGrid, { projectId: "project-1" }],
    ["span", SpanGrid, {}],
  ])(
    "uses explicit has_more for a full final %s page and preserves lower-bound state",
    async (_kind, Grid, extraProps) => {
      getMock.mockResolvedValue({
        data: {
          result: {
            metadata: {
              total_rows: 25,
              total_rows_is_lower_bound: true,
              has_more: false,
            },
            table: Array.from({ length: 25 }, (_, index) => ({ id: index })),
            config: [],
          },
        },
      });

      render(<Grid {...sharedProps} {...extraProps} />);

      await waitFor(() =>
        expect(successMock).toHaveBeenCalledWith(
          expect.objectContaining({ rowCount: 25 }),
        ),
      );
      expect(gridApi.totalRowCount).toBe(25);
      expect(gridApi.totalRowCountIsLowerBound).toBe(true);
    },
  );
});
