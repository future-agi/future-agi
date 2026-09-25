import React from "react";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMTracingView from "../LLMTracingView";

// The Voice screen's chart and grid must count one population. The grid reads
// list_voice_calls (voice calls only); the chart reads the trace graph, which
// counts every trace unless it is asked for the voice-call population.

const harness = vi.hoisted(() => ({
  emptyFilters: [],
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
  primaryGraphProps: [],
  projectDetail: { source: "observe" },
  replayState: {
    openReplaySessionDrawer: {},
    setIsReplayDrawerCollapsed: vi.fn(),
    setCreatedReplay: vi.fn(),
    setReplayType: vi.fn(),
    setOpenReplaySessionDrawer: vi.fn(),
  },
  selectedTab: "trace",
  testDetailState: { setTestDetailDrawerOpen: vi.fn() },
}));

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
        key === "selectedTab" ? harness.selectedTab : defaultValue,
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
    useLLMTracingFilters: (defaultFilters, defaultDateFilter) => {
      const [filters, setFilters] = ReactModule.useState(defaultFilters);
      const [dateFilter, setDateFilter] =
        ReactModule.useState(defaultDateFilter);
      return {
        filters,
        setFilters,
        validatedFilters: harness.emptyFilters,
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

vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
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

vi.mock("../GraphSection/PrimaryGraph", () => ({
  default: (props) => {
    harness.primaryGraphProps.push(props);
    return null;
  },
}));
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

const lastPrimaryGraphProps = async () => {
  await waitFor(() =>
    expect(harness.primaryGraphProps.length).toBeGreaterThan(0),
  );
  return harness.primaryGraphProps.at(-1);
};

describe("LLMTracingView graph population", () => {
  beforeEach(() => {
    harness.primaryGraphProps = [];
    harness.selectedTab = "trace";
  });

  it("asks the Voice screen's trace graph for voice calls, as its grid lists", async () => {
    harness.projectDetail = { source: "simulator" };
    renderView();

    const props = await lastPrimaryGraphProps();
    expect(props.graphEndpoint).toBe("/traces/graph/");
    expect(props.observeType).toBe("voice");
  });

  it("keeps an observe project's trace graph on every trace", async () => {
    harness.projectDetail = { source: "observe" };
    renderView();

    const props = await lastPrimaryGraphProps();
    expect(props.graphEndpoint).toBe("/traces/graph/");
    expect(props.observeType).toBeUndefined();
  });

  it("never sends the voice scope to the span graph", async () => {
    harness.projectDetail = { source: "simulator" };
    harness.selectedTab = "spans";
    renderView();

    const props = await lastPrimaryGraphProps();
    expect(props.graphEndpoint).toBe("/spans/graph/");
    expect(props.observeType).toBeUndefined();
  });
});
