import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";

// No users inside the picked date range says that range was quiet, not that
// the project never sent user IDs. The real UsersView renders the real
// UsersGrid here: only AG Grid itself, the transport and the page chrome
// around the content area are replaced.
const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { filterState, getMock, gridState, header } = vi.hoisted(() => ({
  filterState: { validatedFilters: [], dateFilter: null },
  getMock: vi.fn(),
  gridState: { props: null, api: null },
  header: {
    setHeaderConfig: () => {},
    setActiveViewConfig: () => {},
    registerGetViewConfig: () => {},
  },
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, ref) {
      gridState.props = props;
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          get api() {
            return gridState.api;
          },
        }),
        [],
      );
      // eslint-disable-next-line react/prop-types
      const overlay = props.noRowsOverlayComponent?.();
      return <div data-testid="users-ag-grid">{overlay}</div>;
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => getMock(...args),
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: {
      getUsersList: () => "/projects/users/",
      getUsersAggregateGraphData: () => "/projects/users/graph/",
    },
  },
}));
vi.mock("../common", () => ({
  getUsersColumnConfig: () => [
    { field: "user_id", headerName: "User ID" },
    { field: "last_active", headerName: "Last Active" },
  ],
  userTraceRowHeightMapping: { Short: { height: 40 } },
  buildUsersRequestFilters: (filters) => filters || [],
  userDefaultFilter: {},
}));
vi.mock("../../LLMTracing/common", () => ({
  mergeCellStyle: () => () => ({}),
}));
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: (content) => content,
}));
vi.mock("../UsersEmptyScreen", () => ({
  default: () => <div>Confirmed empty users</div>,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "test-workspace" }),
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => header,
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
}));
// Production filters always end with the toolbar's created_at window; each
// test picks the window (or none) and the date option the toolbar shows.
vi.mock("../../LLMTracing/useLLMTracingFilters", () => ({
  useLLMTracingFilters: (_filters, defaultDateFilter) => ({
    validatedFilters: filterState.validatedFilters,
    filters: [],
    setFilters: vi.fn(),
    dateFilter: filterState.dateFilter ?? defaultDateFilter,
    setDateFilter: vi.fn(),
  }),
}));
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../../LLMTracing/ObserveToolbar", () => ({ default: () => null }));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

import UsersView from "../UsersView";
import useUsersStore from "../Store/usersStore";

const USERS_DOCS_URL = "https://docs.futureagi.com/docs/observe/features/users";

const dateWindow = (start, end) => ({
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: [start, end],
  },
});

const emptyUsersResponse = () => ({
  data: {
    result: {
      table: [],
      total_count: 0,
      count_is_lower_bound: false,
      total_count_is_lower_bound: false,
      has_more: false,
      next_cursor: null,
      query_complete: true,
      query_status: "complete",
    },
  },
});

const makeGridParams = () => {
  let context = {};
  return {
    request: { startRow: 0, endRow: 25, sortModel: [] },
    api: {
      hideOverlay: vi.fn(),
      showNoRowsOverlay: vi.fn(),
      applyColumnState: vi.fn(),
      deselectAll: vi.fn(),
      getGridOption: vi.fn(() => context),
      setGridOption: vi.fn((key, value) => {
        if (key === "context") context = value;
      }),
      refreshServerSide: vi.fn(),
      retryServerSideLoads: vi.fn(),
    },
    success: vi.fn(),
    fail: vi.fn(),
  };
};

// Renders the page and answers its first page with no users.
const renderEmptyFirstPage = async () => {
  getMock.mockResolvedValue(emptyUsersResponse());
  render(
    <HelmetProvider>
      <UsersView />
    </HelmetProvider>,
  );
  await waitFor(() => expect(gridState.props).not.toBeNull());
  const params = makeGridParams();
  gridState.api = params.api;
  await act(async () => {
    await gridState.props.serverSideDatasource.getRows(params);
  });
  expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
  expect(params.api.showNoRowsOverlay).toHaveBeenCalledOnce();
  return params;
};

const usersGrid = () => screen.getByTestId("users-ag-grid");

describe("Users list with no users in the picked date range", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.props = null;
    gridState.api = null;
    storedValues.clear();
    filterState.validatedFilters = [
      dateWindow("2026-11-02T00:00:00.000Z", "2026-11-10T00:00:00.000Z"),
    ];
    filterState.dateFilter = null;
    useUsersStore.setState({ searchQuery: "", sortParams: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the grid and names the default Past 7D range, not the setup screen", async () => {
    await renderEmptyFirstPage();

    // Users active ten days ago exist; this window simply has none.
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
    expect(usersGrid()).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No users for Past 7D" }),
    ).toBeVisible();
    expect(
      screen.getByText("Widen the date range to see users active earlier."),
    ).toBeVisible();
    // A project that truly never sent user IDs still finds the setup docs.
    expect(
      screen.getByRole("link", { name: "Set up user tracking" }),
    ).toHaveAttribute("href", USERS_DOCS_URL);
  });

  it("names the range the user picked", async () => {
    filterState.dateFilter = {
      dateFilter: ["2026-11-09T00:00:00.000Z", "2026-11-10T00:00:00.000Z"],
      dateOption: "Today",
    };
    filterState.validatedFilters = [
      dateWindow("2026-11-09T00:00:00.000Z", "2026-11-10T00:00:00.000Z"),
    ];
    await renderEmptyFirstPage();

    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
    expect(usersGrid()).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No users for Today" }),
    ).toBeVisible();
  });

  it("names the range when a search finds no users in it", async () => {
    useUsersStore.setState({ searchQuery: "acme" });
    await renderEmptyFirstPage();
    expect(getMock.mock.calls[0][1].params.search).toBe("acme");

    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
    expect(usersGrid()).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No matching users for Past 7D" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Change the search or filters, or widen the date range.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Set up user tracking" }),
    ).not.toBeInTheDocument();
  });

  it("still shows the setup screen when a read with no date window finds no users", async () => {
    // Only an all-time read with no filter or search proves the project
    // never sent a user ID.
    filterState.validatedFilters = [];
    await renderEmptyFirstPage();

    expect(screen.getByText("Confirmed empty users")).toBeVisible();
    expect(usersGrid()).not.toBeVisible();
  });
});
