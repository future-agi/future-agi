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

const sessionResponse = ({
  rows = [],
  hasMore,
  nextCursor,
  totalRows = rows.length,
  lowerBound = false,
} = {}) => {
  const metadata = {
    total_rows: totalRows,
    total_rows_is_lower_bound: lowerBound,
  };
  if (hasMore !== undefined) metadata.has_more = hasMore;
  if (nextCursor !== undefined) metadata.next_cursor = nextCursor;
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

const makeParams = ({ startRow = 0, sortModel = [] } = {}) => ({
  request: { startRow, endRow: startRow + 25, sortModel },
  api: {
    showNoRowsOverlay: vi.fn(),
    refreshServerSide: vi.fn(),
    retryServerSideLoads: vi.fn(),
  },
  success: vi.fn(),
  fail: vi.fn(),
});

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

  it("falls back to numbered prefetch when a sorted response omits cursor metadata", async () => {
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

    const params = makeParams({
      sortModel: [{ colId: "started_at", sort: "desc" }],
    });
    await getRows(params);

    expect(getMock.mock.calls[0][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        page_number: 0,
        sort_params: JSON.stringify([
          { column_id: "started_at", direction: "desc" },
        ]),
      }),
    );
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({ page_number: 1 }),
    );
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor_mode");
    expect(getMock.mock.calls[1][1].params).not.toHaveProperty("cursor");
    expect(params.success).toHaveBeenCalledTimes(1);
  });

  it("consumes a rejected cursor prefetch before retrying the page as numbered", async () => {
    let rejectPrefetch;
    const prefetchedCursorPage = new Promise((_resolve, reject) => {
      rejectPrefetch = reject;
    });
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
      .mockReturnValueOnce(prefetchedCursorPage)
      .mockResolvedValueOnce(
        sessionResponse({ rows: [row(25)], totalRows: 26 }),
      );
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    const firstPage = makeParams();
    await getRows(firstPage);
    expect(getMock).toHaveBeenCalledTimes(2);

    const secondPage = makeParams({ startRow: 25 });
    const secondPageRead =
      gridState.props.serverSideDatasource.getRows(secondPage);
    rejectPrefetch(legacyCursorError);
    await act(async () => secondPageRead);

    expect(getMock).toHaveBeenCalledTimes(3);
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "signed-after-25",
      }),
    );
    expect(getMock.mock.calls[2][1].params).toEqual(
      expect.objectContaining({ page_number: 1 }),
    );
    expect(getMock.mock.calls[2][1].params).not.toHaveProperty("cursor_mode");
    expect(getMock.mock.calls[2][1].params).not.toHaveProperty("cursor");
    expect(secondPage.success).toHaveBeenCalledWith({
      rowData: [row(25)],
      rowCount: 26,
    });
    expect(secondPage.fail).not.toHaveBeenCalled();
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
      rowCount: -1,
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
    expect(staleParams.fail).toHaveBeenCalledTimes(1);
    expect(staleParams.success).not.toHaveBeenCalled();
    expect(enqueueSnackbarMock).not.toHaveBeenCalled();
  });
});
