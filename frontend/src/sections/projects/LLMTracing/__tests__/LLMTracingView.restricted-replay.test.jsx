import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMTracingView from "../LLMTracingView";

// Replaying calls creates scenarios and a Run Simulation run, which an org
// restricted to Environments cannot open, so the Voice screen drops its replay
// actions for such an org. Harness shared with the voice-graph-scope test.

const harness = vi.hoisted(() => ({
  emptyFilters: [],
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
  callLogsGridProps: [],
  llmTracingState: {},
  params: { observeId: "project-1" },
  primaryGraphProps: [],
  traceDetailDrawerProps: [],
  traceGridProps: [],
  toolbarProps: [],
  projectDetail: { source: "observe" },
  removeSimulationCalls: undefined,
  replayState: {
    openReplaySessionDrawer: {},
    setIsReplayDrawerCollapsed: vi.fn(),
    setCreatedReplay: vi.fn(),
    setReplayType: vi.fn(),
    setOpenReplaySessionDrawer: vi.fn(),
  },
  selectedTab: "trace",
  testDetailState: { setTestDetailDrawerOpen: vi.fn() },
  user: {},
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin", user: harness.user }),
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => harness.params,
}));

vi.mock("src/routes/hooks/use-url-state", async () => {
  const ReactModule = await import("react");
  return {
    useUrlState: (key, defaultValue) =>
      ReactModule.useState(
        {
          selectedTab: harness.selectedTab,
          remove_simulation_calls: harness.removeSimulationCalls,
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
    useLLMTracingStoreShallow: (selector) =>
      selector({ ...llmState, ...harness.llmTracingState }),
    useTraceGridStoreShallow: useGrid,
    useSpanGridStoreShallow: useGrid,
  };
});

// Mount the real detail drawer the way TraceGrid does.
vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  const { default: LLMTracingTraceDetailDrawer } = await import(
    "../LLMTracingTraceDetailDrawer"
  );
  return {
    default: ReactModule.forwardRef((props, _ref) => {
      harness.traceGridProps.push(props);
      return <LLMTracingTraceDetailDrawer />;
    }),
  };
});

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    harness.traceDetailDrawerProps.push(props);
    return null;
  },
}));

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});

vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef((props, _ref) => {
      harness.callLogsGridProps.push(props);
      return null;
    }),
  };
});

vi.mock("../ObserveToolbar", () => ({
  default: (props) => {
    harness.toolbarProps.push(props);
    return null;
  },
}));

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

const renderView = (viewProps = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <React.Suspense fallback={null}>
        <LLMTracingView {...viewProps} />
      </React.Suspense>
    </QueryClientProvider>,
  );
};

// Wait for the voice-call actions (only they carry a disabledReason), so the
// project has loaded as a simulator project before anything is asserted.
const voiceBulkActionIds = async () => {
  await waitFor(() =>
    expect(
      harness.toolbarProps
        .at(-1)
        ?.bulkActions.some((action) => action.disabledReason),
    ).toBe(true),
  );
  // The toolbar row loads lazily; it renders for every org.
  await screen.findByText("Add filter");
  return harness.toolbarProps.at(-1).bulkActions.map((action) => action.id);
};

describe("LLMTracingView replay for an org restricted to Environments", () => {
  beforeEach(() => {
    harness.toolbarProps = [];
    harness.projectDetail = { source: "simulator" };
    harness.selectedTab = "trace";
  });

  it("drops Replay Calls from a restricted org's voice actions", async () => {
    harness.user = { restrict_simulate_to_environments: true };
    renderView();

    expect(await voiceBulkActionIds()).not.toContain("replay");
    expect(screen.queryByText(/Replay Call/)).toBeNull();
  });

  it("keeps Replay Calls for other orgs", async () => {
    harness.user = { restrict_simulate_to_environments: false };
    renderView();

    expect(await voiceBulkActionIds()).toContain("replay");
    expect(screen.getByText(/Replay Call/)).toBeInTheDocument();
  });
});
