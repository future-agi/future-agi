import React from "react";
import {
  act,
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LLMTracingView from "../LLMTracingView";

const harness = vi.hoisted(() => ({
  updateSavedView: vi.fn(),
  toolbarProps: null,
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

// Records every setFilters call with the slot it belongs to, so a test can ask
// whether the localStorage restore reached the primary filters at all.
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

// Keep the real toolbar and date picker. Only unrelated expensive panels are stubbed.
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

// Spread the real module: this factory replaces it wholesale, so a hook added on
// dev (useGetSavedViews arrived with #2471) would otherwise be undefined at render
// and take down every test in this file.
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
vi.mock("../FilterChips", () => ({ default: () => null }));
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

const originalRange = ["2026-09-01 00:00:00", "2026-09-10 00:00:00"];
const newRange = ["2026-09-02 00:00:00", "2026-09-12 00:00:00"];
const canonicalFilter = (value) => [
  {
    column_id: "trace_name",
    filter_config: {
      col_type: "SYSTEM_METRIC",
      filter_type: "text",
      filter_op: "equals",
      filter_value: value,
    },
  },
];
const savedView = (overrides = {}) => ({
  filters: [],
  extra_filters: [],
  display: { dateFilter: { dateOption: "Custom", dateFilter: originalRange } },
  ...overrides,
});

let queryClient;
beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(
    {},
    "",
    "/?tab=view-saved-view-1&selectedTab=trace",
  );
  harness.observeHeader.activeViewConfig = savedView();
  harness.observeHeader.registerGetViewConfig.mockClear();
  harness.observeHeader.setActiveViewConfig.mockReset();
  harness.updateSavedView.mockReset();
  harness.filterSetters = {};
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

// The latest config getter the view registered with the Observe header.
const viewConfig = () =>
  harness.observeHeader.registerGetViewConfig.mock.calls
    .filter(([fn]) => typeof fn === "function")
    .at(-1)[0]();

const saveViewButton = () =>
  screen.queryByRole("button", { name: "Save view" });

async function editCustomRange() {
  fireEvent.click(screen.getByRole("button", { name: "9/1/2026 - 9/10/2026" }));
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Custom range" }),
  );
  fireEvent.change(screen.getByLabelText("Start Date"), {
    target: { value: "2026-09-02" },
  });
  fireEvent.change(screen.getByLabelText("End Date"), {
    target: { value: "2026-09-12" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() =>
    expect(viewConfig().display.dateFilter.dateFilter).toEqual(newRange),
  );
}

describe("LLMTracingView saved-view dirty state (TH-4883)", () => {
  it("keeps Save view hidden for an untouched hydrated custom range", async () => {
    await mountView();
    expect(viewConfig().display.dateFilter.dateFilter).toEqual(originalRange);
    expect(saveViewButton()).toBeNull();
  });

  it.each(["trace", "spans"])(
    "shows Save view after only the custom range bounds change on %s",
    async (tab) => {
      harness.selectedTab = tab;
      await mountView();
      await editCustomRange();
      expect(viewConfig().display.dateFilter.dateOption).toBe("Custom");
      await waitFor(() => expect(saveViewButton()).toBeInTheDocument());
    },
  );

  it("re-hides Save view when the custom range is restored to the saved bounds", async () => {
    await mountView();
    await editCustomRange();
    await waitFor(() => expect(saveViewButton()).toBeInTheDocument());
    await act(async () =>
      harness.filterSetters.primaryTraceFilter.setDateFilter({
        dateOption: "Custom",
        dateFilter: originalRange,
      }),
    );
    await waitFor(() => expect(saveViewButton()).toBeNull());
  });

  it("switching to a preset still reveals Save view and updates the existing view id", async () => {
    await mountView();
    fireEvent.click(
      screen.getByRole("button", { name: "9/1/2026 - 9/10/2026" }),
    );
    fireEvent.click(await screen.findByRole("menuitem", { name: "Past 7D" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save view" }));
    expect(harness.updateSavedView).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "saved-view-1",
        config: expect.objectContaining({
          display: expect.objectContaining({
            dateFilter: expect.objectContaining({ dateOption: "7D" }),
          }),
        }),
      }),
      expect.any(Object),
    );
  });

  it("shows Save view after a compare-only filter edit in compare mode", async () => {
    harness.observeHeader.activeViewConfig = savedView({
      compare_filters: [],
      display: {
        dateFilter: { dateOption: "Custom", dateFilter: originalRange },
        showCompare: true,
      },
    });
    await mountView();
    expect(saveViewButton()).toBeNull();
    await act(async () =>
      harness.filterSetters.compareTraceFilter.setFilters(
        canonicalFilter("compare-only"),
      ),
    );
    expect(viewConfig().compare_filters).toEqual(
      canonicalFilter("compare-only"),
    );
    expect(viewConfig().filters).toEqual([]);
    await waitFor(() => expect(saveViewButton()).toBeInTheDocument());
  });

  it("shows Save view after a compare-only custom date edit in compare mode", async () => {
    harness.observeHeader.activeViewConfig = savedView({
      compare_filters: [],
      compare_date_filter: { dateOption: "Custom", dateFilter: originalRange },
      display: {
        dateFilter: { dateOption: "Custom", dateFilter: originalRange },
        showCompare: true,
      },
    });
    await mountView();
    expect(saveViewButton()).toBeNull();
    await act(async () =>
      harness.filterSetters.compareTraceFilter.setDateFilter({
        dateOption: "Custom",
        dateFilter: newRange,
      }),
    );
    expect(viewConfig().compare_date_filter.dateFilter).toEqual(newRange);
    expect(viewConfig().display.dateFilter.dateFilter).toEqual(originalRange);
    await waitFor(() => expect(saveViewButton()).toBeInTheDocument());
  });

  it("does not flag a view saved without compare_date_filter as dirty in compare mode", async () => {
    harness.observeHeader.activeViewConfig = savedView({
      compare_filters: [],
      display: {
        dateFilter: { dateOption: "Custom", dateFilter: originalRange },
        showCompare: true,
      },
    });
    await mountView();
    expect(viewConfig().compare_date_filter).toBeDefined();
    expect(saveViewButton()).toBeNull();
  });

  it("ignores compare filter state while compare mode is off", async () => {
    await mountView();
    await act(async () =>
      harness.filterSetters.compareTraceFilter.setFilters(
        canonicalFilter("hidden-compare"),
      ),
    );
    expect(viewConfig().compare_filters).toBeUndefined();
    expect(saveViewButton()).toBeNull();
  });

  it("a failed update keeps the baseline and allows retry against the same id", async () => {
    await mountView();
    await editCustomRange();
    fireEvent.click(await screen.findByRole("button", { name: "Save view" }));
    const [, callbacks] = harness.updateSavedView.mock.calls[0];
    await act(async () => callbacks.onError(new Error("simulated rejection")));
    expect(harness.observeHeader.setActiveViewConfig).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(harness.updateSavedView).toHaveBeenCalledTimes(2);
    const [payload, retryCallbacks] = harness.updateSavedView.mock.calls[1];
    expect(payload.id).toBe("saved-view-1");
    expect(payload.config.display.dateFilter.dateFilter).toEqual(newRange);
    await act(async () =>
      retryCallbacks.onSuccess({
        data: { result: { config: payload.config } },
      }),
    );
    expect(harness.observeHeader.setActiveViewConfig).toHaveBeenCalledWith(
      payload.config,
    );
  });
});
