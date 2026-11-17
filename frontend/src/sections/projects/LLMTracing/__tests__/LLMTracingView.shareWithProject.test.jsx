import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMTracingView from "../LLMTracingView";

// "Share with project" on Traces shares the view as a project tab named for
// its tab type ("Default View" for traces, "Default spans view" for spans);
// nothing loads it for anyone, so the toast says "Shared with the project as
// <name>", never "default for everyone". A user's page has no project to
// share with and saves its views from its tab bar, so the action is listed
// there but disabled, with the reason. Harness from the restricted-replay
// test.

const harness = vi.hoisted(() => ({
  emptyFilters: [],
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  customViews: [],
  toast: vi.fn(),
  llmTracingState: {},
  params: { observeId: "project-1" },
  toolbarProps: [],
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
  user: { id: "u1" },
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
        { selectedTab: harness.selectedTab }[key] ?? defaultValue,
      ),
  };
});

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => harness.toast(...args),
}));

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

vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});

vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
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

// The real name helpers (defaultViewNameFor, findOwnDefaultView, the toast
// wording); only the requests are recorded.
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: harness.createSavedView }),
  useUpdateSavedView: () => ({ mutate: harness.updateSavedView }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetSavedViews: () => ({ data: { custom_views: harness.customViews } }),
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

// Clicks the Display panel's "Share with project" and returns the create
// request's payload and callbacks.
const shareAndCreate = async () => {
  await waitFor(() =>
    expect(harness.toolbarProps.at(-1)?.onSetDefaultView).toEqual(
      expect.any(Function),
    ),
  );
  act(() => harness.toolbarProps.at(-1).onSetDefaultView());
  expect(harness.createSavedView).toHaveBeenCalledTimes(1);
  return harness.createSavedView.mock.calls[0];
};

const created = (name) => ({ data: { result: { id: "view-1", name } } });

describe("LLMTracingView Share with project", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/");
    harness.toolbarProps = [];
    harness.customViews = [];
    harness.params = { observeId: "project-1" };
    harness.selectedTab = "trace";
    harness.user = { id: "u1" };
  });

  it("shares the Traces view with the project and names it in the toast", async () => {
    renderView();
    const [payload, callbacks] = await shareAndCreate();

    // In a project the panel keeps its "Share with project" label.
    expect(harness.toolbarProps.at(-1).setDefaultLabel).toBeUndefined();

    expect(payload).toMatchObject({
      project_id: "project-1",
      name: "Default View",
      tab_type: "traces",
      visibility: "project",
    });
    act(() => callbacks.onSuccess(created("Default View")));

    expect(harness.toast).toHaveBeenCalledWith(
      "Shared with the project as Default View",
      { variant: "success" },
    );
    expect(harness.toast).not.toHaveBeenCalledWith(
      expect.stringMatching(/default for everyone/i),
      expect.anything(),
    );
  });

  // Bug fix: spans created its default as "Default View" too. A user's
  // project view names are unique across tab types, so after a Traces
  // default the Spans one failed with "already exists".
  it("a spans default doesn't take the traces default's name", async () => {
    harness.selectedTab = "spans";
    harness.customViews = [
      {
        id: "own-traces",
        name: "Default View",
        tab_type: "traces",
        created_by: { id: "u1" },
      },
    ];
    renderView();
    const [payload, callbacks] = await shareAndCreate();

    expect(payload).toMatchObject({
      name: "Default spans view",
      tab_type: "spans",
      visibility: "project",
    });
    expect(harness.updateSavedView).not.toHaveBeenCalled();
    act(() => callbacks.onSuccess(created("Default spans view")));
    expect(harness.toast).toHaveBeenCalledWith(
      "Shared with the project as Default spans view",
      { variant: "success" },
    );
  });

  // A user's page has no project to share with, and a view saved there
  // without a project never showed in its tab bar (which lists user_detail
  // views), so the action is listed but disabled, with the reason, and
  // creates nothing.
  it("keeps Share with project disabled with the reason on a user's page", async () => {
    harness.params = { userId: "user-1" };
    renderView({ mode: "user", userIdForUserMode: "user-1" });

    await waitFor(() => expect(harness.toolbarProps.length).toBeGreaterThan(0));
    const toolbar = harness.toolbarProps.at(-1);
    expect(toolbar.onSetDefaultView).toBeUndefined();
    expect(toolbar.setDefaultDisabledReason).toBe(
      "Shared views need a project. Save views on a user's page from its tab bar.",
    );
    expect(toolbar.setDefaultLabel).toBeUndefined();
    expect(harness.createSavedView).not.toHaveBeenCalled();
    expect(harness.updateSavedView).not.toHaveBeenCalled();
  });

  // Note 3: a person's legacy spans default still named "Default View" kept
  // that name, so their Traces share failed with "already exists". Sharing
  // it again renames it, which frees "Default View".
  it("renames a legacy spans default when it is shared again", async () => {
    harness.selectedTab = "spans";
    harness.customViews = [
      {
        id: "legacy-spans",
        name: "Default View",
        tab_type: "spans",
        created_by: { id: "u1" },
      },
    ];
    renderView();
    await waitFor(() =>
      expect(harness.toolbarProps.at(-1)?.onSetDefaultView).toEqual(
        expect.any(Function),
      ),
    );

    act(() => harness.toolbarProps.at(-1).onSetDefaultView());

    expect(harness.createSavedView).not.toHaveBeenCalled();
    expect(harness.updateSavedView).toHaveBeenCalledTimes(1);
    expect(harness.updateSavedView.mock.calls[0][0]).toMatchObject({
      id: "legacy-spans",
      name: "Default spans view",
      visibility: "project",
    });
  });
});
