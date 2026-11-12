/* eslint-disable react/prop-types */
import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import LLMTracingView from "../LLMTracingView";

// Voice screen bulk actions. OBS-SW-133: "Add tags" starts from each call's
// stored tags (trace detail nests them under `trace`) and never wipes a call
// whose tags could not be read. OBS-SW-158: replay, dataset and tags act on
// picked calls, so they must not run on one page while the bar says every
// matching call is selected.

const harness = vi.hoisted(() => ({
  addDataCalls: [],
  attributes: [],
  bannerProps: [],
  callLogsGridProps: [],
  dashboardFilterValues: [],
  emptyFilters: [],
  gridState: {
    toggledNodes: [],
    selectAll: false,
    totalRowCount: 0,
    totalRowCountLowerBound: null,
    totalRowCountIsLowerBound: false,
  },
  inventoryControlProps: {},
  navigate: () => {},
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: () => {},
    setActiveViewConfig: () => {},
    setHeaderConfig: () => {},
  },
  projectQuery: { data: { source: "simulator" } },
  replayCalls: [],
  replayState: {
    openReplaySessionDrawer: {},
    setIsReplayDrawerCollapsed: () => {},
    setCreatedReplay: () => {},
    setReplayType: () => {},
    setOpenReplaySessionDrawer: () => {},
  },
  snackbars: [],
  tagsPopoverProps: [],
  testDetailState: { setTestDetailDrawerOpen: () => {} },
  toolbarProps: [],
}));

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Admin", user: {} }),
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

vi.mock("../useLLMTracingFilters", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
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
    resetStates: () => {},
    viewMode: "graph",
    setViewMode: () => {},
  };
  return {
    ...(await importOriginal()),
    resetSpanGridStore: () => {},
    resetTraceGridStore: () => {},
    useLLMTracingStoreShallow: (selector) => selector(llmState),
    useTraceGridStoreShallow: (selector) => selector(harness.gridState),
    useSpanGridStoreShallow: (selector) => selector(harness.gridState),
  };
});

vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function TraceGridDouble() {
      return null;
    }),
  };
});

vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function SpanGridDouble() {
      return null;
    }),
  };
});

vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function CallLogsGridDouble(props) {
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

vi.mock("../SelectAllBanner", () => ({
  default: (props) => {
    harness.bannerProps.push(props);
    return null;
  },
}));

vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: (props) => {
    harness.tagsPopoverProps.push(props);
    return null;
  },
}));

vi.mock("src/sections/common/AddData/addDataStore", async (importOriginal) => ({
  ...(await importOriginal()),
  openAddData: (opts) => harness.addDataCalls.push(opts),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => harness.snackbars.push(args),
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAnnotationQueuesList: () => ({
      data: { results: [] },
      isLoading: false,
    }),
    useAddQueueItems: () => ({ mutate: () => {}, isPending: false }),
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
    useSessionsGridStore: { getState: () => ({ setToggledNodes: () => {} }) },
  }),
);

vi.mock("src/api/project/replay-sessions", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateReplaySessions: () => ({
    mutate: (...args) => harness.replayCalls.push(args),
    isPending: false,
  }),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: () => {} }),
  useUpdateSavedView: () => ({ mutate: () => {} }),
  useUpdateWorkspaceSavedView: () => ({ mutate: () => {} }),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
}));

// No request leaves the test: reads stay pending unless a test answers them.
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

const pendingRead = () => new Promise(() => {});

const traceRead = (id) => endpoints.project.getTrace(id);

const enabledCallGrid = () =>
  harness.callLogsGridProps.findLast((props) => props.enabled);

const callsBanner = () =>
  harness.bannerProps.findLast((props) => props.noun === "call");

const lastToolbar = () => harness.toolbarProps.at(-1);

const bulkAction = (actionId) =>
  lastToolbar().bulkActions.find((action) => action.id === actionId);

async function renderVoiceView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <React.Suspense fallback={null}>
          <LLMTracingView />
        </React.Suspense>
      </QueryClientProvider>,
    );
  });
  await waitFor(() => expect(enabledCallGrid()).toBeTruthy());
}

async function pickCalls(ids, meta = {}) {
  await act(async () => {
    const grid = enabledCallGrid();
    grid.onSelectionChanged(ids);
    grid.onSelectionMeta({
      selectedCount: ids.length,
      unavailableSelectedCount: 0,
      isAllOnPageSelected: false,
      currentPageSize: ids.length,
      totalPages: 1,
      pageLimit: 50,
      totalMatchingIsLowerBound: false,
      ...meta,
    });
  });
}

async function runBulkAction(actionId) {
  await act(async () => {
    lastToolbar().onBulkAction(actionId, { currentTarget: document.body });
  });
}

beforeEach(() => {
  harness.addDataCalls = [];
  harness.bannerProps = [];
  harness.callLogsGridProps = [];
  harness.replayCalls = [];
  harness.snackbars = [];
  harness.tagsPopoverProps = [];
  harness.toolbarProps = [];
  axios.get.mockClear();
  axios.get.mockImplementation(pendingRead);
});

describe("LLMTracingView voice bulk Add tags", () => {
  it("starts each call from its stored tags and leaves out unreadable calls", async () => {
    const stored = {
      [traceRead("trace-a")]: ["vip"],
      [traceRead("trace-b")]: [{ name: "billing", color: "#3B82F6" }],
    };
    axios.get.mockImplementation((url) => {
      if (url in stored)
        return Promise.resolve({
          data: { result: { trace: { tags: stored[url] } } },
        });
      if (url === traceRead("trace-c"))
        return Promise.reject(new Error("read failed"));
      return pendingRead();
    });
    await renderVoiceView();
    await pickCalls(["trace-a", "trace-b", "trace-c"]);

    await runBulkAction("tags");

    await waitFor(() =>
      expect(harness.tagsPopoverProps.at(-1)?.open).toBe(true),
    );
    expect(harness.tagsPopoverProps.at(-1).bulkItems).toEqual([
      { id: "trace-a", type: "trace", currentTags: ["vip"] },
      {
        id: "trace-b",
        type: "trace",
        currentTags: [{ name: "billing", color: "#3B82F6" }],
      },
    ]);
    expect(harness.snackbars).toContainEqual([
      "Couldn't read the tags of 1 call; they were left out",
      { variant: "warning" },
    ]);
  });

  it("opens nothing when no selected call's tags could be read", async () => {
    axios.get.mockImplementation((url) =>
      url === traceRead("trace-a")
        ? Promise.reject(new Error("read failed"))
        : pendingRead(),
    );
    await renderVoiceView();
    await pickCalls(["trace-a"]);

    await runBulkAction("tags");

    await waitFor(() =>
      expect(harness.snackbars).toContainEqual([
        "Couldn't read the selected calls' tags. Try again.",
        { variant: "error" },
      ]),
    );
    expect(harness.tagsPopoverProps.some((props) => props.open)).toBe(false);
  });
});

describe("LLMTracingView voice actions with all matching calls selected", () => {
  it("does not replay, export or tag only the page's calls", async () => {
    await renderVoiceView();
    await pickCalls(["trace-a", "trace-b"], {
      isAllOnPageSelected: true,
      totalPages: 3,
      pageLimit: 2,
      totalMatching: 6,
    });
    await act(async () => {
      callsBanner().onSelectAll();
    });
    await waitFor(() => expect(lastToolbar().allMatching).toBe(true));

    for (const actionId of ["replay", "dataset", "tags"]) {
      expect(bulkAction(actionId)).toMatchObject({
        disabled: true,
        disabledReason: expect.stringContaining("all matching calls"),
      });
      await runBulkAction(actionId);
    }

    expect(harness.replayCalls).toEqual([]);
    expect(harness.addDataCalls).toEqual([]);
    expect(axios.get).not.toHaveBeenCalledWith(
      traceRead("trace-a"),
      expect.anything(),
    );
    expect(harness.tagsPopoverProps.some((props) => props.open)).toBe(false);
    expect(harness.snackbars).toContainEqual([
      "Not available for all matching calls. Pick specific calls instead.",
      { variant: "info" },
    ]);
  });

  it("still acts on the picked calls otherwise", async () => {
    await renderVoiceView();
    await pickCalls(["trace-a", "trace-b"]);

    expect(bulkAction("dataset").disabled).toBeFalsy();
    await runBulkAction("dataset");
    await runBulkAction("replay");

    expect(harness.addDataCalls.at(-1).source.trace_ids).toEqual([
      "trace-a",
      "trace-b",
    ]);
    expect(harness.replayCalls.at(-1)[0]).toMatchObject({
      ids: ["trace-a", "trace-b"],
      select_all: false,
    });
  });
});
