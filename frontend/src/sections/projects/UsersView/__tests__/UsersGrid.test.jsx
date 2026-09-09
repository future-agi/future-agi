import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { getMock, gridState, routeState, storeState, validated } = vi.hoisted(
  () => {
    const validated = [
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            "2026-03-01T00:00:00.000Z",
            "2026-06-01T00:00:00.000Z",
          ],
        },
      },
    ];
    return {
      getMock: vi.fn(),
      gridState: { props: null, api: null },
      routeState: { observeId: "proj-1" },
      validated,
      storeState: {
        setGridApi: vi.fn(),
        searchQuery: "",
        selectedAll: false,
        selectedRowsData: [],
        setSelectedAll: vi.fn(),
        setSelectedRowsData: vi.fn(),
        clearSelection: vi.fn(),
        columns: [],
        setColumns: vi.fn(),
        filters: validated,
        setFilters: vi.fn(),
      },
    };
  },
);

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, _ref) {
      gridState.props = props;
      ReactModule.useImperativeHandle(
        _ref,
        () => ({
          get api() {
            return gridState.api;
          },
        }),
        [],
      );
      return (
        <div data-testid="ag-grid">{props.noRowsOverlayComponent?.()}</div>
      );
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("../Store/usersStore", async () => {
  const { useSyncExternalStore } = await import("react");
  const listeners = new Set();
  const subscribe = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  };
  storeState.setFilters.mockImplementation((filters) => {
    storeState.filters = filters;
    listeners.forEach((listener) => listener());
  });
  const useUsersStore = () => {
    // A Zustand filter update must reach React.memo without changing props.
    useSyncExternalStore(subscribe, () => storeState.filters);
    return storeState;
  };
  useUsersStore.setState = vi.fn();
  return { default: useUsersStore };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("../common", () => ({
  getUsersColumnConfig: () => [
    { field: "last_active", headerName: "Last Active" },
    { field: "total_cost", headerName: "Total Cost" },
    { field: "num_sessions", headerName: "No. of Sessions" },
    { field: "avg_trace_latency", headerName: "Avg Latency" },
    { field: "eval_score", headerName: "Evals Pass Rate" },
  ],
  userTraceRowHeightMapping: { Short: { height: 40 } },
  buildUsersRequestFilters: (filters) => filters,
}));
vi.mock("../../LLMTracing/common", () => ({
  mergeCellStyle: () => () => ({}),
}));
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: (content) => content,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: routeState.observeId }),
}));
vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => getMock(...args),
  default: { get: (...args) => getMock(...args) },
  endpoints: { project: { getUsersList: () => "/projects/users/" } },
}));

import UsersGrid from "../UsersGrid";
import {
  OBSERVE_LIST_REFRESH_EVENT,
  OBSERVE_PAGE_CHANGED_EVENT,
} from "../../observeEvents";

const usersResponse = ({
  rows = [],
  totalCount = rows.length,
  countIsLowerBound = false,
  hasMore = false,
  nextCursor = null,
  queryComplete = !hasMore,
  queryStatus = queryComplete ? "complete" : "degraded",
} = {}) => ({
  data: {
    result: {
      table: rows,
      total_count: totalCount,
      count_is_lower_bound: countIsLowerBound,
      total_count_is_lower_bound: countIsLowerBound,
      has_more: hasMore,
      next_cursor: nextCursor,
      query_complete: queryComplete,
      query_status: queryStatus,
    },
  },
});

const row = (number) => ({
  user_id: `user-${number}`,
  end_user_id: `end-user-${number}`,
});

const makeGridParams = ({ startRow = 0, endRow = 25, sortModel = [] } = {}) => {
  let context = {};
  const api = {
    hideOverlay: vi.fn(),
    showNoRowsOverlay: vi.fn(),
    applyColumnState: vi.fn(),
    getGridOption: vi.fn(() => context),
    setGridOption: vi.fn((key, value) => {
      if (key === "context") context = value;
    }),
    refreshServerSide: vi.fn(),
    retryServerSideLoads: vi.fn(),
  };
  return {
    request: { startRow, endRow, sortModel },
    api,
    success: vi.fn(),
    fail: vi.fn(),
  };
};

const renderGrid = (overrides = {}) => {
  const props = {
    hasActiveFilter: false,
    setHasData: vi.fn(),
    setIsLoading: vi.fn(),
    setSearchState: vi.fn(),
    cellHeight: "Short",
    ...overrides,
  };
  render(<UsersGrid {...props} />);
  return props;
};

const readPage = async (params) => {
  gridState.api = params.api;
  await act(async () => {
    await gridState.props.serverSideDatasource.getRows(params);
  });
};

describe("UsersGrid deterministic pagination", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.props = null;
    gridState.api = null;
    routeState.observeId = "proj-1";
    storeState.searchQuery = "";
    storeState.filters = validated;
    storeState.columns = [];
    storeState.clearSelection.mockReset();
    localStorage.clear();
  });

  it("uses project and end-user identity for cross-project AG Grid rows", () => {
    renderGrid();

    const sharedUser = {
      user_id: "shared@example.com",
      end_user_id: "shared-end-user-id",
    };
    const firstId = gridState.props.getRowId({
      data: { ...sharedUser, project_id: "project-a" },
    });
    const secondId = gridState.props.getRowId({
      data: { ...sharedUser, project_id: "project-b" },
    });

    expect(firstId).toBe("project-a:shared-end-user-id");
    expect(secondId).toBe("project-b:shared-end-user-id");
    expect(firstId).not.toBe(secondId);
  });

  it("bounds retained blocks and concurrent server-side reads", () => {
    renderGrid();

    expect(gridState.props.pagination).toBe(true);
    expect(gridState.props.paginationPageSize).toBe(25);
    expect(gridState.props.paginationPageSizeSelector).toEqual([
      10, 25, 50, 100,
    ]);
    expect(gridState.props.cacheBlockSize).toBe(25);
    expect(gridState.props.maxBlocksInCache).toBe(5);
    expect(gridState.props.maxConcurrentDatasourceRequests).toBe(1);
  });

  it("preserves visible users on auto refresh and reports page navigation", () => {
    renderGrid();
    const params = makeGridParams();
    params.api.paginationGetCurrentPage = vi.fn(() => 0);
    gridState.api = params.api;
    const pageChanged = vi.fn();
    window.addEventListener(OBSERVE_PAGE_CHANGED_EVENT, pageChanged);

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).toHaveBeenCalledWith({ purge: false });

    params.api.refreshServerSide.mockClear();
    params.api.paginationGetCurrentPage.mockReturnValue(1);
    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).not.toHaveBeenCalled();
    expect(pageChanged.mock.calls[0][0].detail).toEqual({ page: 2 });

    gridState.props.onPaginationChanged({
      api: { paginationGetCurrentPage: () => 1 },
    });
    expect(pageChanged).toHaveBeenCalledTimes(2);
    expect(pageChanged.mock.calls[1][0].detail).toEqual({ page: 2 });
    window.removeEventListener(OBSERVE_PAGE_CHANGED_EVENT, pageChanged);
  });

  it("does not stack user auto refreshes while a read is pending", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );
    renderGrid();
    const params = makeGridParams();
    gridState.api = params.api;
    let pendingRead;
    act(() => {
      pendingRead = gridState.props.serverSideDatasource.getRows(params);
    });
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).not.toHaveBeenCalled();

    await act(async () => {
      resolveResponse(usersResponse());
      await pendingRead;
    });
  });

  it("opts the first unsorted request into cursor mode with the active filters", async () => {
    getMock.mockResolvedValue(usersResponse());
    renderGrid();

    const params = makeGridParams();
    await readPage(params);

    expect(getMock).toHaveBeenCalledTimes(1);
    const [url, config] = getMock.mock.calls[0];
    expect(url).toBe("/projects/users/");
    expect(config.params).toEqual(
      expect.objectContaining({
        project_id: "proj-1",
        filters: JSON.stringify(validated),
        current_page_index: 0,
        cursor_mode: true,
        page_size: 25,
        requested_columns: JSON.stringify([
          "last_active",
          "total_cost",
          "num_sessions",
          "avg_trace_latency",
          "eval_score",
        ]),
        attribute_keys: "[]",
      }),
    );
    expect(config.params).not.toHaveProperty("cursor");
    expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
  });

  it("uses the opaque cursor for page N and never sends a numbered page with it", async () => {
    const firstRows = Array.from({ length: 25 }, (_, index) => row(index));
    getMock
      .mockResolvedValueOnce(
        usersResponse({
          rows: firstRows,
          totalCount: 26,
          countIsLowerBound: true,
          hasMore: true,
          nextCursor: "signed-users-page-2",
        }),
      )
      .mockResolvedValueOnce(usersResponse({ rows: [row(25)] }));
    renderGrid();

    const firstPage = makeGridParams();
    await readPage(firstPage);
    const secondPage = makeGridParams({ startRow: 25, endRow: 50 });
    await readPage(secondPage);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "signed-users-page-2",
        page_size: 25,
      }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty(
      "current_page_index",
    );
    expect(secondPage.success).toHaveBeenCalledWith({
      rowData: [row(25)],
      rowCount: 26,
    });
  });

  it("follows sparse empty checkpoints before publishing a visible page", async () => {
    getMock
      .mockResolvedValueOnce(
        usersResponse({
          hasMore: true,
          nextCursor: "checkpoint-1",
          countIsLowerBound: true,
        }),
      )
      .mockResolvedValueOnce(
        usersResponse({
          rows: [row(20)],
          totalCount: 2,
          countIsLowerBound: true,
          hasMore: true,
          nextCursor: "signed-users-page-2",
        }),
      )
      .mockResolvedValueOnce(usersResponse({ rows: [row(21)] }));
    renderGrid();

    const firstPage = makeGridParams();
    await readPage(firstPage);

    expect(firstPage.success).toHaveBeenCalledTimes(1);
    expect(firstPage.success).toHaveBeenCalledWith({
      rowData: [row(20), row(21)],
      rowCount: 2,
    });
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({ cursor_mode: true, cursor: "checkpoint-1" }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty(
      "current_page_index",
    );

    expect(getMock.mock.calls[2][1].params.cursor).toBe("signed-users-page-2");
  });

  it("stops automatic retries at the bound and preserves the manual retry cursor", async () => {
    Array.from({ length: 13 }, (_, index) =>
      usersResponse({
        hasMore: true,
        nextCursor: `checkpoint-${index}`,
        countIsLowerBound: true,
      }),
    ).forEach((response) => getMock.mockResolvedValueOnce(response));
    getMock.mockResolvedValueOnce(usersResponse({ rows: [row(88)] }));
    const props = renderGrid();

    const boundedRound = makeGridParams();
    const getLocaleText = gridState.props.getLocaleText;
    boundedRound.fail.mockImplementation(() => {
      // The grid reads these labels inside fail(), before React renders the
      // continuation banner. CSS cannot replace the failed-row ARIA label.
      expect(getLocaleText({ key: "loadingError", defaultValue: "ERR" })).toBe(
        "Preparing exact results. Refresh or retry to continue.",
      );
      expect(
        getLocaleText({
          key: "ariaSkeletonCellLoadingFailed",
          defaultValue: "Row failed to load",
        }),
      ).toBe("Preparing exact results. Refresh or retry to continue.");
    });
    await readPage(boundedRound);

    expect(getMock).toHaveBeenCalledTimes(13);
    expect(boundedRound.success).not.toHaveBeenCalled();
    expect(boundedRound.api.showNoRowsOverlay).not.toHaveBeenCalled();
    expect(boundedRound.fail).toHaveBeenCalledTimes(1);
    expect(boundedRound.api.retryServerSideLoads).not.toHaveBeenCalled();
    expect(props.setHasData).not.toHaveBeenCalledWith(false);
    expect(props.setIsLoading).toHaveBeenCalledWith(false);
    expect(props.setSearchState).not.toHaveBeenCalledWith("error");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Preparing exact results. Refresh or retry to continue.",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(gridState.props.className).toContain("ag-grid-cursor-paused");
    expect(gridState.props.noRowsOverlayComponent()).toBeNull();
    expect(gridState.props.getLocaleText).toBe(getLocaleText);
    expect(
      getLocaleText({ key: "noRowsToShow", defaultValue: "No Rows" }),
    ).toBe("No Rows");

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );
    expect(boundedRound.api.retryServerSideLoads).toHaveBeenCalledOnce();
    expect(boundedRound.api.refreshServerSide).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
    expect(gridState.props.className).not.toContain("ag-grid-cursor-paused");
    expect(
      getLocaleText({
        key: "ariaSkeletonCellLoadingFailed",
        defaultValue: "Row failed to load",
      }),
    ).toBe("Row failed to load");

    // A deliberate retry resumes the retained exact checkpoint. The bounded
    // automatic read itself never spins or publishes a false empty page.
    const resumedPage = makeGridParams();
    await readPage(resumedPage);

    expect(getMock.mock.calls[13][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-12",
      }),
    );
    expect(resumedPage.success).toHaveBeenCalledWith({
      rowData: [row(88)],
      rowCount: 1,
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("retains a short intermediate block and overflow across a continuation pause", async () => {
    Array.from({ length: 13 }, (_, index) =>
      usersResponse({
        rows: [row(index)],
        totalCount: index + 1,
        countIsLowerBound: true,
        hasMore: true,
        nextCursor: `checkpoint-${index}`,
      }),
    ).forEach((response) => getMock.mockResolvedValueOnce(response));
    getMock
      .mockResolvedValueOnce(
        usersResponse({
          // Include the boundary row again to exercise buffered deduplication.
          rows: Array.from({ length: 14 }, (_, index) => row(index + 12)),
          totalCount: 26,
          countIsLowerBound: true,
          hasMore: true,
          nextCursor: "after-user-25",
        }),
      )
      .mockResolvedValueOnce(
        usersResponse({ rows: [row(26)], totalCount: 27 }),
      );
    const props = renderGrid();
    const boundedRound = makeGridParams();

    await readPage(boundedRound);

    expect(getMock).toHaveBeenCalledTimes(13);
    expect(boundedRound.success).not.toHaveBeenCalled();
    expect(boundedRound.api.setGridOption).not.toHaveBeenCalled();
    expect(boundedRound.api.showNoRowsOverlay).not.toHaveBeenCalled();
    expect(storeState.clearSelection).not.toHaveBeenCalled();
    expect(props.setHasData).not.toHaveBeenCalledWith(false);

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );
    const resumedPage = makeGridParams();
    await readPage(resumedPage);

    expect(getMock.mock.calls[13][1].params.cursor).toBe("checkpoint-12");
    expect(getMock.mock.calls[13][1].params).not.toHaveProperty(
      "current_page_index",
    );
    expect(resumedPage.success).toHaveBeenCalledWith({
      rowData: Array.from({ length: 25 }, (_, index) => row(index)),
      rowCount: 26,
    });

    const secondPage = makeGridParams({ startRow: 25, endRow: 50 });
    await readPage(secondPage);

    expect(getMock).toHaveBeenCalledTimes(15);
    expect(getMock.mock.calls[14][1].params.cursor).toBe("after-user-25");
    expect(secondPage.success).toHaveBeenCalledWith({
      rowData: [row(25), row(26)],
      rowCount: 27,
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it.each(["complete", "failure"])(
    "renders a real AG Grid continuation pause as preparing before a %s retry",
    async (outcome) => {
      const { createGrid, ModuleRegistry } = await import("ag-grid-community");
      const { AllEnterpriseModule } = await import("ag-grid-enterprise");
      ModuleRegistry.registerModules([AllEnterpriseModule]);
      Array.from({ length: 13 }, (_, index) =>
        usersResponse({
          hasMore: true,
          nextCursor: `checkpoint-${index}`,
          countIsLowerBound: true,
        }),
      ).forEach((response) => getMock.mockResolvedValueOnce(response));
      if (outcome === "complete") {
        getMock.mockResolvedValueOnce(usersResponse({ rows: [row(88)] }));
      } else {
        getMock.mockRejectedValueOnce({ response: { status: 500 } });
      }
      renderGrid();
      const host = document.createElement("div");
      document.body.appendChild(host);
      let api;
      try {
        act(() => {
          api = createGrid(host, {
            theme: "legacy",
            domLayout: "autoHeight",
            columnDefs: [{ field: "user_id" }],
            rowModelType: "serverSide",
            cacheBlockSize: 25,
            maxConcurrentDatasourceRequests: 1,
            serverSideInitialRowCount: 5,
            suppressServerSideFullWidthLoadingRow: true,
            getLocaleText: gridState.props.getLocaleText,
            serverSideDatasource: gridState.props.serverSideDatasource,
          });
          gridState.api = api;
        });

        await waitFor(() => {
          expect(
            host.querySelector(
              '[aria-label="Preparing exact results. Refresh or retry to continue."]',
            ),
          ).not.toBeNull();
        });
        expect(
          host.querySelector('[aria-label="Row failed to load"]'),
        ).toBeNull();
        expect(host.textContent).not.toContain("ERR");
        expect(getMock).toHaveBeenCalledTimes(13);

        await userEvent.click(
          screen.getByRole("button", { name: "Continue search" }),
        );
        if (outcome === "complete") {
          await waitFor(() =>
            expect(api.getDisplayedRowAtIndex(0)?.data).toEqual(row(88)),
          );
          expect(api.getDisplayedRowCount()).toBe(1);
        } else {
          await waitFor(() =>
            expect(
              host.querySelector('[aria-label="Row failed to load"]'),
            ).not.toBeNull(),
          );
          expect(screen.getByRole("alert")).toHaveTextContent(
            "We couldn't load this data. Please retry in a moment.",
          );
        }
        expect(getMock).toHaveBeenCalledTimes(14);
        expect(getMock.mock.calls[13][1].params.cursor).toBe("checkpoint-12");
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
      } finally {
        act(() => api?.destroy());
        host.remove();
      }
    },
  );

  it("clears explicit sorts and keeps deterministic cursor pagination", async () => {
    getMock
      .mockResolvedValueOnce(
        usersResponse({
          hasMore: true,
          nextCursor: "signed-users-page-2",
          countIsLowerBound: true,
        }),
      )
      .mockResolvedValueOnce(usersResponse());
    renderGrid();

    const sortModel = [{ colId: "total_cost", sort: "desc" }];
    const firstPage = makeGridParams({ sortModel });
    await readPage(firstPage);
    const secondPage = makeGridParams({ startRow: 25, endRow: 50, sortModel });
    await readPage(secondPage);

    expect(getMock.mock.calls[0][1].params).toEqual(
      expect.objectContaining({
        current_page_index: 0,
        sort_params: "[]",
        cursor_mode: true,
      }),
    );
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        sort_params: "[]",
        cursor_mode: true,
        cursor: "signed-users-page-2",
      }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty(
      "current_page_index",
    );
  });

  it("disables every global sort control until a bounded sort exists", () => {
    storeState.columns = [
      { id: "last_active", isVisible: true },
      { id: "total_cost", isVisible: true },
      { id: "num_sessions", isVisible: true },
      { id: "avg_trace_latency", isVisible: true },
      { id: "eval_score", isVisible: true },
      {
        id: "customer_tier",
        name: "Customer tier",
        isVisible: true,
        groupBy: "Custom Columns",
      },
    ];

    renderGrid();

    const sortability = Object.fromEntries(
      gridState.props.columnDefs.map(({ colId, sortable }) => [
        colId,
        sortable,
      ]),
    );
    expect(sortability).toEqual({
      last_active: false,
      total_cost: false,
      num_sessions: false,
      avg_trace_latency: false,
      eval_score: false,
      customer_tier: false,
    });
  });

  it("clears a stale unsupported stored sort without sending it to the API", async () => {
    localStorage.setItem(
      "ag-grid-sort-model-proj-1",
      JSON.stringify([{ colId: "num_sessions", sort: "desc" }]),
    );
    getMock.mockResolvedValue(usersResponse());
    renderGrid();

    const params = makeGridParams({
      sortModel: [{ colId: "num_sessions", sort: "desc" }],
    });
    await readPage(params);

    expect(localStorage.getItem("ag-grid-sort-model-proj-1")).toBeNull();
    expect(params.api.applyColumnState).toHaveBeenLastCalledWith({
      state: [],
      defaultState: { sort: null },
    });
    expect(getMock.mock.calls[0][1].params).toEqual(
      expect.objectContaining({
        sort_params: "[]",
        cursor_mode: true,
      }),
    );
  });

  it("clears all stored sorts from the previously unbounded contract", async () => {
    localStorage.setItem(
      "ag-grid-sort-model-proj-1",
      JSON.stringify([
        { colId: "num_sessions", sort: "desc" },
        { colId: "total_cost", sort: "asc" },
      ]),
    );
    getMock.mockResolvedValue(usersResponse());
    renderGrid();

    const params = makeGridParams();
    await readPage(params);

    expect(localStorage.getItem("ag-grid-sort-model-proj-1")).toBeNull();
    expect(params.api.applyColumnState).toHaveBeenCalledWith({
      state: [],
      defaultState: { sort: null },
    });
  });

  it("fails closed on a repeated checkpoint instead of returning a false empty page", async () => {
    getMock
      .mockResolvedValueOnce(
        usersResponse({ hasMore: true, nextCursor: "same-checkpoint" }),
      )
      .mockResolvedValueOnce(
        usersResponse({ hasMore: true, nextCursor: "same-checkpoint" }),
      );
    const props = renderGrid();
    const params = makeGridParams();

    await readPage(params);

    expect(params.fail).toHaveBeenCalledTimes(1);
    expect(params.success).not.toHaveBeenCalled();
    expect(props.setSearchState).toHaveBeenCalledWith("error");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't load this data. Please retry in a moment.",
    );
  });

  it.each([
    {
      status: 500,
      detail: "Code: 159. DB::Exception: Timeout exceeded. private stack trace",
    },
    {
      status: 422,
      detail: "user_sort_unsupported: internal derived sort details",
    },
  ])(
    "sanitizes HTTP $status and preserves selection instead of accepting an empty result",
    async ({ status, detail }) => {
      getMock.mockRejectedValue({
        response: {
          status,
          data: { detail },
        },
      });
      renderGrid();
      const params = makeGridParams();

      await readPage(params);

      expect(params.fail).toHaveBeenCalledTimes(1);
      expect(params.success).not.toHaveBeenCalled();
      expect(storeState.clearSelection).not.toHaveBeenCalled();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "We couldn't load this data. Please retry in a moment.",
      );
      expect(screen.queryByText(/DB::Exception/i)).not.toBeInTheDocument();
      expect(
        screen.queryByText(/user_sort_unsupported/i),
      ).not.toBeInTheDocument();
    },
  );

  it("treats a superseded request cancellation as neutral", async () => {
    getMock.mockRejectedValueOnce(
      Object.assign(new Error("canceled"), { code: "ERR_CANCELED" }),
    );
    const props = renderGrid();
    const params = makeGridParams();

    await readPage(params);

    expect(params.fail).not.toHaveBeenCalled();
    expect(params.success).not.toHaveBeenCalled();
    expect(props.setSearchState).not.toHaveBeenCalledWith("error");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("fails a degraded HTTP 200 instead of accepting a false empty Users page", async () => {
    getMock.mockResolvedValueOnce(
      usersResponse({
        queryComplete: false,
        queryStatus: "degraded",
      }),
    );
    const props = renderGrid();
    const params = makeGridParams();

    await readPage(params);

    expect(params.fail).toHaveBeenCalledTimes(1);
    expect(params.success).not.toHaveBeenCalled();
    // The Users grid owns a custom no-rows overlay which renders readError as
    // an alert, so showing it here exposes the failure rather than a false
    // empty state.
    expect(params.api.showNoRowsOverlay).toHaveBeenCalledOnce();
    expect(props.setHasData).not.toHaveBeenCalledWith(false);
    expect(storeState.clearSelection).not.toHaveBeenCalled();
    expect(props.setSearchState).toHaveBeenCalledWith("error");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't load this data. Please retry in a moment.",
    );
  });

  it.each(["success", "failure"])(
    "keeps replacement-filter loading active after stale request %s",
    async (outcome) => {
      let finishStale;
      let finishCurrent;
      getMock
        .mockImplementationOnce(
          () =>
            new Promise((resolve, reject) => {
              finishStale = () =>
                outcome === "success"
                  ? resolve(usersResponse({ rows: [row(1)] }))
                  : reject(new Error("obsolete request failure"));
            }),
        )
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finishCurrent = () => resolve(usersResponse({ rows: [row(99)] }));
            }),
        );
      const props = {
        setHasData: vi.fn(),
        setIsLoading: vi.fn(),
        setSearchState: vi.fn(),
        cellHeight: "Short",
      };
      const view = render(<UsersGrid {...props} />);
      const staleParams = makeGridParams();
      const staleRead =
        gridState.props.serverSideDatasource.getRows(staleParams);
      await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
      const staleSignal = getMock.mock.calls[0][1].signal;

      await act(async () => {
        storeState.setFilters([
          ...validated,
          {
            column_id: "company_id",
            property_id: "custom_attribute:company_id",
            filter_config: {
              filter_type: "text",
              filter_op: "in",
              filter_value: ["001", "002"],
              col_type: "SPAN_ATTRIBUTE",
              attribute_value_types: ["string", "string"],
            },
          },
        ]);
      });
      view.rerender(<UsersGrid {...props} />);
      // Cancellation can settle before AG Grid starts the replacement read.
      // The obsolete generation must not clear loading during that handoff.
      expect(props.setIsLoading).toHaveBeenLastCalledWith(true);
      const currentParams = makeGridParams();
      const currentRead =
        gridState.props.serverSideDatasource.getRows(currentParams);
      await act(async () => {
        finishStale();
        await staleRead;
      });
      const loadingWhileCurrentPending = props.setIsLoading.mock.lastCall[0];
      await act(async () => {
        finishCurrent();
        await currentRead;
      });

      expect(staleSignal.aborted).toBe(true);
      expect(loadingWhileCurrentPending).toBe(true);
      expect(props.setIsLoading).toHaveBeenLastCalledWith(false);
      expect(JSON.parse(getMock.mock.calls[1][1].params.filters)).toEqual(
        storeState.filters,
      );
      expect(staleParams.success).not.toHaveBeenCalled();
      expect(staleParams.fail).not.toHaveBeenCalled();
      expect(props.setSearchState).not.toHaveBeenCalledWith("error");
      expect(currentParams.success).toHaveBeenCalledWith({
        rowData: [row(99)],
        rowCount: 1,
      });
    },
  );

  it("settles cancelled user reads without releasing the replacement loading owner", async () => {
    let resolveOld;
    let resolveCurrent;
    getMock
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveCurrent = resolve;
          }),
      );
    const props = renderGrid();
    const oldParams = makeGridParams();
    const settled = vi.fn();
    let oldRead;
    act(() => {
      oldRead = gridState.props.serverSideDatasource
        .getRows(oldParams)
        .then(settled);
    });
    await waitFor(() => expect(resolveOld).toBeTypeOf("function"));
    const oldSignal = getMock.mock.calls[0][1].signal;
    const currentParams = makeGridParams({ endRow: 50 });
    let currentRead;
    act(() => {
      currentRead = gridState.props.serverSideDatasource.getRows(currentParams);
    });
    await waitFor(() => expect(resolveCurrent).toBeTypeOf("function"));
    try {
      await waitFor(() => expect(settled).toHaveBeenCalledOnce());
      expect(oldSignal.aborted).toBe(true);
      expect(props.setIsLoading).toHaveBeenLastCalledWith(true);
      expect(oldParams.success).not.toHaveBeenCalled();
      expect(oldParams.fail).not.toHaveBeenCalled();
      expect(currentParams.success).not.toHaveBeenCalled();
      expect(props.setHasData).not.toHaveBeenCalledWith(false);
      expect(props.setSearchState).not.toHaveBeenCalledWith("error");
      expect(currentParams.api.showNoRowsOverlay).not.toHaveBeenCalled();
      await act(async () => {
        resolveCurrent(usersResponse({ rows: [row(99)] }));
        await currentRead;
      });
      expect(props.setIsLoading).toHaveBeenLastCalledWith(false);
      expect(currentParams.success).toHaveBeenCalledWith({
        rowData: [row(99)],
        rowCount: 1,
      });
    } finally {
      await act(async () => {
        resolveOld(usersResponse({ rows: [row(1)] }));
        resolveCurrent(usersResponse({ rows: [row(99)] }));
        await Promise.all([oldRead, currentRead]);
      });
    }
    expect(oldParams.success).not.toHaveBeenCalled();
  });

  it("invalidates an in-flight page when a changed query starts a new generation", async () => {
    let resolveStale;
    const staleResponse = new Promise((resolve) => {
      resolveStale = resolve;
    });
    getMock
      .mockReturnValueOnce(staleResponse)
      .mockResolvedValueOnce(usersResponse({ rows: [row(99)] }));
    renderGrid();

    const staleParams = makeGridParams();
    const staleRead = gridState.props.serverSideDatasource.getRows(staleParams);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));

    // A different page size changes the request identity even though every
    // client-side sort is intentionally cleared by the bounded contract.
    const currentParams = makeGridParams({ endRow: 50 });
    await readPage(currentParams);
    resolveStale(usersResponse({ rows: [row(1)] }));
    await act(async () => staleRead);

    expect(currentParams.success).toHaveBeenCalledTimes(1);
    expect(staleParams.fail).not.toHaveBeenCalled();
    expect(staleParams.success).not.toHaveBeenCalled();
  });

  it("drops a completed request after the grid is destroyed", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );
    renderGrid();

    const params = makeGridParams();
    let destroyed = false;
    params.api.isDestroyed = () => destroyed;
    const read = gridState.props.serverSideDatasource.getRows(params);
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    destroyed = true;
    resolveResponse(usersResponse({ rows: [row(1)] }));
    await act(async () => read);

    expect(params.success).not.toHaveBeenCalled();
    expect(params.fail).not.toHaveBeenCalled();
    expect(params.api.showNoRowsOverlay).not.toHaveBeenCalled();
  });

  it("does not let a destroyed-grid callback release another read's loading", async () => {
    let finishCurrent;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishCurrent = resolve;
        }),
    );
    const props = renderGrid();
    const currentParams = makeGridParams();
    const currentRead =
      gridState.props.serverSideDatasource.getRows(currentParams);
    await waitFor(() => expect(finishCurrent).toBeTypeOf("function"));
    const destroyedParams = makeGridParams();
    destroyedParams.api.isDestroyed = () => true;
    await readPage(destroyedParams);
    const loadingWhileCurrentPending = props.setIsLoading.mock.lastCall[0];
    await act(async () => {
      finishCurrent(usersResponse({ rows: [row(99)] }));
      await currentRead;
    });

    expect(loadingWhileCurrentPending).toBe(true);
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(destroyedParams.success).not.toHaveBeenCalled();
    expect(destroyedParams.fail).not.toHaveBeenCalled();
    expect(currentParams.success).toHaveBeenCalledOnce();
    expect(props.setIsLoading).toHaveBeenLastCalledWith(false);
  });
});
