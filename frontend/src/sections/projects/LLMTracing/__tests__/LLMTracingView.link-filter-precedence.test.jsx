import React from "react";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OBSERVE_LINK_FILTER_PARAM } from "../common";
import LLMTracingView from "../LLMTracingView";

const harness = vi.hoisted(() => ({
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
      ReactModule.useState(key === "selectedTab" ? "trace" : defaultValue),
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

// graphFilters is the filter panel's view of extraFilters, the channel both the
// localStorage restore and the deep link write to.
vi.mock("../ObserveToolbar", () => ({
  default: (props) => (
    <output
      data-testid="observe-toolbar-filter-state"
      data-graph-filter-values={JSON.stringify(
        (props.graphFilters || []).map(
          (row) => row?.filter_config?.filter_value,
        ),
      )}
    />
  ),
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

vi.mock("src/api/project/saved-views", () => ({
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
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

const FILTERS_STORAGE_KEY = "observe-filters-project-1";

const STORED_PRIMARY_VALUE = "stored-primary";
const STORED_EXTRA_VALUE = "stored-extra";
const LINKED_EXTRA_VALUE = "linked-extra";

const filterRow = (filterValue) => ({
  column_id: "customer.tier",
  property_id: "custom_attribute:customer.tier",
  filter_config: {
    col_type: "SPAN_ATTRIBUTE",
    filter_type: "text",
    filter_op: "equals",
    filter_value: filterValue,
  },
});

const seedStoredFilters = () => {
  window.localStorage.setItem(
    FILTERS_STORAGE_KEY,
    JSON.stringify({
      tabType: "trace",
      filters: [filterRow(STORED_PRIMARY_VALUE)],
      extra_filters: [filterRow(STORED_EXTRA_VALUE)],
    }),
  );
};

const setSearch = (search) => {
  window.history.replaceState({}, "", search ? `/?${search}` : "/");
};

const linkFilterParam = () =>
  `${OBSERVE_LINK_FILTER_PARAM}=${encodeURIComponent(
    JSON.stringify([filterRow(LINKED_EXTRA_VALUE)]),
  )}`;

async function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <React.Suspense fallback={<div>Loading tracing view</div>}>
          <LLMTracingView />
        </React.Suspense>
      </QueryClientProvider>,
    );
  });
}

const restoredPrimaryValues = () =>
  harness.setFiltersCalls
    .filter((call) => call.filterKey === "primaryTraceFilter")
    .flatMap((call) => (Array.isArray(call.next) ? call.next : []))
    .map((row) => row?.filter_config?.filter_value);

const panelFilterValues = () =>
  JSON.parse(
    screen
      .getByTestId("observe-toolbar-filter-state")
      .getAttribute("data-graph-filter-values"),
  );

describe("LLMTracingView deep link vs localStorage filter precedence", () => {
  beforeEach(() => {
    window.localStorage.clear();
    harness.setFiltersCalls.length = 0;
    setSearch("");
  });

  it("restores the stored filters when the URL carries no filter param", async () => {
    seedStoredFilters();
    await renderView();

    expect(restoredPrimaryValues()).toContain(STORED_PRIMARY_VALUE);
    expect(panelFilterValues()).toEqual([STORED_EXTRA_VALUE]);
  });

  it("still restores when the URL carries an unrelated param", async () => {
    seedStoredFilters();
    setSearch("selectedTab=trace");
    await renderView();

    expect(restoredPrimaryValues()).toContain(STORED_PRIMARY_VALUE);
    expect(panelFilterValues()).toEqual([STORED_EXTRA_VALUE]);
  });

  // The trace list writes these two params itself on every filter change, so
  // they turn up on any page that has ever been filtered — including an
  // ordinary reload. Skipping the whole restore there would drop the chip
  // strip, which localStorage is the only source for.
  it.each(["primaryTraceFilter", "primarySpanFilter"])(
    "skips only the primary rows when the URL carries %s, keeping the chips",
    async (param) => {
      seedStoredFilters();
      setSearch(`${param}=${encodeURIComponent(JSON.stringify([]))}`);
      await renderView();

      expect(restoredPrimaryValues()).not.toContain(STORED_PRIMARY_VALUE);
      expect(panelFilterValues()).toEqual([STORED_EXTRA_VALUE]);
    },
  );

  it("lets a link's filters win over the stored ones", async () => {
    seedStoredFilters();
    setSearch(linkFilterParam());
    await renderView();

    expect(restoredPrimaryValues()).not.toContain(STORED_PRIMARY_VALUE);
    expect(panelFilterValues()).toEqual([LINKED_EXTRA_VALUE]);
  });
});
