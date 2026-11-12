/* eslint-disable react/prop-types */
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMTracingView from "../LLMTracingView";

// OBS-SW-115 wiring, OBS-SW-116 banner, OBS-SW-117 date carry and OBS-SW-118
// unreadable project, on the list area of the Observe Trace/Spans page.

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
  projectQuery: { data: { source: "observe" } },
  navigate: vi.fn(),
  traceGrid: {},
  spanGrid: {},
  bannerVisible: {},
  replayState: {
    openReplaySessionDrawer: {},
    setIsReplayDrawerCollapsed: vi.fn(),
    setCreatedReplay: vi.fn(),
    setReplayType: vi.fn(),
    setOpenReplaySessionDrawer: vi.fn(),
  },
  testDetailState: { setTestDetailDrawerOpen: vi.fn() },
}));

const emptyGridState = () => ({
  toggledNodes: [],
  selectAll: false,
  totalRowCount: 0,
  totalRowCountLowerBound: null,
  totalRowCountIsLowerBound: false,
});

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("react-helmet-async", async (importOriginal) => ({
  ...(await importOriginal()),
  Helmet: () => null,
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => harness.navigate,
  useParams: () => ({ observeId: "project-1" }),
}));

vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    useUrlState: (key, defaultValue) =>
      ReactModule.useState(key === "selectedTab" ? "trace" : defaultValue),
  };
});

vi.mock(
  "src/sections/project/context/ObserveHeaderContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useObserveHeader: () => harness.observeHeader,
  }),
);

vi.mock("src/api/project/project-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetProjectDetails: () => harness.projectQuery,
}));

// Each list keeps its own date window, as the real hook does in the URL.
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

vi.mock("../states", async (importOriginal) => {
  const llmState = {
    resetStates: vi.fn(),
    viewMode: "graph",
    setViewMode: vi.fn(),
  };
  return {
    ...(await importOriginal()),
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) => selector(llmState),
    useTraceGridStoreShallow: (selector) => selector(harness.traceGrid),
    useSpanGridStoreShallow: (selector) => selector(harness.spanGrid),
  };
});

// vi.mock factories are hoisted above module code, so each grid double is
// written out inside its own factory.
vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function TraceGridDouble(props, _ref) {
      const id = `${props.compareType}-trace-grid`;
      return (
        <div
          data-testid={id}
          data-range-label={props.rangeLabel ?? ""}
          data-can-widen={String(typeof props.onWidenRange === "function")}
          data-can-clear={String(typeof props.onClearFilters === "function")}
        >
          <button type="button" onClick={() => props.onWidenRange?.()}>
            {`widen ${id}`}
          </button>
        </div>
      );
    }),
  };
});

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function SpanGridDouble(props, _ref) {
      const id = `${props.compareType}-span-grid`;
      return (
        <div
          data-testid={id}
          data-range-label={props.rangeLabel ?? ""}
          data-can-widen={String(typeof props.onWidenRange === "function")}
          data-can-clear={String(typeof props.onClearFilters === "function")}
        >
          <button type="button" onClick={() => props.onWidenRange?.()}>
            {`widen ${id}`}
          </button>
        </div>
      );
    }),
  };
});

vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function CallLogsGridDouble() {
      return null;
    }),
  };
});

vi.mock("../ObserveToolbar", () => ({
  default: (props) => (
    <div>
      <output data-testid="toolbar-date-label">{props.dateLabel}</output>
      <button
        type="button"
        onClick={() =>
          props.setDateFilter((prev) => ({
            ...prev,
            dateFilter: ["2026-10-11T00:00:00", "2026-11-11T00:00:00"],
            dateOption: "30D",
          }))
        }
      >
        Pick Past 30D
      </button>
      <button type="button" onClick={() => props.onGroupByChange("span")}>
        Group by span
      </button>
      <button type="button" onClick={() => props.onGroupByChange("trace")}>
        Group by trace
      </button>
    </div>
  ),
}));

vi.mock("../SelectAllBanner", () => ({
  default: (props) => {
    harness.bannerVisible[props.noun] = Boolean(props.visible);
    return null;
  },
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAnnotationQueuesList: () => ({
      data: { results: [] },
      isLoading: false,
    }),
    useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
  }),
);

vi.mock("src/sections/annotations/queues/create-queue-drawer", () => ({
  default: () => null,
}));

vi.mock("src/sections/test-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector(harness.testDetailState),
}));

vi.mock("src/sections/projects/UsersView/useProjectFilterField", () => ({
  default: () => null,
}));

vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: () => ({ data: harness.dashboardFilterValues }),
}));

vi.mock("../useCursorAttributeInventory", async (importOriginal) => ({
  ...(await importOriginal()),
  useCursorAttributeInventory: () => ({
    attributes: harness.attributes,
    inventoryControlProps: harness.inventoryControlProps,
  }),
}));

vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));

vi.mock("src/api/project/agent-graph", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgentGraph: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    pollingPaused: false,
  }),
}));

vi.mock(
  "src/sections/projects/SessionsView/ReplaySessions/store",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useReplaySessionsStoreShallow: (selector) => selector(harness.replayState),
    useSessionsGridStore: { getState: () => ({ setToggledNodes: vi.fn() }) },
  }),
);

vi.mock("src/api/project/replay-sessions", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateReplaySessions: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
}));

// No request leaves the test: every read stays pending.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  },
  readQuery: vi.fn(() => new Promise(() => {})),
}));

vi.mock("../GraphSection/PrimaryGraph", () => ({ default: () => null }));
vi.mock("../GraphSection/AgentGraph", () => ({ default: () => null }));
vi.mock("../GraphSection/AgentPath", () => ({ default: () => null }));
vi.mock("../FilterChips", () => ({ default: () => null }));
vi.mock("../TracingControls", () => ({ default: () => null }));
vi.mock("../CustomColumnDialog", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip", async (importOriginal) => ({
  ...(await importOriginal()),
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

const dateLabel = () => screen.getByTestId("toolbar-date-label").textContent;

const click = async (name) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
};

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  harness.projectQuery = { data: { source: "observe" } };
  harness.navigate.mockReset();
  harness.traceGrid = emptyGridState();
  harness.spanGrid = emptyGridState();
  harness.bannerVisible = {};
});

describe("OBS-SW-115 empty list context", () => {
  it("hands the primary grids the range, a clear and a 30-day widen", async () => {
    await renderView();

    const primaryTrace = await screen.findByTestId("primary-trace-grid");
    expect(primaryTrace).toHaveAttribute("data-range-label", "Past 7D");
    expect(primaryTrace).toHaveAttribute("data-can-widen", "true");
    expect(primaryTrace).toHaveAttribute("data-can-clear", "true");
    expect(screen.getByTestId("primary-span-grid")).toHaveAttribute(
      "data-range-label",
      "Past 7D",
    );
    // Compare grids keep their own window and the bare overlay.
    expect(screen.getByTestId("compare-trace-grid")).toHaveAttribute(
      "data-range-label",
      "",
    );

    await click("widen primary-trace-grid");

    expect(dateLabel()).toBe("Past 30D");
    // Past 30D is not widened again.
    expect(screen.getByTestId("primary-trace-grid")).toHaveAttribute(
      "data-can-widen",
      "false",
    );
  });
});

describe("OBS-SW-116 select-all banner on an exact empty list", () => {
  it("hides the trace banner when select-all covers no rows", async () => {
    harness.traceGrid = { ...emptyGridState(), selectAll: true };
    await renderView();

    expect(harness.bannerVisible.trace).toBe(false);
  });

  it("hides the span banner when select-all covers no rows", async () => {
    harness.spanGrid = { ...emptyGridState(), selectAll: true };
    await renderView();
    await click("Group by span");

    expect(harness.bannerVisible.span).toBe(false);
  });

  it("keeps the banner while select-all covers rows", async () => {
    harness.traceGrid = {
      ...emptyGridState(),
      selectAll: true,
      totalRowCount: 3,
    };
    await renderView();

    expect(harness.bannerVisible.trace).toBe(true);
  });
});

describe("OBS-SW-117 Group traces by keeps the date window", () => {
  it("carries the picked range from Trace to Span and back", async () => {
    await renderView();
    expect(dateLabel()).toBe("Past 7D");

    await click("Pick Past 30D");
    expect(dateLabel()).toBe("Past 30D");

    await click("Group by span");
    expect(dateLabel()).toBe("Past 30D");

    await click("Group by trace");
    expect(dateLabel()).toBe("Past 30D");
  });

  it("does not overwrite the range when the grouping is already current", async () => {
    await renderView();
    await click("Pick Past 30D");

    await click("Group by trace");

    expect(dateLabel()).toBe("Past 30D");
  });
});

describe("OBS-SW-118 unreadable project", () => {
  it("explains the failed read and offers Retry and Back to projects", async () => {
    const refetch = vi.fn();
    harness.projectQuery = {
      data: undefined,
      isError: true,
      isLoading: false,
      refetch,
    };
    await renderView();

    expect(
      screen.getByText("This project could not be loaded"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "It may have been deleted, or you may not have access to it.",
      ),
    ).toBeInTheDocument();

    await click("Retry");
    expect(refetch).toHaveBeenCalledTimes(1);

    await click("Back to projects");
    expect(harness.navigate).toHaveBeenCalledWith("/dashboard/observe");
  });

  it("shows nothing extra once the project resolves", async () => {
    await renderView();

    expect(
      screen.queryByText("This project could not be loaded"),
    ).not.toBeInTheDocument();
  });
});
