import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  closeAddData,
  openAddData,
  useAddDataStore,
} from "src/sections/common/AddData/addDataStore";
import ProductionDataSheet from "src/sections/common/AddData/ProductionDataSheet";
import LLMTracingView from "../LLMTracingView";

const harness = vi.hoisted(() => {
  const createStore = () => {
    const initial = {
      toggledNodes: [],
      selectAll: false,
      totalRowCount: 2,
      totalRowCountLowerBound: 2,
      totalRowCountIsLowerBound: false,
    };
    let state = initial;
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
        state = initial;
      },
    };
  };
  return {
    selectedTab: "spans",
    spanStore: createStore(),
    traceStore: createStore(),
    children: [
      {
        project_id: "project-1",
        trace_id: "T",
        span_id: "S1",
        parent_span_id: "R",
        start_time: "2026-11-09T10:00:00.000000Z",
        name: "Child one",
      },
      {
        project_id: "project-1",
        trace_id: "T",
        span_id: "S2",
        parent_span_id: "R",
        start_time: "2026-11-09T10:00:01.000000Z",
        name: "Child two",
      },
    ],
    root: { span_id: "R", trace_id: "T", name: "Trace root" },
    empty: [],
    inventoryControlProps: {},
    projectDetail: { source: "observe" },
    observeHeader: {
      activeViewConfig: null,
      registerGetViewConfig: vi.fn(),
      setActiveViewConfig: vi.fn(),
      setHeaderConfig: vi.fn(),
    },
    replayState: {
      openReplaySessionDrawer: {},
      setIsReplayDrawerCollapsed: vi.fn(),
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    },
    testDetailState: { setTestDetailDrawerOpen: vi.fn() },
    savedViews: { custom_views: [] },
    get: vi.fn(),
    post: vi.fn(),
    navigate: vi.fn(),
    dirty: vi.fn(),
    snackbar: vi.fn(),
  };
});

/* eslint-disable react/prop-types */
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: harness.snackbar }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => harness.navigate,
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => harness.navigate,
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const ReactModule = await import("react");
  return {
    useUrlState: (key, fallback) =>
      ReactModule.useState(
        key === "selectedTab" ? harness.selectedTab : fallback,
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
        dateFilter,
        setDateFilter,
        validatedFilters: harness.empty,
      };
    },
  };
});
vi.mock("../states", async () => {
  const ReactModule = await import("react");
  const state = {
    resetStates: vi.fn(),
    viewMode: "graph",
    setViewMode: vi.fn(),
  };
  const useGrid = (store, selector) =>
    selector(
      ReactModule.useSyncExternalStore(
        store.subscribe,
        store.getSnapshot,
        store.getSnapshot,
      ),
    );
  return {
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) => selector(state),
    useSpanGridStoreShallow: (selector) => useGrid(harness.spanStore, selector),
    useTraceGridStoreShallow: (selector) =>
      useGrid(harness.traceStore, selector),
  };
});
// The row-count status bar is not under test; it formats grid counts.
vi.mock(
  "src/sections/develop-detail/Common/TotalRowsStatusBar",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  // Catch-all grid api: the view sizes the grid's columns on mount
  // (api.sizeColumnsToFit) and reads other api methods; any method not
  // listed here is a harmless vi.fn() stub.
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
  const { getSpanPhysicalRowId } = await import("../spanPhysicalIdentity");
  return {
    default: ReactModule.forwardRef(function SpanGrid({ enabled }, ref) {
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          api: stubGridApi({
            sizeColumnsToFit: vi.fn(),
            getDisplayedRowCount: () => 0,
            getSelectedNodes: () =>
              harness.children
                .filter((row) =>
                  harness.spanStore
                    .getSnapshot()
                    .toggledNodes.includes(getSpanPhysicalRowId(row)),
                )
                .map((data) => ({ data })),
          }),
        }),
        [],
      );
      if (!enabled) return null;
      return harness.children.map((row) => (
        <button
          key={row.span_id}
          type="button"
          onClick={() =>
            harness.spanStore.set({
              toggledNodes: [
                ...harness.spanStore.getSnapshot().toggledNodes,
                getSpanPhysicalRowId(row),
              ],
            })
          }
        >
          Select child {row.span_id}
        </button>
      ));
    }),
  };
});
vi.mock("../TraceGrid", async () => {
  const ReactModule = await import("react");
  return {
    default: ReactModule.forwardRef(function TraceGrid({ enabled }, ref) {
      return enabled ? (
        <button
          ref={ref}
          type="button"
          onClick={() => harness.traceStore.set({ toggledNodes: ["T"] })}
        >
          Select trace T
        </button>
      ) : null;
    }),
  };
});
// Keep the shipped selection menu and the view's real action configuration/handler.
vi.mock("../ObserveToolbar", async () => {
  const { default: BulkActionsBar } = await import("../BulkActionsBar");
  return {
    default: ({
      bulkActions,
      selectedCount,
      onBulkAction,
      onClearSelection,
    }) => (
      <BulkActionsBar
        actions={bulkActions}
        selectedCount={selectedCount}
        onAction={onBulkAction}
        onClearSelection={onClearSelection}
      />
    ),
  };
});
vi.mock("src/sections/common/AddData/addDataStore", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, openAddData: vi.fn(actual.openAddData) };
});
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: harness.empty }),
  useGetDatasetColumns: () => ({ data: harness.empty }),
}));
vi.mock("src/sections/test-detail/states", () => ({
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector(harness.testDetailState),
}));
vi.mock("src/sections/projects/UsersView/useProjectFilterField", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({ data: harness.empty }),
}));
vi.mock("../useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: harness.empty,
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
  useGetSavedViews: () => ({ data: harness.savedViews }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: harness.get, post: harness.post },
  readQuery: vi.fn(),
  endpoints: {
    project: {
      listProjects: () => "/projects/",
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
vi.mock("../LLMFiltersDrawer", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AnnotateDrawer", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Trace/TraceCompareDrawer", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);
/* eslint-enable react/prop-types */

// Mount the real hosted importer using only the state written by openAddData.
function DatasetSheetHost() {
  const { open, source, target } = useAddDataStore();
  return open ? (
    <ProductionDataSheet
      initial={{ source, target }}
      onClose={closeAddData}
      setDirty={harness.dirty}
    />
  ) : null;
}

let client;
beforeEach(() => {
  window.localStorage.clear();
  closeAddData();
  vi.clearAllMocks();
  harness.selectedTab = "spans";
  harness.spanStore.reset();
  harness.traceStore.reset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  harness.get.mockImplementation(async (path) => ({
    data: {
      result:
        path === "/projects/"
          ? { projects: [{ id: "project-1", name: "Project" }] }
          : { metadata: { total_rows: 2 } },
    },
  }));
  harness.post.mockImplementation(async (path, body) => {
    if (path === "/tracer/dataset/preview_sources/") {
      // Mirror the server's root-only trace mode, rather than always returning children.
      const items =
        body.source_kind === "spans"
          ? harness.children.filter((row) =>
              body.span_ids?.includes(row.span_id),
            )
          : [harness.root];
      return { data: { result: { items, limited: false } } };
    }
    if (path === "/tracer/dataset/add_to_new_dataset/")
      return {
        data: { result: { dataset_id: "created", status: "processing" } },
      };
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => {
  cleanup();
  closeAddData();
  client.clear();
});

async function mountView() {
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <React.Suspense fallback={<div>Loading tracing view</div>}>
          <LLMTracingView />
          <DatasetSheetHost />
        </React.Suspense>
      </QueryClientProvider>,
    );
  });
}
async function selectChildren(user, ids = ["S1", "S2"]) {
  for (const id of ids)
    await user.click(
      await screen.findByRole("button", { name: `Select child ${id}` }),
    );
}
async function addToDataset(user) {
  await user.click(await screen.findByRole("button", { name: "Actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Add to dataset" }),
  );
}

// RED specifications on both pinned bases; no runtime result is claimed here.
describe("LLMTracingView dataset span selection", () => {
  it.each([
    ["W4-C-003-A one child", ["S1"]],
    ["W4-C-003-B sibling children", ["S1", "S2"]],
  ])(
    "%s keeps selected span IDs in the dataset handoff",
    async (_label, ids) => {
      const user = userEvent.setup();
      await mountView();
      await selectChildren(user, ids);
      await addToDataset(user);
      expect(openAddData).toHaveBeenCalledExactlyOnceWith({
        source: { kind: "traces", project_id: "project-1", span_ids: ids },
      });
    },
  );

  it("W4-C-003-C previews and imports both children instead of their trace root", async () => {
    const user = userEvent.setup();
    await mountView();
    await selectChildren(user);
    await addToDataset(user);
    await waitFor(() =>
      expect(harness.post).toHaveBeenCalledWith(
        "/tracer/dataset/preview_sources/",
        {
          project: "project-1",
          source_kind: "spans",
          failures_only: false,
          span_ids: ["S1", "S2"],
        },
      ),
    );
    expect(screen.getByRole("combobox", { name: "Source" })).toHaveTextContent(
      "Spans",
    );
    // OBS-SW-190: the children ticked in the grid arrive ticked in the sheet.
    expect(
      await screen.findByRole("checkbox", { name: /Child one/ }),
    ).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Child two/ })).toBeChecked();
    expect(
      screen.queryByRole("checkbox", { name: /Trace root/ }),
    ).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Dataset name"), "Selected children");
    await user.click(
      screen.getByRole("button", { name: "Create dataset with 2 rows" }),
    );
    await waitFor(() =>
      expect(harness.post).toHaveBeenCalledWith(
        "/tracer/dataset/add_to_new_dataset/",
        expect.objectContaining({
          project: "project-1",
          span_ids: ["S1", "S2"],
        }),
      ),
    );
    await waitFor(() =>
      expect(harness.navigate).toHaveBeenCalledWith(
        "/dashboard/develop/created?tab=data",
      ),
    );
  });

  it("W4-C-003-D retains trace-tab root preview semantics", async () => {
    harness.selectedTab = "trace";
    const user = userEvent.setup();
    await mountView();
    await user.click(
      await screen.findByRole("button", { name: "Select trace T" }),
    );
    await addToDataset(user);
    expect(openAddData).toHaveBeenCalledExactlyOnceWith({
      source: { kind: "traces", project_id: "project-1", trace_ids: ["T"] },
    });
    await waitFor(() =>
      expect(harness.post).toHaveBeenCalledWith(
        "/tracer/dataset/preview_sources/",
        {
          project: "project-1",
          source_kind: "traces",
          failures_only: false,
          trace_ids: ["T"],
        },
      ),
    );
    expect(
      await screen.findByRole("checkbox", { name: /Trace root/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /Child one/ }),
    ).not.toBeInTheDocument();
  });

  it("W4-C-003-E preserves sibling IDs through the direct dataset button", async () => {
    const user = userEvent.setup();
    await mountView();
    await selectChildren(user);
    fireEvent.click(
      screen.getByRole("button", { name: "Add to dataset", hidden: true }),
    );
    expect(openAddData).toHaveBeenCalledWith({
      source: {
        kind: "traces",
        project_id: "project-1",
        span_ids: ["S1", "S2"],
      },
    });
  });

  it("W4-C-003-F does not open an unpinned preview for an invalid direct selection", async () => {
    await mountView();
    act(() =>
      harness.spanStore.set({ toggledNodes: ["not-a-physical-row-id"] }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add to dataset", hidden: true }),
    );
    expect(openAddData).not.toHaveBeenCalled();
    expect(harness.post).not.toHaveBeenCalled();
    expect(harness.snackbar).toHaveBeenCalledWith(
      "Selected spans are missing their canonical identity. Refresh the data and retry.",
      { variant: "error" },
    );
  });
});
