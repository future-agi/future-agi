import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";

// The real UsersView renders the real UsersGrid here: only AG Grid itself,
// the transport and the page chrome around the content area are replaced.
const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { getMock, gridState, header } = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { props: null, api: null },
  header: {
    setHeaderConfig: vi.fn(),
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
      return <div data-testid="users-ag-grid" />;
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
vi.mock("../../LLMTracing/useLLMTracingFilters", async () => {
  const filters = [];
  return {
    useLLMTracingFilters: (_filters, dateFilter) => ({
      validatedFilters: filters,
      filters,
      setFilters: vi.fn(),
      dateFilter,
      setDateFilter: vi.fn(),
    }),
  };
});
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
import * as listCursorPagination from "../../LLMTracing/listCursorPagination";
import { OBSERVE_LIST_REFRESH_EVENT } from "../../observeEvents";

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

const renderUsersView = async () => {
  render(
    <HelmetProvider>
      <UsersView />
    </HelmetProvider>,
  );
  await waitFor(() => expect(gridState.props).not.toBeNull());
};

const usersGrid = () => screen.getByTestId("users-ag-grid");

describe("Users view content visibility", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.props = null;
    gridState.api = null;
    storedValues.clear();
    header.setHeaderConfig.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each(["manual", "auto"])(
    "%s refresh reads a fresh first page instead of replaying cached users",
    async (refreshKind) => {
      const previousRows = [{ user_id: "previous", end_user_id: "previous" }];
      const freshRows = [{ user_id: "fresh", end_user_id: "fresh" }];
      const response = (rows) => ({
        data: {
          result: {
            ...emptyUsersResponse().data.result,
            table: rows,
            total_count: rows.length,
          },
        },
      });
      let resolveRefresh;
      getMock.mockResolvedValueOnce(response(previousRows)).mockReturnValueOnce(
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
      );
      await renderUsersView();
      const params = makeGridParams();
      gridState.api = params.api;
      act(() => gridState.props.onGridReady({ api: params.api }));
      await act(async () => {
        await gridState.props.serverSideDatasource.getRows(params);
      });
      expect(params.success).toHaveBeenCalledWith({
        rowData: previousRows,
        rowCount: 1,
      });

      act(() => {
        if (refreshKind === "manual") {
          header.setHeaderConfig.mock.calls.at(-1)[0]({}).refreshData();
        } else {
          window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT));
        }
      });
      expect(params.api.refreshServerSide).toHaveBeenCalledWith({
        purge: false,
      });
      const freshParams = makeGridParams();
      gridState.api = freshParams.api;
      let pendingRefresh;
      act(() => {
        pendingRefresh =
          gridState.props.serverSideDatasource.getRows(freshParams);
      });
      await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
      expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor");
      expect(getMock.mock.calls[1][1].params.current_page_index).toBe(0);
      expect(usersGrid()).toBeVisible();
      expect(freshParams.success).not.toHaveBeenCalled();
      await act(async () => {
        resolveRefresh(response(freshRows));
        await pendingRefresh;
      });
      expect(freshParams.success).toHaveBeenCalledWith({
        rowData: freshRows,
        rowCount: 1,
      });
    },
  );

  it("manual refresh cancels an older read without publishing its users", async () => {
    let resolvePrevious;
    getMock
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolvePrevious = resolve;
        }),
      )
      .mockResolvedValueOnce(emptyUsersResponse());
    await renderUsersView();
    const previousParams = makeGridParams();
    gridState.api = previousParams.api;
    act(() => gridState.props.onGridReady({ api: previousParams.api }));
    let previousRead;
    act(() => {
      previousRead =
        gridState.props.serverSideDatasource.getRows(previousParams);
    });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    act(() => header.setHeaderConfig.mock.calls.at(-1)[0]({}).refreshData());
    expect(getMock.mock.calls[0][1].signal.aborted).toBe(true);
    const freshParams = makeGridParams();
    gridState.api = freshParams.api;
    await act(async () => {
      await gridState.props.serverSideDatasource.getRows(freshParams);
      resolvePrevious({
        data: {
          result: {
            ...emptyUsersResponse().data.result,
            table: [{ user_id: "stale", end_user_id: "stale" }],
            total_count: 1,
          },
        },
      });
      await previousRead;
    });
    expect(getMock).toHaveBeenCalledTimes(2);
    expect(freshParams.success).toHaveBeenCalledWith({
      rowData: [],
      rowCount: 0,
    });
    expect(previousParams.success).not.toHaveBeenCalled();
    expect(previousParams.fail).toHaveBeenCalledOnce();
  });

  it("keeps the grid on screen while a refresh reloads the confirmed-empty page", async () => {
    getMock.mockResolvedValue(emptyUsersResponse());
    await renderUsersView();
    const params = makeGridParams();
    gridState.api = params.api;
    await act(async () => {
      await gridState.props.serverSideDatasource.getRows(params);
    });
    expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
    expect(screen.getByText("Confirmed empty users")).toBeVisible();
    expect(usersGrid()).not.toBeVisible();

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).toHaveBeenCalledWith({
      purge: false,
    });

    // AG Grid answers the refresh with a page-0 read. While it is loading the
    // empty screen steps aside; the grid must take its place, not nothing.
    const refreshParams = makeGridParams();
    gridState.api = refreshParams.api;
    let refreshRead;
    act(() => {
      refreshRead = gridState.props.serverSideDatasource.getRows(refreshParams);
    });
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
    expect(usersGrid()).toBeVisible();

    await act(async () => {
      await refreshRead;
    });
    expect(refreshParams.success).toHaveBeenCalledWith({
      rowData: [],
      rowCount: 0,
    });
    expect(screen.getByText("Confirmed empty users")).toBeVisible();
  });

  it("keeps the grid on screen while a search reloads the confirmed-empty page", async () => {
    // A search is a new query: its page-0 read goes to the network while
    // searchState is still "empty".
    let resolveSearch;
    getMock.mockResolvedValueOnce(emptyUsersResponse()).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSearch = resolve;
      }),
    );
    await renderUsersView();
    const params = makeGridParams();
    gridState.api = params.api;
    await act(async () => {
      await gridState.props.serverSideDatasource.getRows(params);
    });
    expect(screen.getByText("Confirmed empty users")).toBeVisible();
    const emptyDatasource = gridState.props.serverSideDatasource;

    act(() => useUsersStore.setState({ searchQuery: "acme" }));
    expect(gridState.props.serverSideDatasource).not.toBe(emptyDatasource);
    const searchParams = makeGridParams();
    gridState.api = searchParams.api;
    let searchRead;
    act(() => {
      searchRead = gridState.props.serverSideDatasource.getRows(searchParams);
    });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    expect(getMock.mock.calls[1][1].params.search).toBe("acme");
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
    expect(usersGrid()).toBeVisible();

    await act(async () => {
      resolveSearch(emptyUsersResponse());
      await searchRead;
    });
    expect(searchParams.success).toHaveBeenCalledWith({
      rowData: [],
      rowCount: 0,
    });
    // A search with no match is not the project's empty state.
    expect(usersGrid()).toBeVisible();
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
  });

  it("shows Continue search when the first page stops at the continuation limit", async () => {
    const limit = new Error("Exact list continuation safety limit reached");
    limit.code = listCursorPagination.LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE;
    vi.spyOn(listCursorPagination, "loadExactListPage").mockRejectedValueOnce(
      limit,
    );
    await renderUsersView();
    const params = makeGridParams();
    gridState.api = params.api;
    await act(async () => {
      await gridState.props.serverSideDatasource.getRows(params);
    });

    // The bounded read paused before it established whether users exist.
    expect(params.fail).toHaveBeenCalledOnce();
    expect(params.success).not.toHaveBeenCalled();
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
    expect(usersGrid()).toBeVisible();
    const continueButton = screen.getByRole("button", {
      name: "Continue search",
    });
    expect(continueButton).toBeVisible();

    act(() => continueButton.click());
    expect(params.api.retryServerSideLoads).toHaveBeenCalledOnce();
  });

  it.each([
    [
      "a wider date range",
      {
        filters: [
          {
            column_id: "created_at",
            filter_config: {
              filter_type: "datetime",
              filter_op: "between",
              filter_value: ["2026-01-01", "2026-09-01"],
            },
          },
        ],
      },
    ],
    ["a search", { searchQuery: "acme" }],
  ])(
    "shows Continue search, not the empty screen, when %s from the confirmed-empty page pauses page 0",
    async (_trigger, storeChange) => {
      getMock.mockResolvedValue(emptyUsersResponse());
      await renderUsersView();
      const params = makeGridParams();
      gridState.api = params.api;
      await act(async () => {
        await gridState.props.serverSideDatasource.getRows(params);
      });
      expect(screen.getByText("Confirmed empty users")).toBeVisible();
      const emptyDatasource = gridState.props.serverSideDatasource;

      // The new query's first page stops at the continuation limit: it has
      // not established whether any user exists for that query.
      act(() => useUsersStore.setState(storeChange));
      expect(gridState.props.serverSideDatasource).not.toBe(emptyDatasource);
      const limit = new Error("Exact list continuation safety limit reached");
      limit.code =
        listCursorPagination.LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE;
      vi.spyOn(listCursorPagination, "loadExactListPage").mockRejectedValueOnce(
        limit,
      );
      const pausedParams = makeGridParams();
      gridState.api = pausedParams.api;
      await act(async () => {
        await gridState.props.serverSideDatasource.getRows(pausedParams);
      });

      expect(pausedParams.fail).toHaveBeenCalledOnce();
      expect(pausedParams.success).not.toHaveBeenCalled();
      expect(
        screen.queryByText("Confirmed empty users"),
      ).not.toBeInTheDocument();
      expect(usersGrid()).toBeVisible();
      const continueButton = screen.getByRole("button", {
        name: "Continue search",
      });
      expect(continueButton).toBeVisible();

      act(() => continueButton.click());
      expect(pausedParams.api.retryServerSideLoads).toHaveBeenCalledOnce();
    },
  );
});
