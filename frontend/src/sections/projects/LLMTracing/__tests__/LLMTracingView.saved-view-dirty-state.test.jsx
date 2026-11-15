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
import { serializeSavedViewConfig } from "src/api/project/saved-views";
import { normalizeConfigKeys } from "../common";
import {
  getCanonicalColumnSnapshot,
  mergeColumnsWithAuthoritativeConfig,
} from "../defaultColumns";

const harness = vi.hoisted(() => ({
  updateSavedView: vi.fn(),
  toolbarProps: null,
  gridProps: {},
  columnChooserProps: null,
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
  return {
    default: ReactModule.forwardRef((props, _ref) => {
      harness.gridProps[`${props.compareType}-trace`] = props;
      return null;
    }),
  };
});

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef((props, _ref) => {
      harness.gridProps[`${props.compareType}-spans`] = props;
      return null;
    }),
  };
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
  () => ({
    default: (props) => {
      harness.columnChooserProps = props;
      return null;
    },
  }),
);

const originalRange = ["2026-09-01 00:00:00", "2026-09-10 00:00:00"];
// September 2–12 selected in the local calendar: the API upper bound is
// September 13 at local midnight, serialized as an exact UTC instant.
const newRange = [
  new Date(2026, 8, 2).toISOString(),
  new Date(2026, 8, 13).toISOString(),
];
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
  harness.gridProps = {};
  harness.columnChooserProps = null;
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
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <React.Suspense fallback={<div>Loading</div>}>
        <LLMTracingView />
      </React.Suspense>
    </QueryClientProvider>
  );
  let mounted;
  await act(async () => {
    mounted = render(tree());
  });
  await waitFor(() => expect(screen.queryByText("Loading")).toBeNull());
  await waitFor(() =>
    expect(harness.observeHeader.registerGetViewConfig).toHaveBeenCalled(),
  );
  return () => mounted.rerender(tree());
}

// The latest config getter the view registered with the Observe header.
const viewConfig = () =>
  harness.observeHeader.registerGetViewConfig.mock.calls
    .filter(([fn]) => typeof fn === "function")
    .at(-1)[0]();

const saveViewButton = () =>
  screen.queryByRole("button", { name: "Save view" });

async function editCustomRange() {
  fireEvent.click(screen.getByRole("button", { name: "9/1/2026 - 9/9/2026" }));
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

// The saved-view API stores a flat customColumns array unchanged. Only list
// config keys are normalized from snake_case. These are the old 17 span ids,
// with the newly available Tags definition deliberately absent from saved state.
const savedSpanIds = [
  "cost",
  "span_name",
  "status",
  "input",
  "output",
  "latency_ms",
  "total_tokens",
  "model",
  "start_time",
  "span_id",
  "trace_id",
  "prompt_tokens",
  "completion_tokens",
  "provider",
  "user_id",
  "user_id_type",
  "user_id_hash",
];
const columnFixture = (tab, withCustom = true) => {
  const nameId = tab === "spans" ? "span_name" : "trace_name";
  const ids =
    tab === "spans" ? savedSpanIds : ["cost", nameId, "status", "trace_id"];
  const customColumns = withCustom
    ? [
        {
          id: "custom.visible",
          name: "custom.visible",
          isVisible: true,
          groupBy: "Custom Columns",
        },
        {
          id: "custom.hidden",
          name: "custom.hidden",
          isVisible: false,
          groupBy: "Custom Columns",
        },
      ]
    : [];
  const order = withCustom
    ? [ids[0], "custom.visible", ...ids.slice(1), "custom.hidden"]
    : ids;
  const config = serializeSavedViewConfig(
    savedView({
      display: {
        dateFilter: { dateOption: "Custom", dateFilter: originalRange },
        columnState: order.map((colId) => ({
          colId,
          hide: !["cost", nameId, "custom.visible"].includes(colId),
        })),
        customColumns,
      },
    }),
  );
  const authoritative = normalizeConfigKeys([
    ...[nameId, ...ids.filter((id) => id !== nameId)].map((id) => ({
      id,
      name: id,
      is_visible: true,
      group_by: null,
    })),
    // Trace Tags already default visible; only span Tags are newly hidden.
    { id: "tags", name: "Tags", is_visible: tab === "trace", group_by: null },
  ]);
  return { config, authoritative };
};

// Exercise the public grid-to-parent config boundary with the same production
// snapshot/merge helpers as TraceGrid and SpanGrid; no AG Grid internals mocked.
async function receiveColumnConfig(slot, authoritative) {
  await act(async () => {
    const props = harness.gridProps[slot];
    const canonical = getCanonicalColumnSnapshot(authoritative);
    props.canonicalColumnsRef.current = canonical.columns;
    props.canonicalOrderRef.current = canonical.order;
    const pending = props.pendingCustomColumnsRef.current;
    props.pendingCustomColumnsRef.current = [];
    props.setColumns(
      mergeColumnsWithAuthoritativeConfig(
        props.columns,
        authoritative,
        pending,
      ),
    );
  });
}

const columnPreferences = (slot) =>
  harness.gridProps[slot].columns.map((c) => ({
    colId: c.id,
    hide: c.isVisible === false,
  }));

describe("saved column hydration and authoritative config", () => {
  it.each(["trace", "spans"])(
    "keeps %s customs when the saved response precedes the list response",
    async (tab) => {
      harness.selectedTab = tab;
      const { config, authoritative } = columnFixture(tab);
      const persisted = JSON.stringify(config);
      harness.observeHeader.activeViewConfig = config;
      await mountView();
      const slot = `primary-${tab}`;
      // A non-empty fallback slot must hydrate customs before any list response.
      expect(
        harness.gridProps[slot].columns.filter(
          (c) => c.groupBy === "Custom Columns",
        ),
      ).toEqual(config.display.customColumns);
      await receiveColumnConfig(slot, authoritative);
      expect(columnPreferences(slot)).toEqual([
        ...config.display.columnState,
        { colId: "tags", hide: tab === "spans" },
      ]);
      expect(viewConfig().display.customColumns).toEqual(
        config.display.customColumns,
      );
      expect(JSON.stringify(config)).toBe(persisted);
      expect(harness.updateSavedView).not.toHaveBeenCalled();
      expect(saveViewButton()).toBeNull();
    },
  );

  it.each(["trace", "spans"])(
    "hydrates a late %s saved response and retains edits on subsequent schema updates",
    async (tab) => {
      harness.selectedTab = tab;
      harness.observeHeader.activeViewConfig = null;
      const { config, authoritative } = columnFixture(tab);
      const slot = `primary-${tab}`;
      const rerender = await mountView();
      await receiveColumnConfig(slot, authoritative);
      harness.observeHeader.activeViewConfig = config;
      await act(async () => rerender());
      expect(columnPreferences(slot)).toEqual([
        ...config.display.columnState,
        { colId: "tags", hide: tab === "spans" },
      ]);
      // A same-id authoritative merge must not segregate customs from base ids.
      await receiveColumnConfig(slot, authoritative);
      expect(columnPreferences(slot)).toEqual([
        ...config.display.columnState,
        { colId: "tags", hide: tab === "spans" },
      ]);
      // Public chooser callbacks model a user toggle and reorder after hydration.
      await act(async () => {
        harness.columnChooserProps.onColumnVisibilityChange({
          ...Object.fromEntries(
            harness.gridProps[slot].columns.map((c) => [c.id, c.isVisible]),
          ),
          cost: false,
        });
      });
      await act(async () => {
        harness.columnChooserProps.setColumns(
          [...harness.gridProps[slot].columns].reverse(),
        );
      });
      const edited = columnPreferences(slot);
      await receiveColumnConfig(slot, [
        ...authoritative,
        { id: "new.metric", name: "New metric", isVisible: false },
      ]);
      expect(columnPreferences(slot)).toEqual([
        ...edited,
        { colId: "new.metric", hide: true },
      ]);
      expect(harness.updateSavedView).not.toHaveBeenCalled();
    },
  );

  it("does not expire hydration on fallbacks before the first list config", async () => {
    harness.selectedTab = "spans";
    harness.observeHeader.activeViewConfig = null;
    const { config, authoritative } = columnFixture("spans");
    const rerender = await mountView();
    vi.useFakeTimers();
    try {
      harness.observeHeader.activeViewConfig = config;
      await act(async () => rerender());
      // Exercise the existing 2500ms fail-safe, not a new timing matrix.
      await act(async () => vi.advanceTimersByTimeAsync(2501));
      await receiveColumnConfig("primary-spans", authoritative);
      expect(columnPreferences("primary-spans")).toEqual([
        ...config.display.columnState,
        { colId: "tags", hide: true },
      ]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("preserves an old17-only saved view and exposes new Tags hidden", async () => {
    harness.selectedTab = "spans";
    const { config, authoritative } = columnFixture("spans", false);
    harness.observeHeader.activeViewConfig = config;
    await mountView();
    await receiveColumnConfig("primary-spans", authoritative);
    expect(columnPreferences("primary-spans")).toEqual([
      ...config.display.columnState,
      { colId: "tags", hide: true },
    ]);
    expect(harness.updateSavedView).not.toHaveBeenCalled();
  });
});

describe("LLMTracingView saved-view dirty state (TH-4883)", () => {
  it("keeps Save view hidden for an untouched hydrated custom range", async () => {
    await mountView();
    expect(viewConfig().display.dateFilter.dateFilter).toEqual(originalRange);
    expect(saveViewButton()).toBeNull();
  });

  it("keeps the exact stored bounds and stays clean after opening and accepting the calendar", async () => {
    await mountView();
    fireEvent.click(
      screen.getByRole("button", { name: "9/1/2026 - 9/9/2026" }),
    );
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Custom range" }),
    );
    expect(screen.getByLabelText("Start Date")).toHaveValue("2026-09-01");
    expect(screen.getByLabelText("End Date")).toHaveValue("2026-09-09");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
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
      screen.getByRole("button", { name: "9/1/2026 - 9/9/2026" }),
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

  it("the filter chip Save moves the baseline to the saved config", async () => {
    harness.observeHeader.activeViewConfig = savedView({
      extra_filters: canonicalFilter("primary"),
    });
    await mountView();
    await act(async () => harness.filterChipsProps.onSave());
    const [payload, callbacks] = harness.updateSavedView.mock.calls[0];
    expect(payload.id).toBe("saved-view-1");
    await act(async () =>
      callbacks.onSuccess({ data: { result: { config: payload.config } } }),
    );
    expect(harness.observeHeader.setActiveViewConfig).toHaveBeenCalledWith(
      payload.config,
    );
  });

  it("the compare-mode filter bar Save moves the baseline to the saved config", async () => {
    harness.observeHeader.activeViewConfig = savedView({
      compare_extra_filters: canonicalFilter("compare-only"),
      display: {
        dateFilter: { dateOption: "Custom", dateFilter: originalRange },
        showCompare: true,
      },
    });
    await mountView();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const [payload, callbacks] = harness.updateSavedView.mock.calls[0];
    expect(payload.id).toBe("saved-view-1");
    expect(payload.config.compare_extra_filters).toEqual(
      canonicalFilter("compare-only"),
    );
    await act(async () =>
      callbacks.onSuccess({ data: { result: { config: payload.config } } }),
    );
    expect(harness.observeHeader.setActiveViewConfig).toHaveBeenCalledWith(
      payload.config,
    );
  });
});
