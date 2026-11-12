import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
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
    filters: [
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "chosen cohort",
        },
      },
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: ["2026-08-01T00:00:00Z", "2026-08-03T00:00:00Z"],
        },
      },
    ],
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
        validatedFilters: harness.filters,
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
vi.mock("src/sections/develop-detail/Common/TotalRowsStatusBar", () => ({
  default: () => null,
}));
vi.mock("../SpanGrid", async () => {
  const ReactModule = await import("react");
  // Catch-all grid api: AG Grid and shared status bars call many api methods;
  // any method not listed here is a harmless vi.fn() stub.
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
    // eslint-disable-next-line react/prop-types
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
    // eslint-disable-next-line react/prop-types
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
    default: function ObserveToolbar({
      // eslint-disable-next-line react/prop-types
      bulkActions,
      selectedCount,
      onBulkAction,
      onClearSelection,
      // eslint-disable-next-line react/prop-types
      onToggleErrors,
      onAddEvals,
    }) {
      return (
        <>
          <button type="button" onClick={onToggleErrors}>
            Toggle errors
          </button>
          <button type="button" onClick={onAddEvals}>
            Add eval task
          </button>
          <BulkActionsBar
            actions={bulkActions}
            selectedCount={selectedCount}
            onAction={onBulkAction}
            onClearSelection={onClearSelection}
          />
        </>
      );
    },
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
vi.mock("../SelectAllBanner", () => ({
  // eslint-disable-next-line react/prop-types
  default: function SelectAllBanner({ visible, onSelectAll }) {
    return visible ? (
      <button type="button" onClick={onSelectAll}>
        All matching rows
      </button>
    ) : null;
  },
}));
vi.mock("../FilterChips", () => ({ default: () => null }));
vi.mock("../TracingControls", () => ({ default: () => null }));
vi.mock("../CustomColumnDialog", () => ({ default: () => null }));
vi.mock("../LLMFiltersDrawer", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// eslint-disable-next-line react/prop-types
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
async function addToDataset(user) {
  await user.click(await screen.findByRole("button", { name: "Actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Add to dataset" }),
  );
}

function lastDraft() {
  const url = harness.navigate.mock.calls.at(-1)[0];
  const draftId = new URL(url, "https://example.test").searchParams.get(
    "draft",
  );
  return JSON.parse(window.localStorage.getItem(`task-draft-${draftId}`))
    .values;
}

describe("Observe action population", () => {
  it.each(["trace", "spans"])(
    "OBS-SW-39 keeps the error-only population in %s eval drafts after toggle changes",
    async (tab) => {
      harness.selectedTab = tab;
      const user = userEvent.setup();
      await mountView();
      await user.click(screen.getByRole("button", { name: "Toggle errors" }));
      await user.click(screen.getByRole("button", { name: "Add eval task" }));
      expect(lastDraft().filters).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            propertyId: "status",
            filterConfig: expect.objectContaining({
              filterOp: "equals",
              filterValue: "ERROR",
            }),
          }),
        ]),
      );
      await user.click(screen.getByRole("button", { name: "Toggle errors" }));
      await user.click(screen.getByRole("button", { name: "Add eval task" }));
      expect(
        lastDraft().filters.some((row) => row.propertyId === "status"),
      ).toBe(false);
    },
  );

  it.each(["trace", "spans"])(
    "OBS-SW-45 carries %s all-matching filters, dates and exclusions into the preview",
    async (tab) => {
      harness.selectedTab = tab;
      const user = userEvent.setup();
      await mountView();
      await user.click(screen.getByRole("button", { name: "Toggle errors" }));
      const store = tab === "spans" ? harness.spanStore : harness.traceStore;
      const { getSpanPhysicalRowId } = await import("../spanPhysicalIdentity");
      const excluded =
        tab === "spans" ? getSpanPhysicalRowId(harness.children[0]) : "T";
      act(() => store.set({ selectAll: true, toggledNodes: [excluded] }));
      await user.click(
        await screen.findByRole("button", { name: "All matching rows" }),
      );
      await addToDataset(user);
      const kind = tab === "spans" ? "spans" : "traces";
      const ids = tab === "spans" ? ["S1"] : ["T"];
      const expectedFilters = [
        ...harness.filters,
        {
          column_id: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "ERROR",
          },
        },
      ];
      expect(openAddData).toHaveBeenCalledWith({
        source: expect.objectContaining({
          kind: "traces",
          project_id: "project-1",
          select_all: true,
          source_kind: kind,
          filters: expectedFilters,
        }),
      });
      await waitFor(() =>
        expect(harness.post).toHaveBeenCalledWith(
          "/tracer/dataset/preview_sources/",
          {
            project: "project-1",
            source_kind: kind,
            failures_only: false,
            filters: expectedFilters,
            exclude_ids: ids,
          },
        ),
      );
    },
  );
});
