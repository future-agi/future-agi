import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionsView from "../Sessions-view";
import UsersView from "../../UsersView/UsersView";
import useUsersStore from "../../UsersView/Store/usersStore";
import { initialVisibility } from "../common";
import { getUsersColumnConfig } from "../../UsersView/common";
import { REPLAY_ACTION_ID } from "../ReplaySessions/constants";
import { toBackendFilters } from "../../LLMTracing/common";

const h = vi.hoisted(() => ({
  toolbar: null,
  grid: null,
  navigate: vi.fn(),
  replay: vi.fn(),
  save: vi.fn(),
  visibilitySave: vi.fn(),
  selection: { toggledNodes: [], selectAll: false },
  header: {
    activeViewConfig: null,
    setHeaderConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    registerGetViewConfig: vi.fn(),
  },
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ observeId: "project-1" }),
  useLocation: () => ({ pathname: "/dashboard/observe/project-1/users" }),
  useNavigate: () => h.navigate,
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => h.header,
}));
vi.mock("../../LLMTracing/useLLMTracingFilters", async () => {
  const { useState } = await import("react");
  return {
    useLLMTracingFilters: (initialFilters, initialDate) => {
      const [filters, setFilters] = useState(initialFilters);
      const [dateFilter, setDateFilter] = useState(initialDate);
      const validatedFilters = React.useMemo(
        () => [
          {
            column_id: "created_at",
            filter_config: {
              filter_type: "datetime",
              filter_op: "between",
              filter_value: ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
            },
          },
        ],
        [],
      );
      return {
        filters,
        setFilters,
        dateFilter,
        setDateFilter,
        validatedFilters,
      };
    },
  };
});
vi.mock("../../LLMTracing/ObserveToolbar", () => ({
  default: (props) => {
    h.toolbar = props;
    return null;
  },
}));
vi.mock("../Session-grid", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function MockSessionGrid(props, _ref) {
      h.grid = props;
      return null;
    }),
  };
});
vi.mock("../../UsersView/UsersGrid", () => ({ default: () => null }));
vi.mock("../../UsersView/UsersEmptyScreen", () => ({ default: () => null }));
vi.mock("../../UsersView/useProjectFilterField", () => ({
  default: () => null,
}));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/SelectAllBanner", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({ default: () => null }));
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
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
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    }),
  useSessionsGridStore: {
    setState: (state) => Object.assign(h.selection, state),
  },
  useSessionsGridStoreShallow: (selector) => selector(h.selection),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    default: {
      ...original.default,
      post: (...args) => h.visibilitySave(...args),
    },
  };
});
vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({ mutate: h.replay }),
}));
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: h.save }),
  useUpdateWorkspaceSavedView: () => ({ mutate: h.save }),
}));

const aliceFilter = {
  column_id: "user_id",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "Alice",
  },
};
const saved = (columnState = []) => ({
  extra_filters: [],
  display: {
    cellHeight: "Short",
    showCompare: false,
    dateFilter: {
      dateOption: "Custom",
      dateFilter: ["2026-10-01", "2026-10-02"],
    },
    columnState,
  },
});
const mount = (Component = SessionsView) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <Component />
      </React.Suspense>
    </QueryClientProvider>,
  );
const makeApi = (initialState = []) => {
  let state = initialState;
  const listeners = new Map();
  return {
    getColumnState: () => state,
    getColumnDefs: vi.fn(() => []),
    sizeColumnsToFit: vi.fn(),
    autoSizeColumns: vi.fn(),
    isDestroyed: vi.fn(() => false),
    applyColumnState: vi.fn(({ state: next }) => {
      state = next;
    }),
    resetColumnState: vi.fn(() => {
      state = [];
    }),
    setColumnsVisible: vi.fn(),
    refreshServerSide: vi.fn(),
    deselectAll: vi.fn(),
    addEventListener: vi.fn((name, fn) => listeners.set(name, fn)),
    removeEventListener: vi.fn((name) => listeners.delete(name)),
    sort(next) {
      state = next;
      listeners.get("sortChanged")?.();
    },
  };
};

beforeEach(() => {
  vi.clearAllMocks();
  h.visibilitySave.mockResolvedValue({ data: {} });
  localStorage.clear();
  window.history.replaceState({}, "", "/?tab=view-saved");
  h.header.activeViewConfig = saved();
  h.toolbar = null;
  h.grid = null;
  h.selection = { toggledNodes: [], selectAll: false, totalRowCount: 2 };
  useUsersStore.setState({ columns: [], gridApi: null, searchQuery: "" });
});
afterEach(cleanup);

describe("Observe grouping leaves the saved baseline behind", () => {
  it.each(["trace", "span", "users"])(
    "Sessions clears context before grouping by %s",
    async (key) => {
      mount();
      await waitFor(() => expect(h.toolbar).not.toBeNull());
      act(() => h.toolbar.onGroupByChange(key));
      expect(h.header.setActiveViewConfig).toHaveBeenCalledWith(null);
      expect(
        h.header.setActiveViewConfig.mock.invocationCallOrder[0],
      ).toBeLessThan(h.navigate.mock.invocationCallOrder[0]);
    },
  );
  it.each(["trace", "span", "sessions"])(
    "Users clears context before grouping by %s",
    async (key) => {
      mount(UsersView);
      await waitFor(() => expect(h.toolbar).not.toBeNull());
      act(() => h.toolbar.onGroupByChange(key));
      expect(h.header.setActiveViewConfig).toHaveBeenCalledWith(null);
      expect(
        h.header.setActiveViewConfig.mock.invocationCallOrder[0],
      ).toBeLessThan(h.navigate.mock.invocationCallOrder[0]);
    },
  );
  it.each([SessionsView, UsersView])(
    "ignores the current grouping",
    async (Component) => {
      mount(Component);
      await waitFor(() => expect(h.toolbar).not.toBeNull());
      act(() => h.toolbar.onGroupByChange(h.toolbar.groupBy));
      expect(h.navigate).not.toHaveBeenCalled();
      expect(h.header.setActiveViewConfig).not.toHaveBeenCalled();
    },
  );
});

describe("Sessions saved sort", () => {
  it("detects, saves and undoes a sort-only edit", async () => {
    const ascending = [{ colId: "total_cost", sort: "asc", sortIndex: 0 }];
    const descending = [{ colId: "total_cost", sort: "desc", sortIndex: 0 }];
    h.header.activeViewConfig = saved(ascending);
    const api = makeApi();
    const view = mount();
    await waitFor(() => expect(h.grid).not.toBeNull());
    act(() => h.grid.onGridReady({ api }));
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(false));
    act(() => api.sort(descending));
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(true));
    act(() => h.toolbar.onSaveView());
    expect(h.save.mock.calls[0][0].config.display.columnState).toEqual(
      descending,
    );
    act(() => api.sort(ascending));
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(false));
    view.unmount();
    expect(api.removeEventListener).toHaveBeenCalledWith(
      "sortChanged",
      expect.any(Function),
    );
  });
  it("detects multi-sort precedence without confusing column order with sort order", async () => {
    const baseline = [
      { colId: "total_cost", sort: "asc", sortIndex: 1 },
      { colId: "duration", sort: "desc", sortIndex: 0 },
    ];
    h.header.activeViewConfig = saved(baseline);
    const api = makeApi();
    mount();
    await waitFor(() => expect(h.grid).not.toBeNull());
    act(() => h.grid.onGridReady({ api }));
    act(() => api.sort([...baseline].reverse()));
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(false));
    act(() =>
      api.sort(
        baseline.map((col) => ({ ...col, sortIndex: 1 - col.sortIndex })),
      ),
    );
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(true));
  });
});

describe("Observe Reset restores canonical settings", () => {
  it("persists default Sessions visibility before refreshing the grid", async () => {
    h.header.activeViewConfig = null;
    window.history.replaceState({}, "", "/?tab=sessions");
    const api = makeApi();
    mount();
    await waitFor(() => expect(h.grid).not.toBeNull());
    act(() => h.grid.onGridReady({ api }));
    act(() => h.toolbar.setCellHeight("Extra Large"));
    expect(h.toolbar.onResetView).toEqual(expect.any(Function));
    act(() => h.toolbar.onResetView());
    await waitFor(() =>
      expect(h.visibilitySave).toHaveBeenCalledWith(expect.any(String), {
        project_id: "project-1",
        visibility: initialVisibility,
      }),
    );
    await waitFor(() =>
      expect(api.refreshServerSide).toHaveBeenCalledWith({ purge: true }),
    );
    expect(h.toolbar.cellHeight).toBe("Short");
  });
  it("resets Sessions filters, height, comparison and queued columns without losing the saved baseline", async () => {
    h.header.activeViewConfig = {
      ...saved(),
      extra_filters: [aliceFilter],
      display: {
        ...saved().display,
        cellHeight: "Extra Large",
        showCompare: true,
        visibleColumns: { session_id: false },
        customColumns: [{ id: "custom.test", groupBy: "Custom Columns" }],
        columnState: [{ colId: "session_id", hide: true }],
      },
    };
    const api = makeApi();
    mount();
    await waitFor(() => expect(h.toolbar.cellHeight).toBe("Extra Large"));
    act(() =>
      h.grid.setColumns([
        { id: "session_id", isVisible: false },
        { id: "custom.test", groupBy: "Custom Columns" },
      ]),
    );
    act(() => h.grid.onGridReady({ api }));
    expect(h.toolbar.onResetView).toEqual(expect.any(Function));
    act(() => h.toolbar.onResetView());
    await waitFor(() => expect(h.toolbar.cellHeight).toBe("Short"));
    expect(h.toolbar.graphFilters).toEqual([]);
    expect(h.toolbar.isCompareActive).toBe(false);
    expect(h.grid.updateObj).toEqual(initialVisibility);
    expect(h.grid.columns.map((c) => c.id)).toEqual(["session_id"]);
    expect(h.grid.pendingCustomColumnsRef.current).toEqual([]);
    expect(api.resetColumnState).toHaveBeenCalled();
    expect(h.header.setActiveViewConfig).not.toHaveBeenCalled();
    expect(h.toolbar.canSaveView).toBe(true);
  });
  it("resets Users despite stored preferences and queued saved visibility", async () => {
    h.header.activeViewConfig = {
      ...saved(),
      extra_filters: [aliceFilter],
      display: {
        ...saved().display,
        cellHeight: "Extra Large",
        visibleColumns: { user_id: false },
      },
    };
    localStorage.setItem(
      "observe-users-display-project-1",
      JSON.stringify({ cellHeight: "Extra Large" }),
    );
    const api = makeApi();
    useUsersStore.setState({ gridApi: api });
    mount(UsersView);
    await waitFor(() => expect(h.toolbar.cellHeight).toBe("Extra Large"));
    expect(h.toolbar.onResetView).toEqual(expect.any(Function));
    act(() => h.toolbar.onResetView());
    const defaults = getUsersColumnConfig({ includeProject: false });
    act(() =>
      useUsersStore.setState({
        columns: defaults.map((c) => ({ id: c.field, isVisible: !c.hide })),
      }),
    );
    await waitFor(() => expect(h.toolbar.cellHeight).toBe("Short"));
    expect(h.toolbar.graphFilters).toEqual([]);
    expect(h.toolbar.columns.find((c) => c.id === "user_id").isVisible).toBe(
      true,
    );
    expect(api.resetColumnState).toHaveBeenCalled();
    expect(h.header.setActiveViewConfig).not.toHaveBeenCalled();
  });
});

describe("Replay uses the displayed population", () => {
  it("sends canonical filters and explicit exclusions for select-all", async () => {
    h.header.activeViewConfig = { ...saved(), extra_filters: [aliceFilter] };
    h.selection = {
      selectAll: true,
      toggledNodes: ["excluded-session"],
      totalRowCount: 2,
    };
    mount();
    await waitFor(() => expect(h.toolbar.graphFilters).toEqual([aliceFilter]));
    act(() => h.toolbar.onBulkAction(REPLAY_ACTION_ID));
    expect(h.replay.mock.calls[0][0]).toMatchObject({
      project_id: "project-1",
      select_all: true,
      ids: [],
      exclude_ids: ["excluded-session"],
      filters: toBackendFilters(h.grid.filters),
    });
  });
  it("does not treat loaded selected rows as exclusions when the inverted list is empty", async () => {
    const view = mount();
    await waitFor(() => expect(h.grid).not.toBeNull());
    act(() =>
      h.grid.onSelectionChanged({
        api: {
          getServerSideSelectionState: () => ({
            selectAll: true,
            toggledNodes: [],
          }),
          getSelectedNodes: () => [
            { data: { session_id: "included-session" } },
          ],
          totalRowCount: 2,
        },
      }),
    );
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <SessionsView />
      </QueryClientProvider>,
    );
    act(() => h.toolbar.onBulkAction(REPLAY_ACTION_ID));
    expect(h.replay.mock.calls[0][0]).toMatchObject({
      select_all: true,
      ids: [],
      exclude_ids: [],
    });
  });
});
