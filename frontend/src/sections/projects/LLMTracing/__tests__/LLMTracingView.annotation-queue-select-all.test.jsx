import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  FILTER_FOR_ERRORS,
  FILTER_FOR_HAS_EVAL,
  FILTER_FOR_NON_ANNOTATED,
} from "../common";
import LLMTracingView from "../LLMTracingView";

const harness = vi.hoisted(() => {
  const createStore = (initialState) => {
    let state = initialState;
    const listeners = new Set();
    return {
      getSnapshot: () => state,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      set: (nextState) => {
        state = { ...state, ...nextState };
        listeners.forEach((listener) => listener());
      },
      reset: () => {
        state = initialState;
        listeners.forEach((listener) => listener());
      },
    };
  };

  return {
    addItems: vi.fn(),
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
    selectedGraph: "primary",
    selectedTab: "trace",
    traceStore: createStore({
      toggledNodes: [],
      selectAll: false,
      totalRowCount: 7,
      totalRowCountLowerBound: 7,
      totalRowCountIsLowerBound: false,
    }),
    spanStore: createStore({
      toggledNodes: [],
      selectAll: false,
      totalRowCount: 5,
      totalRowCountLowerBound: 5,
      totalRowCountIsLowerBound: false,
    }),
    validatedFilters: {},
    spanExcludedPhysicalId: JSON.stringify([
      "project-1",
      "trace-2",
      "span-excluded",
      "2026-08-25T10:00:00.000000Z",
    ]),
  };
});

const mixedCatalogFilters = [
  {
    column_id: "quality_eval",
    property_id: "eval:quality-eval",
    filter_config: {
      filter_type: "number",
      filter_op: "greater_than",
      filter_value: 0.8,
      col_type: "EVAL_METRIC",
    },
  },
  {
    column_id: "quality_label",
    property_id: "annotation:quality-label",
    filter_config: {
      filter_type: "categorical",
      filter_op: "in",
      filter_value: ["approved"],
      col_type: "ANNOTATION",
    },
  },
  {
    column_id: "customer.tier",
    property_id: "custom_attribute:customer.tier",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: "enterprise",
      col_type: "SPAN_ATTRIBUTE",
    },
  },
];

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
        key === "selectedTab"
          ? harness.selectedTab
          : key === "selectedGraph"
            ? harness.selectedGraph
            : defaultValue,
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
      return {
        filters,
        setFilters,
        validatedFilters:
          harness.validatedFilters[filterKey] || harness.emptyFilters,
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
  return {
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) => selector(llmState),
    useTraceGridStoreShallow: (selector) =>
      selector(
        ReactModule.useSyncExternalStore(
          harness.traceStore.subscribe,
          harness.traceStore.getSnapshot,
          harness.traceStore.getSnapshot,
        ),
      ),
    useSpanGridStoreShallow: (selector) =>
      selector(
        ReactModule.useSyncExternalStore(
          harness.spanStore.subscribe,
          harness.spanStore.getSnapshot,
          harness.spanStore.getSnapshot,
        ),
      ),
  };
});

vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(({ compareType, enabled }, _ref) => (
      <div
        data-testid={`${compareType}-trace-grid`}
        data-enabled={String(enabled)}
      >
        <button
          type="button"
          onClick={() =>
            harness.traceStore.set({
              selectAll: true,
              toggledNodes: ["trace-excluded"],
            })
          }
        >
          Header select all traces
        </button>
      </div>
    )),
  };
});

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(({ compareType, enabled }, _ref) => (
      <div
        data-testid={`${compareType}-span-grid`}
        data-enabled={String(enabled)}
      >
        <button
          type="button"
          onClick={() =>
            harness.spanStore.set({
              selectAll: true,
              toggledNodes: [harness.spanExcludedPhysicalId],
            })
          }
        >
          Header select all spans
        </button>
      </div>
    )),
  };
});

vi.mock("../ObserveToolbar", () => ({
  default: (props) => (
    <div>
      <output
        data-testid="observe-toolbar-filter-state"
        data-catalog-filter-count={props.graphFilters?.length || 0}
        data-has-eval-filter={String(props.hasEvalFilter)}
        data-show-errors={String(props.showErrors)}
        data-show-non-annotated={String(props.showNonAnnotated)}
      />
      <button
        type="button"
        onClick={() => props.onApplyExtraFilters(mixedCatalogFilters)}
      >
        Apply mixed catalog filters
      </button>
      <button type="button" onClick={props.onToggleEvalFilter}>
        Toggle eval-only
      </button>
      <button type="button" onClick={props.onToggleErrors}>
        Toggle errors
      </button>
      <button type="button" onClick={props.onToggleNonAnnotated}>
        Toggle non-annotated
      </button>
      <button
        type="button"
        onClick={(event) => props.onBulkAction("annotation-queue", event)}
      >
        Add selected rows to annotation queue
      </button>
    </div>
  ),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueuesList: () => ({
    data: {
      results: [
        {
          id: "manager-queue",
          name: "Manager Queue",
          status: "active",
          viewer_role: "manager",
          viewer_roles: ["manager"],
        },
      ],
    },
    isLoading: false,
  }),
  useAddQueueItems: () => ({ mutate: harness.addItems, isPending: false }),
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

const traceSystemFilter = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "OK",
    col_type: "SYSTEM_METRIC",
  },
};

const spanSystemFilter = {
  column_id: "span_name",
  filter_config: {
    filter_type: "text",
    filter_op: "contains",
    filter_value: "tool",
    col_type: "SYSTEM_METRIC",
  },
};

async function applyMixedFiltersAndOpenQueue(user) {
  await user.click(screen.getByRole("button", { name: /mixed catalog/i }));
  await user.click(screen.getByRole("button", { name: /eval-only/i }));
  await user.click(screen.getByRole("button", { name: /toggle errors/i }));
  await user.click(
    screen.getByRole("button", { name: /toggle non-annotated/i }),
  );

  // The queue selection snapshots the active filter set when the queue is
  // opened. Wait for React to commit every preceding toolbar interaction so
  // this test cannot submit an intentionally stale intermediate render under
  // a heavily loaded component-test worker.
  await waitFor(() => {
    const state = screen.getByTestId("observe-toolbar-filter-state");
    expect(state).toHaveAttribute(
      "data-catalog-filter-count",
      String(mixedCatalogFilters.length),
    );
    expect(state).toHaveAttribute("data-has-eval-filter", "true");
    expect(state).toHaveAttribute("data-show-errors", "true");
    expect(state).toHaveAttribute("data-show-non-annotated", "true");
  });

  await user.click(screen.getByRole("button", { name: /add selected rows/i }));
  await user.click(await screen.findByText("Manager Queue"));
}

async function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let rendered;
  await act(async () => {
    rendered = render(
      <QueryClientProvider client={queryClient}>
        <React.Suspense fallback={<div>Loading tracing view</div>}>
          <LLMTracingView />
        </React.Suspense>
      </QueryClientProvider>,
    );
  });
  return rendered;
}

describe("LLMTracingView header select-all annotation queue contract", () => {
  beforeEach(() => {
    window.localStorage?.clear();
    harness.addItems.mockReset();
    harness.selectedGraph = "primary";
    harness.selectedTab = "trace";
    harness.validatedFilters = {
      primaryTraceFilter: [traceSystemFilter],
      primarySpanFilter: [spanSystemFilter],
    };
    harness.traceStore.reset();
    harness.spanStore.reset();
  });

  it("enables only the visible compare grid", async () => {
    harness.selectedGraph = "compare";
    await renderView();

    expect(await screen.findByTestId("primary-trace-grid")).toHaveAttribute(
      "data-enabled",
      "false",
    );
    expect(screen.getByTestId("compare-trace-grid")).toHaveAttribute(
      "data-enabled",
      "true",
    );
    expect(screen.getByTestId("primary-span-grid")).toHaveAttribute(
      "data-enabled",
      "false",
    );
    expect(screen.getByTestId("compare-span-grid")).toHaveAttribute(
      "data-enabled",
      "false",
    );
  });

  it("submits trace exclusions with mixed catalog, eval-only, and display filters", async () => {
    const user = userEvent.setup();
    await renderView();

    await user.click(
      await screen.findByRole("button", {
        name: "Header select all traces",
      }),
    );
    await applyMixedFiltersAndOpenQueue(user);

    await waitFor(() => {
      expect(harness.addItems).toHaveBeenCalledWith(
        {
          queueId: "manager-queue",
          selection: {
            mode: "filter",
            source_type: "trace",
            project_id: "project-1",
            filter: [
              traceSystemFilter,
              FILTER_FOR_HAS_EVAL,
              ...mixedCatalogFilters,
              FILTER_FOR_ERRORS,
              FILTER_FOR_NON_ANNOTATED,
            ],
            exclude_ids: ["trace-excluded"],
            is_voice_call: false,
            remove_simulation_calls: false,
          },
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });
  });

  it("submits decoded span exclusions without widening the mixed filter set", async () => {
    harness.selectedTab = "spans";
    const user = userEvent.setup();
    await renderView();

    await user.click(
      await screen.findByRole("button", {
        name: "Header select all spans",
      }),
    );
    await applyMixedFiltersAndOpenQueue(user);

    await waitFor(() => {
      expect(harness.addItems).toHaveBeenCalledWith(
        {
          queueId: "manager-queue",
          selection: {
            mode: "filter",
            source_type: "observation_span",
            project_id: "project-1",
            filter: [
              spanSystemFilter,
              FILTER_FOR_HAS_EVAL,
              ...mixedCatalogFilters,
              FILTER_FOR_ERRORS,
              FILTER_FOR_NON_ANNOTATED,
            ],
            exclude_ids: ["span-excluded"],
            is_voice_call: false,
            remove_simulation_calls: false,
          },
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });
  });
});
