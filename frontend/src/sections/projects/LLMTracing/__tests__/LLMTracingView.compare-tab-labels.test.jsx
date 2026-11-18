import React from "react";
import { act, render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LLMTracingView from "../LLMTracingView";

// Compare mode shows two tabs above the data table. They only switch which
// table (primary or comparison) is shown; both graphs stay on screen, so the
// tab labels must name the data, not a graph (#1686).

const harness = vi.hoisted(() => ({
  updateSavedView: vi.fn(),
  toolbarProps: null,
  filterChipsProps: null,
  filterSetters: {},
  selectedTab: "trace",
  attributes: [],
  dashboardFilterValues: [],
  emptyFilters: [],
  inventoryControlProps: {},
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
  setFiltersCalls: [],
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
    useLLMTracingFilters: (defaultFilters, defaultDateFilter, filterKey) => {
      const [filters, setFilters] = ReactModule.useState(defaultFilters);
      const [dateFilter, setDateFilter] =
        ReactModule.useState(defaultDateFilter);
      const recordingSetFilters = ReactModule.useCallback(
        (next) => {
          harness.setFiltersCalls.push({ filterKey, next });
          setFilters(next);
        },
        [filterKey],
      );
      harness.filterSetters[filterKey] = {
        setFilters: recordingSetFilters,
        setDateFilter,
      };
      return {
        filters,
        setFilters: recordingSetFilters,
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

vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../TraceFilterPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
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

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({ data: harness.dashboardFilterValues }),
}));

vi.mock("../useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: harness.attributes,
    inventoryControlProps: harness.inventoryControlProps,
  }),
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
  useUpdateSavedView: () => ({ mutate: harness.updateSavedView }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    project: {
      addAnnotationValuesForSpan: () => "/annotations/values/",
      getSpanGraphData: () => "/spans/graph/",
      getTrace: () => "/traces/detail/",
      getTraceGraphData: () => "/traces/graph/",
      updateProjectColumnVisibility: () => "/projects/columns/",
    },
  },
}));

vi.mock("../GraphSection/PrimaryGraph", () => ({ default: () => null }));
vi.mock("../GraphSection/AgentGraph", () => ({ default: () => null }));
vi.mock("../GraphSection/AgentPath", () => ({ default: () => null }));
vi.mock("../SelectAllBanner", () => ({ default: () => null }));
vi.mock("../FilterChips", () => ({
  default: (props) => {
    harness.filterChipsProps = props;
    return null;
  },
}));
vi.mock("../TracingControls", () => ({ default: () => null }));
vi.mock("../CustomColumnDialog", () => ({ default: () => null }));

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

const savedRange = ["2026-09-01 00:00:00", "2026-09-10 00:00:00"];

let queryClient;
beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(
    {},
    "",
    "/?tab=view-saved-view-1&selectedTab=trace",
  );
  harness.observeHeader.activeViewConfig = {
    filters: [],
    extra_filters: [],
    compare_filters: [],
    display: {
      dateFilter: { dateOption: "Custom", dateFilter: savedRange },
      showCompare: true,
    },
  };
  harness.observeHeader.registerGetViewConfig.mockClear();
  harness.observeHeader.setActiveViewConfig.mockReset();
  harness.updateSavedView.mockReset();
  harness.filterSetters = {};
  harness.filterChipsProps = null;
  harness.selectedTab = "trace";
});
afterEach(() => {
  cleanup();
  queryClient?.clear();
});

async function mountView() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <React.Suspense fallback={<div>Loading</div>}>
          <LLMTracingView />
        </React.Suspense>
      </QueryClientProvider>,
    );
  });
  await waitFor(() => expect(screen.queryByText("Loading")).toBeNull());
  await waitFor(() =>
    expect(harness.observeHeader.registerGetViewConfig).toHaveBeenCalled(),
  );
}

describe("LLMTracingView compare tab labels (#1686)", () => {
  it("names the compare tabs after the data tables they switch", async () => {
    await mountView();

    const primaryTab = await screen.findByRole("tab", { name: "Primary Data" });
    expect(
      screen.getByRole("tab", { name: "Comparison Data" }),
    ).toBeInTheDocument();
    expect(primaryTab).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: /graph/i })).toBeNull();
  });
});
