import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import LLMTracingView from "../LLMTracingView";

// The other LLMTracingView suites stub useCursorAttributeInventory with a
// hoisted array, which hid a render loop: the real hook returned a new
// `attributes` array on every render, and the filter-definition effects keyed
// on it set state again. This suite keeps the real inventory and catalog hooks.

const harness = vi.hoisted(() => ({
  emptyFilters: [],
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
  projectDetail: { source: "observe" },
  replayState: {
    openReplaySessionDrawer: {},
    setIsReplayDrawerCollapsed: vi.fn(),
    setCreatedReplay: vi.fn(),
    setReplayType: vi.fn(),
    setOpenReplaySessionDrawer: vi.fn(),
  },
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
      ReactModule.useState(key === "selectedTab" ? "trace" : defaultValue),
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

// Keep the real usePropertyCatalog: its per-render `metrics` array was half of
// the loop.
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

// Every chunk LLMTracingView lazy-loads. Resolving them before mounting keeps
// a late Suspense resolution on a loaded runner out of the idle window.
const LAZY_CHILDREN = [
  () => import("../GraphSection/PrimaryGraph"),
  () => import("../GraphSection/AgentGraph"),
  () => import("../GraphSection/AgentPath"),
  () => import("src/sections/agents/CallLogs/CallLogsGrid"),
  () => import("../LLMFiltersDrawer"),
  () => import("src/components/traceDetailDrawer/addToDataset/add-dataset"),
  () => import("src/components/traceDetail/AddTagsPopover"),
  () =>
    import("src/sections/annotations/queues/components/add-to-queue-dialog"),
  () => import("src/components/traceDetailDrawer/AnnotateDrawer"),
  () =>
    import(
      "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown"
    ),
];

// React's scheduler keeps the real setImmediate it captured at import, so a
// render loop still runs between these turns while component timers are fake.
const schedulerTurn = () =>
  new Promise((resolve) => globalThis.setImmediate(resolve));
const QUIET_TURNS = 50;
const MAX_SETTLE_TURNS = 500;
const IDLE_TURNS = 200;

// Settled means no query fetching or mutating, no timer pending and no commit
// across QUIET_TURNS scheduler turns. Nothing here reads the wall clock.
const settle = async ({ counts, queryClient }) => {
  let quietTurns = 0;
  for (
    let turn = 0;
    turn < MAX_SETTLE_TURNS && quietTurns < QUIET_TURNS;
    turn += 1
  ) {
    const before = counts.commits;
    vi.runOnlyPendingTimers();
    await schedulerTurn();
    const busy =
      queryClient.isFetching() > 0 ||
      queryClient.isMutating() > 0 ||
      vi.getTimerCount() > 0;
    quietTurns = !busy && counts.commits === before ? quietTurns + 1 : 0;
  }
};

let previousActEnvironment;
let root;
let host;

beforeAll(async () => {
  // The loop is driven by React's scheduler, so observe it the way a browser
  // tab does: outside act(), which would otherwise flush it forever.
  previousActEnvironment = globalThis.IS_REACT_ACT_ENVIRONMENT;
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
  await Promise.all(LAZY_CHILDREN.map((load) => load()));
});

afterAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
});

afterEach(() => {
  root?.unmount();
  host?.remove();
  root = null;
  host = null;
  vi.useRealTimers();
});

const mountView = () => {
  const counts = { commits: 0 };
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  root.render(
    <QueryClientProvider client={queryClient}>
      <React.Suspense fallback={null}>
        <React.Profiler
          id="llm-tracing-view"
          onRender={() => {
            counts.commits += 1;
          }}
        >
          <LLMTracingView />
        </React.Profiler>
      </React.Suspense>
    </QueryClientProvider>,
  );
  return { counts, queryClient };
};

describe("LLMTracingView idle rendering", () => {
  it.each(["simulator", "observe"])(
    "commits nothing while a %s project's Trace tab sits idle",
    async (source) => {
      harness.projectDetail = { source };
      vi.useFakeTimers({
        toFake: [
          "setTimeout",
          "clearTimeout",
          "setInterval",
          "clearInterval",
          "Date",
        ],
      });
      const view = mountView();
      await settle(view);
      expect(view.counts.commits).toBeGreaterThan(0);

      // Ten idle minutes of timers with auto-refresh off and no input, then
      // enough scheduler turns for a render loop to commit many times over.
      const settledCommits = view.counts.commits;
      vi.advanceTimersByTime(10 * 60 * 1000);
      for (let turn = 0; turn < IDLE_TURNS; turn += 1) {
        await schedulerTurn();
      }
      expect(view.counts.commits - settledCommits).toBe(0);
      expect(vi.getTimerCount()).toBe(0);
    },
  );
});
