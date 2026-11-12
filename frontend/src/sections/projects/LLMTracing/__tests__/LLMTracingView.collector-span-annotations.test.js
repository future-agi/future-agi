import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import LLMTracingView from "../LLMTracingView";

// Exercise the real view, AnnotationSidebarContent, LabelInput and bulk-score
// hook. Hook/store/API modules are partial mocks (real exports kept, only the
// listed hooks replaced). Default-export-only render units that are not under
// test (grids, toolbar, graphs, pickers) are replaced whole.
const h = React.createElement;
const QUALITY_PLACEHOLDER = "Describe the span quality";
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
      set: (next) => {
        state = { ...state, ...next };
        listeners.forEach((listener) => listener());
      },
      reset: () => {
        state = initialState;
        listeners.forEach((listener) => listener());
      },
    };
  };
  const initialState = {
    toggledNodes: [],
    selectAll: false,
    totalRowCount: 5,
    totalRowCountLowerBound: 5,
    totalRowCountIsLowerBound: false,
  };
  return {
    spanStore: createStore(initialState),
    traceState: initialState,
    selectedGraph: "primary",
    empty: [],
    inventoryControlProps: {},
    projectDetail: { source: "observe" },
    observeHeader: {
      activeViewConfig: null,
      registerGetViewConfig: vi.fn(),
      setActiveViewConfig: vi.fn(),
      setHeaderConfig: vi.fn(),
    },
    llmState: { resetStates: vi.fn(), viewMode: "graph", setViewMode: vi.fn() },
    replayState: {
      openReplaySessionDrawer: {},
      setIsReplayDrawerCollapsed: vi.fn(),
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    },
    testDetailState: { setTestDetailDrawerOpen: vi.fn() },
    queueRead: vi.fn(),
    refreshPrimary: vi.fn(),
    refreshCompare: vi.fn(),
    queueItems: [
      {
        queue: {
          id: "queue-1",
          name: "Collector span review",
          instructions: "",
        },
        item: {
          id: "item-1",
          status: "pending",
          source_type: "observation_span",
        },
        labels: [
          {
            id: "quality",
            name: "Quality",
            type: "text",
            settings: { placeholder: "Describe the span quality" },
          },
        ],
        existing_scores: { quality: { text: "existing collector score" } },
        existing_notes: "existing span note",
        existing_label_notes: {},
      },
    ],
  };
});

/* eslint-disable react/prop-types */
// --- Partial mocks: hook, store, API and library modules -------------------
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
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    useUrlState: (key, defaultValue) =>
      ReactModule.useState(
        key === "selectedTab"
          ? "spans"
          : key === "selectedGraph"
            ? harness.selectedGraph
            : defaultValue,
      ),
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
  useGetProjectDetails: () => ({ data: harness.projectDetail }),
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
        validatedFilters: harness.empty,
        dateFilter,
        setDateFilter,
      };
    },
  };
});
vi.mock("../states", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) => selector(harness.llmState),
    useTraceGridStoreShallow: (selector) => selector(harness.traceState),
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
// Queue discovery is the seam; the real useBulkCreateScores stays in place.
vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAnnotationQueuesList: () => ({
      data: { results: [] },
      isLoading: false,
    }),
    useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
    useQueueItemsForSource: (sources, options) => {
      harness.queueRead(sources, options);
      return {
        data: harness.queueItems,
        isLoading: false,
        isFetching: false,
        refetch: vi.fn(),
      };
    },
  }),
);
// Partial: CallLogsGrid and other test-detail consumers import resetState too.
vi.mock("src/sections/test-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector(harness.testDetailState),
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: () => ({ data: harness.empty }),
}));
vi.mock("../useCursorAttributeInventory", async (importOriginal) => ({
  ...(await importOriginal()),
  useCursorAttributeInventory: () => ({
    attributes: harness.empty,
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
vi.mock("src/sections/common/AddData/addDataStore", async (importOriginal) => ({
  ...(await importOriginal()),
  openAddData: vi.fn(),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
// Only the HTTP transport is replaced; endpoints and helpers stay real.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));

// --- Whole replacements: default-export-only render units not under test ---
vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});
vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  // Catch-all grid api: the view calls many optional api methods; any method
  // not listed here is a harmless vi.fn() stub.
  const stubGridApi = (methods) =>
    new Proxy(methods, {
      get: (target, key) => {
        if (
          key in target ||
          typeof key === "symbol" ||
          key === "then" ||
          key === "toJSON"
        ) {
          return target[key];
        }
        target[key] = vi.fn(() => undefined);
        return target[key];
      },
    });
  return {
    default: ReactModule.forwardRef(({ compareType }, ref) => {
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          api: stubGridApi({
            isDestroyed: () => false,
            sizeColumnsToFit: vi.fn(),
            getDisplayedRowCount: () => 0,
            refreshServerSide:
              compareType === "primary"
                ? harness.refreshPrimary
                : harness.refreshCompare,
          }),
        }),
        [compareType],
      );
      return null;
    }),
  };
});
// Simulator-only call grid, mounted hidden with enabled=false for an Observe
// project. Its real unmount resets the test-detail store; not under test.
vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef((_props, _ref) => null) };
});
// The row-count status bar is not under test; it formats grid counts.
vi.mock("src/sections/develop-detail/Common/TotalRowsStatusBar", () => ({
  default: () => null,
}));
vi.mock("../ObserveToolbar", () => ({
  default: ({ onBulkAction }) =>
    h(
      "button",
      {
        type: "button",
        onClick: (event) => onBulkAction("annotate", event),
      },
      "Annotate selected span",
    ),
}));
vi.mock("src/sections/annotations/queues/create-queue-drawer", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/UsersView/useProjectFilterField", () => ({
  default: () => null,
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
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: () => null,
}));
// Legacy editor: rendering it means the view took the pre-fix path.
vi.mock("src/components/traceDetailDrawer/AnnotateDrawer", () => ({
  default: ({ open }) =>
    open ? h("div", null, "Legacy span annotations") : null,
}));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);
/* eslint-enable react/prop-types */

const physicalRowId = (spanId = "collector-span", projectId = "project-1") =>
  JSON.stringify([
    projectId,
    "collector-trace",
    spanId,
    "2026-11-09T10:00:00.000000Z",
  ]);
const clients = [];
const successfulSave = {
  data: { result: { scores: [{ id: "score-1" }], errors: [] } },
};

async function renderView(props = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  await act(async () => {
    render(
      h(
        QueryClientProvider,
        { client },
        h(React.Suspense, { fallback: null }, h(LLMTracingView, props)),
      ),
    );
  });
  // Opening alone must not refresh rows; only a successful score write does.
  harness.refreshPrimary.mockClear();
  harness.refreshCompare.mockClear();
}

// The real LabelInput renders the label name as unassociated text and its
// text field without a label, so the field is reached by its placeholder.
async function openAnnotation() {
  fireEvent.click(
    await screen.findByRole("button", { name: "Annotate selected span" }),
  );
  expect(
    await screen.findByText("Quality", undefined, { timeout: 5000 }),
  ).toBeInTheDocument();
  const input = screen.getByPlaceholderText(QUALITY_PLACEHOLDER);
  await waitFor(() => expect(input).toHaveValue("existing collector score"));
  return input;
}

// Load the drawer's lazy chunk before mounting so a slow runner cannot turn
// a late Suspense resolution into a timeout.
beforeAll(async () => {
  await import("src/components/traceDetailDrawer/AnnotationSidebarContent");
});
beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
  harness.selectedGraph = "primary";
  harness.spanStore.reset();
  harness.spanStore.set({ toggledNodes: [physicalRowId()] });
  axios.get.mockReset();
  axios.post.mockReset();
  // Collector-only fixture: the legacy endpoint is never a successful fallback.
  axios.post.mockImplementation((url) =>
    url === "/model-hub/scores/bulk/"
      ? Promise.resolve(successfulSave)
      : Promise.reject(new Error("Observation span not found")),
  );
});
afterEach(() => {
  vi.useRealTimers();
  clients.splice(0).forEach((client) => client.clear());
});

describe("OBS-SW-21 collector span grid annotations", () => {
  it("prefills canonical queue scores for the selected span and Observe project", async () => {
    await renderView();
    expect(harness.queueRead).not.toHaveBeenCalled();
    await openAnnotation();
    expect(harness.queueRead).toHaveBeenLastCalledWith(
      [
        {
          sourceType: "observation_span",
          sourceId: "collector-span",
          projectId: "project-1",
        },
      ],
      { projectId: "project-1" },
    );
    expect(screen.getByPlaceholderText("Add your notes here...")).toHaveValue(
      "existing span note",
    );
    expect(
      screen.queryByText("Legacy span annotations"),
    ).not.toBeInTheDocument();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it.each(["primary", "compare"])(
    "saves canonical bulk scores from the %s grid and refreshes after success",
    async (selectedGraph) => {
      harness.selectedGraph = selectedGraph;
      let resolveSave;
      axios.post.mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveSave = resolve;
          }),
      );
      await renderView();
      const input = await openAnnotation();
      // Save straight after typing: the pending 300 ms text debounce is flushed.
      fireEvent.change(input, { target: { value: "approved" } });
      fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
      await waitFor(() =>
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/scores/bulk/",
          {
            source_type: "observation_span",
            source_id: "collector-span",
            project_id: "project-1",
            queue_item_id: "item-1",
            scores: [
              {
                label_id: "quality",
                value: { text: "approved" },
                notes: "",
                score_source: "human",
              },
            ],
            notes: "",
            span_notes: "existing span note",
            span_notes_source_id: "collector-span",
          },
          expect.anything(),
        ),
      );
      expect(harness.refreshPrimary).not.toHaveBeenCalled();
      expect(harness.refreshCompare).not.toHaveBeenCalled();
      await act(async () => {
        resolveSave(successfulSave);
      });
      await waitFor(() => {
        expect(harness.refreshPrimary).toHaveBeenCalledTimes(1);
        expect(harness.refreshCompare).toHaveBeenCalledTimes(1);
      });
      expect(axios.post).toHaveBeenCalledTimes(1);
    },
  );

  // OBS-SW-192: the score reaches the list API through CDC up to ~8 s after
  // the save, so the refresh fired on success can still read "-". Grid
  // auto-refresh is off by default, so the view re-reads the Spans grids on
  // its own until the score can have landed, then stops.
  it("re-reads the Spans grids after the score's CDC copy can land, then stops", async () => {
    let resolveSave;
    axios.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );
    await renderView();
    const input = await openAnnotation();
    fireEvent.change(input, { target: { value: "approved" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(harness.refreshPrimary).not.toHaveBeenCalled();

    // Fake timeouts only once the lazy drawer is loaded; no waitFor below.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const advance = async (ms) => {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    };
    await act(async () => {
      resolveSave(successfulSave);
    });
    // Let the save's success chain settle; no follow-up is due before 2 s.
    await advance(100);
    expect(harness.refreshPrimary).toHaveBeenCalledTimes(1);
    expect(harness.refreshCompare).toHaveBeenCalledTimes(1);

    await advance(8000);
    const refreshesBy8s = harness.refreshPrimary.mock.calls.length;
    await advance(2000);
    const refreshesBy10s = harness.refreshPrimary.mock.calls.length;
    expect(refreshesBy10s).toBeGreaterThan(refreshesBy8s);
    expect(harness.refreshCompare).toHaveBeenCalledTimes(refreshesBy10s);

    await advance(60000);
    expect(harness.refreshPrimary).toHaveBeenCalledTimes(refreshesBy10s);
    expect(harness.refreshCompare).toHaveBeenCalledTimes(refreshesBy10s);
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("keeps the original span and row project when selection changes in user mode", async () => {
    harness.spanStore.set({
      toggledNodes: [physicalRowId("collector-span", "project-b")],
    });
    await renderView({ mode: "user", userIdForUserMode: "user-1" });
    await openAnnotation();
    act(() =>
      harness.spanStore.set({
        toggledNodes: [physicalRowId("other-span", "project-c")],
      }),
    );
    expect(harness.queueRead).toHaveBeenLastCalledWith(
      [
        {
          sourceType: "observation_span",
          sourceId: "collector-span",
          projectId: "project-b",
        },
      ],
      { projectId: "project-b" },
    );
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/model-hub/scores/bulk/",
        expect.objectContaining({
          source_id: "collector-span",
          project_id: "project-b",
        }),
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(harness.refreshPrimary).toHaveBeenCalledTimes(1),
    );
  });

  it("keeps edits for retry and does not refresh when canonical saving fails", async () => {
    axios.post.mockRejectedValueOnce(new Error("Score write failed"));
    await renderView();
    const input = await openAnnotation();
    fireEvent.change(input, { target: { value: "keep this edit" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Score write failed", {
        variant: "error",
      }),
    );
    expect(input).toHaveValue("keep this edit");
    expect(harness.refreshPrimary).not.toHaveBeenCalled();
    expect(harness.refreshCompare).not.toHaveBeenCalled();
    const save = screen.getByRole("button", { name: /^Save/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() =>
      expect(harness.refreshPrimary).toHaveBeenCalledTimes(1),
    );
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post).toHaveBeenLastCalledWith(
      "/model-hub/scores/bulk/",
      expect.objectContaining({
        scores: [
          {
            label_id: "quality",
            value: { text: "keep this edit" },
            notes: "",
            score_source: "human",
          },
        ],
      }),
      expect.anything(),
    );
    expect(
      axios.post.mock.calls.every(([url]) => url === "/model-hub/scores/bulk/"),
    ).toBe(true);
  });

  it.each([
    ["empty", [], false],
    ["multiple", [physicalRowId(), physicalRowId("second-span")], false],
    ["select-all", [physicalRowId()], true],
    ["malformed", ["bare-span-id"], false],
  ])(
    "does not open annotations for a %s selection",
    async (_name, ids, selectAll) => {
      harness.spanStore.set({ toggledNodes: ids, selectAll });
      await renderView();
      fireEvent.click(
        await screen.findByRole("button", { name: "Annotate selected span" }),
      );
      await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalled());
      expect(harness.queueRead).not.toHaveBeenCalled();
      expect(
        screen.queryByPlaceholderText(QUALITY_PLACEHOLDER),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("Legacy span annotations"),
      ).not.toBeInTheDocument();
      expect(axios.post).not.toHaveBeenCalled();
    },
  );
});
