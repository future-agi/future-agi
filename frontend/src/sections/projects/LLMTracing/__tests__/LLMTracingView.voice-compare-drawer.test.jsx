import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMTracingView from "../LLMTracingView";

// With Compare on, a voice project lists calls in two grids, one per tab. Each
// grid's call drawer steps Previous/Next through that grid's own list, so only
// the drawer of the tab the call was opened from may be mounted. Otherwise a
// call opened on the Compare tab steps through the primary list and filters.

const harness = vi.hoisted(() => {
  const compareFilters = [
    {
      column_id: "status",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "failed",
      },
    },
  ];
  return {
    emptyFilters: [],
    // Keyed on useLLMTracingFilters' URL key. Stable arrays, so no effect
    // keyed on the filters re-fires on every render.
    validatedFilters: { compareTraceFilter: compareFilters },
    observeHeader: {
      activeViewConfig: null,
      registerGetViewConfig: vi.fn(),
      setActiveViewConfig: vi.fn(),
      setHeaderConfig: vi.fn(),
    },
    callLogsGridProps: [],
    projectDetail: { source: "simulator" },
    replayState: {
      openReplaySessionDrawer: {},
      setIsReplayDrawerCollapsed: vi.fn(),
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    },
    selectedGraph: "primary",
    testDetailState: { setTestDetailDrawerOpen: vi.fn() },
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: "project-1" }),
}));

vi.mock("src/routes/hooks/use-url-state", async () => {
  const ReactModule = await import("react");
  return {
    useUrlState: (key, defaultValue) =>
      ReactModule.useState(
        {
          selectedTab: "trace",
          selectedGraph: harness.selectedGraph,
          showCompare: true,
        }[key] ?? defaultValue,
      ),
  };
});

vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => harness.observeHeader,
}));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: harness.projectDetail }),
}));

vi.mock("../useLLMTracingFilters", async () => {
  const ReactModule = await import("react");
  return {
    useLLMTracingFilters: (defaultFilters, defaultDateFilter, filterKey) => {
      const [filters, setFilters] = ReactModule.useState(defaultFilters);
      const [dateFilter, setDateFilter] =
        ReactModule.useState(defaultDateFilter);
      return {
        filters,
        setFilters,
        validatedFilters:
          harness.validatedFilters[filterKey] ?? harness.emptyFilters,
        dateFilter,
        setDateFilter,
      };
    },
  };
});

vi.mock("../states", async () => {
  const ReactModule = await import("react");
  const llmState = {
    resetStates: vi.fn(),
    viewMode: "graph",
    setViewMode: vi.fn(),
  };
  const gridState = {
    toggledNodes: [],
    selectAll: false,
    totalRowCount: 0,
    totalRowCountLowerBound: 0,
    totalRowCountIsLowerBound: false,
  };
  const subscribe = () => () => {};
  const getSnapshot = () => gridState;
  const useGrid = (selector) =>
    selector(
      ReactModule.useSyncExternalStore(subscribe, getSnapshot, getSnapshot),
    );
  return {
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) => selector(llmState),
    useTraceGridStoreShallow: useGrid,
    useSpanGridStoreShallow: useGrid,
  };
});

vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});

// CallLogsGrid mounts its own TestDetailSideDrawer unless hideDrawer is set,
// and that drawer pages through the grid's own list key, which carries the
// grid's request params. The stand-in drawer records those params' filters.
// No ref is attached: every call-logs ref read in the view is null-safe.
vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function CallLogsGrid(props, _ref) {
      harness.callLogsGridProps.push(props);
      // eslint-disable-next-line react/prop-types
      const { hideDrawer, params } = props;
      if (hideDrawer) return null;
      return <div data-testid="call-drawer" data-list={params?.filters} />;
    }),
  };
});

vi.mock("../ObserveToolbar", () => ({ default: () => null }));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueuesList: () => ({ data: { results: [] }, isLoading: false }),
  useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/sections/annotations/queues/create-queue-drawer", () => ({
  default: () => null,
}));

vi.mock("src/sections/test-detail/states", () => ({
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector(harness.testDetailState),
}));

vi.mock("src/sections/projects/UsersView/useProjectFilterField", () => ({
  default: () => null,
}));

vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: () => ({ data: [] }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));

vi.mock("src/api/project/agent-graph", () => ({
  useAgentGraph: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    pollingPaused: false,
  }),
}));

vi.mock("src/sections/projects/SessionsView/ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) => selector(harness.replayState),
  useSessionsGridStore: { getState: () => ({ setToggledNodes: vi.fn() }) },
}));

vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: {
    dashboard: { metrics: "/tracer/dashboard/metrics/" },
    project: {
      addAnnotationValuesForSpan: () => "/annotations/values/",
      getSpanGraphData: () => "/spans/graph/",
      getTrace: () => "/traces/detail/",
      getTraceGraphData: () => "/traces/graph/",
      spanAttributeKeys: () => "/traces/span-attribute-keys/",
      updateProjectColumnVisibility: () => "/projects/columns/",
    },
  },
}));

vi.mock("../GraphSection/PrimaryGraph", () => ({ default: () => null }));
vi.mock("../GraphSection/AgentGraph", () => ({ default: () => null }));
vi.mock("../GraphSection/AgentPath", () => ({ default: () => null }));
vi.mock("../SelectAllBanner", () => ({ default: () => null }));
vi.mock("../FilterChips", () => ({ default: () => null }));
vi.mock("../TracingControls", () => ({ default: () => null }));
vi.mock("../CustomColumnDialog", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/addToDataset/add-dataset", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AnnotateDrawer", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

const renderView = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <React.Suspense fallback={null}>
        <LLMTracingView />
      </React.Suspense>
    </QueryClientProvider>,
  );
};

// Both voice grids render on every pass; `enabled` follows the active tab.
const voiceGrids = async () => {
  await waitFor(() =>
    expect(harness.callLogsGridProps.some((props) => props.enabled)).toBe(true),
  );
  return {
    active: harness.callLogsGridProps.findLast((props) => props.enabled),
    inactive: harness.callLogsGridProps.findLast((props) => !props.enabled),
  };
};

const openFirstCall = async (grid, call) => {
  await act(async () => {
    grid.onRowClicked({ rowIndex: 0, data: call }, 1, 30);
  });
};

describe("LLMTracingView voice Compare call drawer", () => {
  const { setTestDetailDrawerOpen } = harness.testDetailState;

  beforeEach(() => {
    harness.callLogsGridProps = [];
    setTestDetailDrawerOpen.mockClear();
  });

  it("steps a call opened on the Compare tab through the Compare list", async () => {
    harness.selectedGraph = "compare";
    renderView();

    const { active: compareGrid, inactive: primaryGrid } = await voiceGrids();
    // The two tabs list different calls: only the Compare side filters on
    // status = failed.
    expect(compareGrid.params.filters).toContain("failed");
    expect(primaryGrid.params.filters).not.toContain("failed");

    await openFirstCall(compareGrid, { id: "failed-call-1" });
    expect(setTestDetailDrawerOpen).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: "failed-call-1" }),
    );

    // One drawer is mounted, and its Previous/Next pages the Compare list.
    const drawers = screen.getAllByTestId("call-drawer");
    expect(drawers).toHaveLength(1);
    expect(drawers[0].getAttribute("data-list")).toBe(
      compareGrid.params.filters,
    );
  });

  it("steps a call opened on the Primary tab through the Primary list", async () => {
    harness.selectedGraph = "primary";
    renderView();

    const { active: primaryGrid, inactive: compareGrid } = await voiceGrids();
    expect(primaryGrid.params.filters).not.toContain("failed");
    expect(compareGrid.params.filters).toContain("failed");

    await openFirstCall(primaryGrid, { id: "call-1" });
    expect(setTestDetailDrawerOpen).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: "call-1" }),
    );

    const drawers = screen.getAllByTestId("call-drawer");
    expect(drawers).toHaveLength(1);
    expect(drawers[0].getAttribute("data-list")).toBe(
      primaryGrid.params.filters,
    );
  });
});
