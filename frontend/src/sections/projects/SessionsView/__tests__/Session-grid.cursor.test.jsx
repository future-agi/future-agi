import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

const { enqueueSnackbarMock, getMock, gridState, sessionStoreState } =
  vi.hoisted(() => ({
    enqueueSnackbarMock: vi.fn(),
    getMock: vi.fn(),
    gridState: { props: null, api: null },
    sessionStoreState: {
      toggledNodes: [],
      selectAll: false,
      totalRowCount: null,
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
      return <div data-testid="session-grid" />;
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/utils/utils", () => ({ getRandomId: () => "column" }));
vi.mock("src/sections/develop-detail/Common/TotalRowsStatusBar", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: { projectSessionList: () => "/sessions/list/" },
  },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => enqueueSnackbarMock(...args),
}));
vi.mock("../../TracesDrawer/TracesDrawer", () => ({ default: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("../common", () => ({
  getSessionListColumnDef: (column) => ({ field: column.id }),
  initialVisibility: { session_id: true },
  mergeNonCustomColumns: (_current, incoming) => incoming,
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { observeSessionidClicked: "session" },
  trackEvent: vi.fn(),
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => ["day", vi.fn()],
}));
vi.mock("../../UsersView/common", () => ({
  userTraceRowHeightMapping: { Short: { height: 40 } },
}));
vi.mock("src/sections/projects/LLMTracing/common", () => ({
  normalizeConfigKeys: (config) => config || [],
  toBackendFilters: (filters) => filters,
}));
vi.mock("../ReplaySessions/store", () => {
  const useSessionsGridStore = { setState: vi.fn() };
  return {
    useSessionsGridStore,
    useSessionsGridStoreShallow: (selector) => selector(sessionStoreState),
  };
});

import SessionGrid from "../Session-grid";
import { OBSERVE_LIST_REFRESH_EVENT } from "../../observeEvents";

const sessionResponse = ({
  rows = [],
  hasMore,
  nextCursor,
  totalRows = rows.length,
  lowerBound = false,
  queryComplete,
  queryStatus,
} = {}) => {
  const metadata = {
    total_rows: totalRows,
    total_rows_is_lower_bound: lowerBound,
  };
  if (hasMore !== undefined) metadata.has_more = hasMore;
  if (nextCursor !== undefined) metadata.next_cursor = nextCursor;
  if (queryComplete !== undefined) metadata.query_complete = queryComplete;
  if (queryStatus !== undefined) metadata.query_status = queryStatus;
  return {
    data: {
      result: {
        config: [],
        table: rows,
        metadata,
      },
    },
  };
};

const row = (number) => ({ session_id: `session-${number}` });

const renderGrid = () =>
  render(
    <SessionGrid
      ref={React.createRef()}
      updateObj={{ session_id: true }}
      columns={[{ id: "session_id", isVisible: true }]}
      setColumns={vi.fn()}
      filters={[{ column_id: "created_at" }]}
      projectId="project-1"
      cellHeight="Short"
      onSelectionChanged={vi.fn()}
      className=""
      onGridReady={vi.fn()}
    />,
  );

const makeParams = ({ startRow = 0, sortModel = [] } = {}) => {
  let currentPage = Math.floor(startRow / 25);
  let renderedNodes = [];
  let paintedRows = true;
  let paintedSignature = `page-${currentPage + 1}`;
  const api = {
    getGui: vi.fn(() => ({
      querySelectorAll: vi.fn(() =>
        paintedRows
          ? [
              {
                getAttribute: vi.fn(() => "0"),
                textContent: paintedSignature,
              },
            ]
          : [],
      ),
    })),
    getRenderedNodes: vi.fn(() => renderedNodes),
    paginationGetCurrentPage: vi.fn(() => currentPage),
    paginationGoToFirstPage: vi.fn(),
    paginationGoToPage: vi.fn((nextPage) => {
      currentPage = nextPage;
    }),
    showNoRowsOverlay: vi.fn(),
    refreshServerSide: vi.fn(),
    retryServerSideLoads: vi.fn(),
    setPaintedRows: (nextPaintedRows) => {
      paintedRows = nextPaintedRows;
      if (nextPaintedRows) paintedSignature = `page-${currentPage + 1}`;
    },
    setPaintedText: (text) => {
      paintedRows = true;
      paintedSignature = text;
    },
  };
  return {
    request: { startRow, endRow: startRow + 25, sortModel },
    api,
    success: vi.fn(({ rowData = [] }) => {
      renderedNodes = rowData.map((data) => ({
        data,
        id: data.session_id,
      }));
      paintedSignature = `page-${currentPage + 1}`;
    }),
    fail: vi.fn(),
  };
};

const getRows = async (params) => {
  gridState.api = params.api;
  await act(async () => {
    await gridState.props.serverSideDatasource.getRows(params);
  });
};

describe("SessionGrid cursor continuation", () => {
  beforeEach(() => {
    getMock.mockReset();
    enqueueSnackbarMock.mockReset();
    gridState.props = null;
    gridState.api = null;
  });

  it("keeps cache purging enabled with a fixed row height", async () => {
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    expect(gridState.props.getRowHeight).toBeUndefined();
    expect(gridState.props.rowHeight).toBe(40);
    expect(gridState.props.pagination).toBe(true);
    expect(gridState.props.paginationPageSize).toBe(25);
    expect(gridState.props.paginationPageSizeSelector).toBe(false);
    expect(gridState.props.suppressPaginationPanel).toBe(true);
    expect(gridState.props.cacheBlockSize).toBe(25);
    expect(gridState.props.maxBlocksInCache).toBe(5);
    expect(gridState.props.maxConcurrentDatasourceRequests).toBe(1);
    expect(screen.getByLabelText("Results per page")).toHaveTextContent("25");
  });

  it("refreshes visible session rows without purging them", async () => {
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());
    const params = makeParams();
    gridState.api = params.api;

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));

    expect(params.api.refreshServerSide).toHaveBeenCalledWith({ purge: false });
  });

  it("does not stack session auto refreshes while a read is pending", async () => {
    let resolveResponse;
    getMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());
    const params = makeParams();
    gridState.api = params.api;
    let pendingRead;
    act(() => {
      pendingRead = gridState.props.serverSideDatasource.getRows(params);
    });
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(params.api.refreshServerSide).not.toHaveBeenCalled();

    await act(async () => {
      resolveResponse(sessionResponse());
      await pendingRead;
    });
  });

  it("loads a numbered next page only after explicit navigation when cursor metadata is absent", async () => {
    getMock
      .mockResolvedValueOnce(
        sessionResponse({
          rows: Array.from({ length: 25 }, (_, index) => row(index)),
          totalRows: 50,
        }),
      )
      .mockResolvedValueOnce(sessionResponse({ rows: [row(25)] }));
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams({
      sortModel: [{ colId: "started_at", sort: "desc" }],
    });
    await getRows(firstPage);

    expect(getMock.mock.calls[0][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        page_number: 0,
        sort_params: JSON.stringify([
          { column_id: "started_at", direction: "desc" },
        ]),
      }),
    );
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(firstPage.success).toHaveBeenCalledWith({
      rowData: Array.from({ length: 25 }, (_, index) => row(index)),
      rowCount: 26,
    });

    const secondPage = makeParams({
      startRow: 25,
      sortModel: [{ colId: "started_at", sort: "desc" }],
    });
    await getRows(secondPage);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({ page_number: 1 }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor_mode");
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor");
    expect(secondPage.success).toHaveBeenCalledWith({
      rowData: [row(25)],
      rowCount: 26,
    });
  });

  it("shows a loading state while an explicitly requested session page is pending", async () => {
    let resolveSecondPage;
    getMock
      .mockResolvedValueOnce(
        sessionResponse({
          rows: Array.from({ length: 25 }, (_, index) => row(index)),
          hasMore: true,
          nextCursor: "page-2",
          totalRows: 25,
          lowerBound: true,
        }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecondPage = resolve;
          }),
      );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams();
    await getRows(firstPage);
    await userEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    firstPage.api.refreshServerSide.mockClear();
    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(firstPage.api.refreshServerSide).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(screen.getByRole("button", { name: "page 2" })).toBeDisabled();

    const secondPage = makeParams({ startRow: 25 });
    gridState.api = secondPage.api;
    let pendingRead;
    await act(async () => {
      pendingRead = gridState.props.serverSideDatasource.getRows(secondPage);
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
      expect(gridState.props.loading).toBe(false);
    });
    expect(screen.getByRole("button", { name: "page 2" })).toBeDisabled();

    secondPage.success.mockImplementation(() => {});
    secondPage.api.setPaintedRows(false);
    resolveSecondPage(
      sessionResponse({
        rows: [row(25)],
        hasMore: false,
        nextCursor: null,
        totalRows: 26,
      }),
    );
    await act(async () => pendingRead);

    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(gridState.props.loading).toBe(false);

    secondPage.api.getRenderedNodes.mockReturnValue([
      { id: "session-25", data: row(25) },
    ]);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");

    secondPage.api.setPaintedText("   ");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");

    secondPage.api.setPaintedRows(true);
    await waitFor(() => {
      expect(screen.queryByText("Loading page…")).not.toBeInTheDocument();
      expect(gridState.props.loading).toBe(false);
    });
  });

  it("falls back to numbered pagination when an explicit cursor page is rejected by an older API", async () => {
    const legacyCursorError = {
      response: {
        status: 400,
        data: {
          attr: "cursor_mode",
          detail: "cursor_mode: Unknown field.",
          details: { cursor_mode: ["Unknown field."] },
        },
      },
    };
    getMock
      .mockResolvedValueOnce(
        sessionResponse({
          rows: Array.from({ length: 25 }, (_, index) => row(index)),
          hasMore: true,
          nextCursor: "signed-after-25",
          totalRows: 25,
          lowerBound: true,
        }),
      )
      .mockRejectedValueOnce(legacyCursorError)
      .mockResolvedValueOnce(
        sessionResponse({ rows: [row(25)], totalRows: 26 }),
      );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams();
    await getRows(firstPage);
    expect(getMock).toHaveBeenCalledTimes(1);

    const secondPage = makeParams({ startRow: 25 });
    await getRows(secondPage);

    // A mixed-version cursor error invalidates the current generation before
    // asking AG Grid for a clean numbered-page replay.  Do not retry inside
    // the stale cursor request: the real grid invokes getRows again after the
    // purge, so mirror that lifecycle explicitly here.
    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "signed-after-25",
      }),
    );
    expect(secondPage.fail).toHaveBeenCalledTimes(1);
    expect(secondPage.api.refreshServerSide).toHaveBeenCalledWith({
      purge: true,
    });

    const numberedSecondPage = makeParams({ startRow: 25 });
    await getRows(numberedSecondPage);

    expect(getMock).toHaveBeenCalledTimes(3);
    expect(getMock.mock.calls[2][1].params).toEqual(
      expect.objectContaining({ page_number: 1 }),
    );
    expect(getMock.mock.calls[2][1].params).not.toHaveProperty("cursor_mode");
    expect(getMock.mock.calls[2][1].params).not.toHaveProperty("cursor");
    expect(numberedSecondPage.success).toHaveBeenCalledWith({
      rowData: [row(25)],
      rowCount: 26,
    });
    expect(numberedSecondPage.fail).not.toHaveBeenCalled();
  });

  it("follows an empty checkpoint and publishes only the first genuine match", async () => {
    getMock
      .mockResolvedValueOnce(
        sessionResponse({
          hasMore: true,
          nextCursor: "checkpoint-1",
          lowerBound: true,
        }),
      )
      .mockResolvedValueOnce(
        sessionResponse({
          rows: [row(8)],
          hasMore: false,
          nextCursor: null,
          totalRows: 1,
        }),
      );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-1",
      }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("page_number");
    expect(params.success).toHaveBeenCalledWith({
      rowData: [row(8)],
      rowCount: 1,
    });
  });

  it("treats cursor exhaustion as exact even when an older response leaves a lower-bound flag", async () => {
    getMock.mockResolvedValueOnce(
      sessionResponse({
        rows: [row(8)],
        hasMore: false,
        nextCursor: null,
        totalRows: 99,
        lowerBound: true,
      }),
    );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(params.success).toHaveBeenCalledWith({
      rowData: [row(8)],
      rowCount: 1,
    });
    expect(params.api.totalRowCount).toBe(1);
    expect(params.api.totalRowCountLowerBound).toBeNull();
    expect(params.api.totalRowCountIsLowerBound).toBe(false);
  });

  it("fills a short nonterminal page and carries overflow into page N", async () => {
    getMock
      .mockResolvedValueOnce(
        sessionResponse({
          rows: [row(1)],
          hasMore: true,
          nextCursor: "after-1",
          lowerBound: true,
        }),
      )
      .mockResolvedValueOnce(
        sessionResponse({
          rows: Array.from({ length: 25 }, (_, index) => row(index + 2)),
          hasMore: true,
          nextCursor: "after-26",
          lowerBound: true,
        }),
      )
      .mockResolvedValueOnce(
        sessionResponse({
          rows: [row(27), row(28)],
          hasMore: false,
          nextCursor: null,
          totalRows: 28,
        }),
      );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams();
    await getRows(firstPage);
    expect(firstPage.success).toHaveBeenCalledWith({
      rowData: Array.from({ length: 25 }, (_, index) => row(index + 1)),
      rowCount: 26,
    });

    const secondPage = makeParams({ startRow: 25 });
    await getRows(secondPage);
    expect(secondPage.success).toHaveBeenCalledWith({
      rowData: [row(26), row(27), row(28)],
      rowCount: 28,
    });
    expect(getMock.mock.calls[2][1].params).toEqual(
      expect.objectContaining({ cursor: "after-26", cursor_mode: true }),
    );
  });

  it("stops automatic retries at the bound and preserves the manual retry cursor", async () => {
    Array.from({ length: 13 }, (_, index) =>
      sessionResponse({
        hasMore: true,
        nextCursor: `checkpoint-${index}`,
        lowerBound: true,
      }),
    ).forEach((response) => getMock.mockResolvedValueOnce(response));
    getMock.mockResolvedValueOnce(
      sessionResponse({
        rows: [row(99)],
        hasMore: false,
        nextCursor: null,
        totalRows: 1,
      }),
    );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const boundedRound = makeParams();
    await getRows(boundedRound);

    expect(getMock).toHaveBeenCalledTimes(13);
    expect(boundedRound.success).not.toHaveBeenCalled();
    expect(boundedRound.api.showNoRowsOverlay).not.toHaveBeenCalled();
    expect(boundedRound.fail).toHaveBeenCalledTimes(1);
    expect(boundedRound.api.retryServerSideLoads).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Preparing exact results. Refresh or retry to continue.",
    );
    expect(gridState.props.className).toContain("ag-grid-cursor-paused");
    expect(gridState.props.noRowsOverlayComponent()).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );
    expect(boundedRound.api.retryServerSideLoads).toHaveBeenCalledOnce();
    expect(boundedRound.api.refreshServerSide).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
    expect(gridState.props.className).not.toContain("ag-grid-cursor-paused");

    // A deliberate retry resumes the retained exact checkpoint. The bounded
    // automatic read itself never spins or publishes a false empty page.
    const resumedPage = makeParams();
    await getRows(resumedPage);

    expect(getMock.mock.calls[13][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-12",
      }),
    );
    expect(resumedPage.success).toHaveBeenCalledWith({
      rowData: [row(99)],
      rowCount: 1,
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("fails instead of looping or displaying a false empty page on a repeated token", async () => {
    getMock
      .mockResolvedValueOnce(
        sessionResponse({ hasMore: true, nextCursor: "same-token" }),
      )
      .mockResolvedValueOnce(
        sessionResponse({ hasMore: true, nextCursor: "same-token" }),
      );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(params.fail).toHaveBeenCalledTimes(1);
    expect(params.success).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).toHaveBeenCalledWith(
      "Session data could not be loaded. Please retry.",
      { variant: "error" },
    );
  });

  it("sanitizes API errors and does not convert them into successful empty data", async () => {
    getMock.mockRejectedValue({
      response: {
        status: 500,
        data: { detail: "DB::Exception Code 159 private stack" },
      },
    });
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(params.fail).toHaveBeenCalledTimes(1);
    expect(params.success).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).toHaveBeenCalledWith(
      "Session data could not be loaded. Please retry.",
      { variant: "error" },
    );
    expect(enqueueSnackbarMock).not.toHaveBeenCalledWith(
      expect.stringMatching(/DB::Exception/i),
      expect.anything(),
    );
  });

  it("does not show an error toast for a superseded scroll request", async () => {
    getMock.mockRejectedValueOnce(
      Object.assign(new Error("canceled"), { code: "ERR_CANCELED" }),
    );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(params.fail).not.toHaveBeenCalled();
    expect(params.success).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).not.toHaveBeenCalled();
  });

  it("fails a degraded HTTP 200 instead of displaying a false empty session grid", async () => {
    getMock.mockResolvedValueOnce(
      sessionResponse({
        hasMore: false,
        nextCursor: null,
        queryComplete: false,
        queryStatus: "degraded",
      }),
    );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    await getRows(params);

    expect(params.fail).toHaveBeenCalledTimes(1);
    expect(params.success).not.toHaveBeenCalled();
    expect(params.api.showNoRowsOverlay).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).toHaveBeenCalledWith(
      "Session data could not be loaded. Please retry.",
      { variant: "error" },
    );
  });

  it("silently discards an in-flight response from an older sort generation", async () => {
    let resolveStale;
    const staleResponse = new Promise((resolve) => {
      resolveStale = resolve;
    });
    getMock
      .mockReturnValueOnce(staleResponse)
      .mockResolvedValueOnce(sessionResponse({ rows: [row(9)] }));
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const staleParams = makeParams();
    const staleRead = gridState.props.serverSideDatasource.getRows(staleParams);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));

    const currentParams = makeParams({
      sortModel: [{ colId: "started_at", sort: "desc" }],
    });
    await getRows(currentParams);
    resolveStale(sessionResponse({ rows: [row(1)] }));
    await act(async () => staleRead);

    expect(currentParams.success).toHaveBeenCalledTimes(1);
    expect(staleParams.fail).not.toHaveBeenCalled();
    expect(staleParams.success).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).not.toHaveBeenCalled();
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
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const params = makeParams();
    let destroyed = false;
    params.api.isDestroyed = () => destroyed;
    const read = gridState.props.serverSideDatasource.getRows(params);
    await waitFor(() => expect(resolveResponse).toBeTypeOf("function"));

    destroyed = true;
    resolveResponse(sessionResponse({ rows: [row(1)] }));
    await act(async () => read);

    expect(params.success).not.toHaveBeenCalled();
    expect(params.fail).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).not.toHaveBeenCalled();
  });
});
