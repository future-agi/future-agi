import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
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
  callLogsGridProps: [],
  primaryGraphProps: [],
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
    harness.callLogsGridProps = [];
    harness.primaryGraphProps = [];
    harness.removeSimulationCalls = undefined;
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

  it("applies the Voice list's simulation-call toggle to the Voice chart", async () => {
    harness.projectDetail = { source: "simulator" };
    harness.removeSimulationCalls = true;
    renderView();

    const props = await lastPrimaryGraphProps();
    expect(props.observeType).toBe("voice");
    expect(props.removeSimulationCalls).toBe(true);
    // The grid beside it lists calls under the same toggle.
    await waitFor(() =>
      expect(harness.callLogsGridProps.length).toBeGreaterThan(0),
    );
    expect(
      harness.callLogsGridProps.at(-1).params.remove_simulation_calls,
    ).toBe(true);
  });

  it("keeps simulation calls on the Voice chart while the toggle is off", async () => {
    harness.projectDetail = { source: "simulator" };
    renderView();

    const props = await lastPrimaryGraphProps();
    expect(props.observeType).toBe("voice");
    expect(props.removeSimulationCalls).toBe(false);
  });
});

// A voice call's trace id can exist in several projects (a provider account
// shared by several voice projects). Bulk "Add tags" reads each selected
// call's current tags; that read must come from this project's copy.
describe("LLMTracingView voice bulk tags", () => {
  beforeEach(() => {
    harness.callLogsGridProps = [];
    harness.toolbarProps = [];
    harness.selectedTab = "trace";
    harness.projectDetail = { source: "simulator" };
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: { result: { tags: ["vip"] } } });
  });

  it("reads each selected call's current tags from this project's copy", async () => {
    renderView();
    await waitFor(() =>
      expect(harness.callLogsGridProps.some((props) => props.enabled)).toBe(
        true,
      ),
    );

    await act(async () => {
      harness.callLogsGridProps
        .findLast((props) => props.enabled)
        .onSelectionChanged(["trace-a", "trace-b"]);
    });
    await act(async () => {
      harness.toolbarProps
        .at(-1)
        .onBulkAction("tags", { currentTarget: document.body });
    });

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    for (const [url, config] of axios.get.mock.calls) {
      expect(url).toBe("/traces/detail/");
      expect(config?.params).toEqual({ project_id: "project-1" });
    }
  });
});
