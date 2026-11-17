/* eslint-disable react/prop-types */
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LLMTracingView from "../LLMTracingView";

// The trace list's toolbar offers Trace and Spans one click away. Clicking
// Spans makes the same switch as Display → "Group traces by" → "Span": the
// span list is the one asked for rows (its `enabled`), the trace list stops.
// The real toolbar renders; the grids record what they are asked. Harness
// from the saved-view dirty-state test.

const harness = vi.hoisted(() => ({
  grids: { trace: null, spans: null },
  compareGrids: { trace: null, spans: null },
  selectedTab: "trace",
  selectedGraph: "primary",
  showCompare: false,
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
          selectedGraph: harness.selectedGraph,
          showCompare: harness.showCompare,
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

// Record both graph populations so toolbar actions can be checked for isolation.
vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef((props, _ref) => {
      if (props.compareType === "primary") harness.grids.trace = props;
      if (props.compareType === "compare") harness.compareGrids.trace = props;
      return null;
    }),
  };
});

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef((props, _ref) => {
      if (props.compareType === "primary") harness.grids.spans = props;
      if (props.compareType === "compare") harness.compareGrids.spans = props;
      return null;
    }),
  };
});

// Keep the real toolbar. Only unrelated expensive panels are stubbed.
vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../TraceFilterPanel", () => ({
  default: ({ open, onClear }) =>
    open ? <button onClick={onClear}>Clear all</button> : null,
}));
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
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
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

vi.mock("../GraphSection/PrimaryGraph", () => ({
  default: ({ graphLabel, onFilterToggle }) =>
    onFilterToggle ? (
      <button onClick={onFilterToggle}>
        {graphLabel === "Compare Graph" ? "Comparison filters" : "Primary filters"}
      </button>
    ) : null,
}));
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

let queryClient;
beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/?tab=traces&selectedTab=trace");
  harness.grids = { trace: null, spans: null };
  harness.compareGrids = { trace: null, spans: null };
  harness.selectedTab = "trace";
  harness.selectedGraph = "primary";
  harness.showCompare = false;
});
afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.useRealTimers();
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
}

const toolbarToggle = () =>
  screen.getByRole("group", { name: "Group traces by" });
const listAsked = (kind) => Boolean(harness.grids[kind]?.enabled);

describe("LLMTracingView input/output search graph scope", () => {
  const search = () =>
    screen.getByRole("textbox", { name: "Search input or output" });
  const selectGraph = (graph) =>
    fireEvent.click(
      screen.getByRole("tab", {
        name: graph === "primary" ? "Primary Graph" : "Comparison Graph",
      }),
    );
  const searchFilters = (grid) =>
    grid.extraFilters.filter((filter) => filter.column_id === "input_output");

  it.each(["trace", "spans"])(
    "searches and clears comparison %s without changing the primary search",
    async (tab) => {
      harness.selectedTab = tab;
      harness.showCompare = true;
      await mountView();
      await waitFor(() => expect(harness.grids[tab]?.enabled).toBe(true));
      vi.useFakeTimers();

      fireEvent.change(search(), { target: { value: "primary content" } });
      act(() => vi.advanceTimersByTime(350));
      expect(searchFilters(harness.grids[tab])).toEqual([
        expect.objectContaining({
          filter_config: expect.objectContaining({ filter_value: "primary content" }),
        }),
      ]);

      selectGraph("compare");
      expect(harness.compareGrids[tab].enabled).toBe(true);
      expect(search()).toHaveValue("");
      fireEvent.change(search(), { target: { value: "comparison content" } });
      act(() => vi.advanceTimersByTime(350));
      expect(searchFilters(harness.compareGrids[tab])).toEqual([
        expect.objectContaining({
          filter_config: expect.objectContaining({ filter_value: "comparison content" }),
        }),
      ]);
      expect(searchFilters(harness.grids[tab])[0].filter_config.filter_value).toBe(
        "primary content",
      );

      selectGraph("primary");
      expect(search()).toHaveValue("primary content");
      selectGraph("compare");
      expect(search()).toHaveValue("comparison content");
      fireEvent.click(
        screen.getByRole("button", { name: "Clear input or output search" }),
      );
      expect(search()).toHaveValue("");
      expect(searchFilters(harness.compareGrids[tab])).toEqual([]);
      act(() => vi.advanceTimersByTime(350));
      expect(searchFilters(harness.compareGrids[tab])).toEqual([]);
      expect(searchFilters(harness.grids[tab])[0].filter_config.filter_value).toBe(
        "primary content",
      );
      selectGraph("primary");
      expect(search()).toHaveValue("primary content");
    },
  );

  it.each([
    ["trace", "pending"],
    ["trace", "oversized"],
    ["spans", "pending"],
    ["spans", "oversized"],
  ])("panel Clear all cancels an uncommitted comparison %s %s search", async (tab, kind) => {
    harness.selectedTab = tab;
    harness.showCompare = true;
    await mountView();
    await waitFor(() => expect(harness.grids[tab]?.enabled).toBe(true));
    vi.useFakeTimers();

    fireEvent.change(search(), { target: { value: "primary content" } });
    act(() => vi.advanceTimersByTime(350));
    const primaryFilters = harness.grids[tab].extraFilters;
    selectGraph("compare");
    const draft = kind === "oversized" ? "é".repeat(513) : "pending content";
    fireEvent.change(search(), { target: { value: draft } });
    expect(searchFilters(harness.compareGrids[tab])).toEqual([]);
    if (kind === "oversized") {
      expect(screen.getByText("Use 1,024 UTF-8 bytes or fewer.")).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("button", { name: "Comparison filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(search()).toHaveValue("");
    expect(screen.queryByText("Use 1,024 UTF-8 bytes or fewer.")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(350));
    expect(search()).toHaveValue("");
    expect(searchFilters(harness.compareGrids[tab])).toEqual([]);
    expect(harness.grids[tab].extraFilters).toEqual(primaryFilters);
    selectGraph("primary");
    expect(search()).toHaveValue("primary content");
  });

  it.each([
    ["trace", "primary", "compare"],
    ["trace", "compare", "primary"],
    ["spans", "primary", "compare"],
    ["spans", "compare", "primary"],
  ])("preserves the %s %s search draft when clearing the %s panel", async (tab, owner, target) => {
    harness.selectedTab = tab;
    harness.selectedGraph = target;
    harness.showCompare = true;
    await mountView();
    const gridsFor = (graph) => graph === "primary" ? harness.grids : harness.compareGrids;
    await waitFor(() => expect(gridsFor(target)[tab]?.enabled).toBe(true));
    vi.useFakeTimers();

    fireEvent.change(search(), { target: { value: "content to clear" } });
    act(() => vi.advanceTimersByTime(350));
    expect(searchFilters(gridsFor(target)[tab])).toHaveLength(1);
    selectGraph(owner);
    fireEvent.change(search(), { target: { value: "keep pending content" } });
    fireEvent.click(screen.getByRole("button", {
      name: target === "primary" ? "Primary filters" : "Comparison filters",
    }));
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(search()).toHaveValue("keep pending content");
    expect(searchFilters(gridsFor(target)[tab])).toEqual([]);
    act(() => vi.advanceTimersByTime(350));
    expect(searchFilters(gridsFor(owner)[tab])).toEqual([
      expect.objectContaining({
        filter_config: expect.objectContaining({ filter_value: "keep pending content" }),
      }),
    ]);
    expect(searchFilters(gridsFor(target)[tab])).toEqual([]);
  });

  it.each([
    ["trace", "primary", "compare"],
    ["trace", "compare", "primary"],
    ["spans", "primary", "compare"],
    ["spans", "compare", "primary"],
  ])("cancels a pending %s draft when switching %s to %s", async (tab, from, to) => {
    harness.selectedTab = tab;
    harness.selectedGraph = from;
    harness.showCompare = true;
    await mountView();
    await waitFor(() =>
      expect(
        (from === "primary" ? harness.grids : harness.compareGrids)[tab]?.enabled,
      ).toBe(true),
    );
    vi.useFakeTimers();
    // Both graphs have the same committed value: only the graph scope can
    // cancel this draft, not synchronization of a changed committed value.
    fireEvent.change(search(), { target: { value: "uncommitted content" } });
    selectGraph(to);
    act(() => vi.advanceTimersByTime(350));
    expect(search()).toHaveValue("");
    expect(searchFilters(harness.grids[tab])).toEqual([]);
    expect(searchFilters(harness.compareGrids[tab])).toEqual([]);
    selectGraph(from);
    expect(search()).toHaveValue("");
  });
});

describe("LLMTracingView Trace and Spans toggle", () => {
  it("switches the list to spans from the toolbar's Spans toggle", async () => {
    await mountView();
    const toggle = await waitFor(toolbarToggle);
    await waitFor(() => expect(listAsked("trace")).toBe(true));
    expect(listAsked("spans")).toBe(false);

    fireEvent.click(within(toggle).getByRole("button", { name: "Spans" }));

    await waitFor(() => expect(listAsked("spans")).toBe(true));
    expect(listAsked("trace")).toBe(false);
    expect(
      within(toolbarToggle()).getByRole("button", { name: "Spans" }),
    ).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(
      within(toolbarToggle()).getByRole("button", { name: "Trace" }),
    );

    await waitFor(() => expect(listAsked("trace")).toBe(true));
    expect(listAsked("spans")).toBe(false);
  });

  it("opens with Spans pressed when the view is grouped by span", async () => {
    harness.selectedTab = "spans";
    window.history.replaceState({}, "", "/?tab=traces&selectedTab=spans");
    await mountView();

    await waitFor(() => expect(listAsked("spans")).toBe(true));
    const toggle = await waitFor(toolbarToggle);
    expect(
      within(toggle).getByRole("button", { name: "Spans" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(toggle).getByRole("button", { name: "Trace" }),
    ).toHaveAttribute("aria-pressed", "false");
  });
});
