import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

const { enqueueSnackbarMock, getMock, gridState, sessionStoreState } =
  vi.hoisted(() => ({
    enqueueSnackbarMock: vi.fn(),
    getMock: vi.fn(),
    gridState: { props: null, api: null, drawer: null },
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
  readQuery: (...args) => getMock(...args),
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: { projectSessionList: () => "/sessions/list/" },
  },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => enqueueSnackbarMock(...args),
}));
vi.mock("../../TracesDrawer/TracesDrawer", () => ({ default: (props) => {
  gridState.drawer = props;
  return null;
} }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
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
import * as listCursorPagination from "../../LLMTracing/listCursorPagination";
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

const renderGrid = (props = {}) =>
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
      {...props}
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

const deferredCompletion = () => {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
};

const completionGrid = async (datasource) => {
  const { createGrid, ModuleRegistry } = await import("ag-grid-community");
  const { AllEnterpriseModule } = await import("ag-grid-enterprise");
  ModuleRegistry.registerModules([AllEnterpriseModule]);
  const reads = [];
  const track = (source) => ({
    getRows(params) {
      const read = {
        ...params,
        success: vi.fn(params.success),
        fail: vi.fn(params.fail),
      };
      reads.push(read);
      read.settled = source.getRows(read);
    },
  });
  const host = document.createElement("div");
  document.body.appendChild(host);
  let api;
  act(() => {
    api = createGrid(host, {
      theme: "legacy",
      domLayout: "autoHeight",
      columnDefs: [{ field: "session_id" }],
      rowModelType: "serverSide",
      cacheBlockSize: 25,
      serverSideInitialRowCount: 5,
      maxConcurrentDatasourceRequests: 1,
      rowSelection: { mode: "multiRow" },
      suppressServerSideFullWidthLoadingRow: true,
      serverSideDatasource: track(datasource),
    });
    gridState.api = api;
  });
  return {
    api,
    reads,
    replace: (source) => act(() => api.setGridOption("serverSideDatasource", track(source))),
    close: () => {
      act(() => api.destroy());
      host.remove();
    },
  };
};

describe("SessionGrid completion regression", () => {
  beforeEach(() => {
    getMock.mockReset();
    enqueueSnackbarMock.mockReset();
    gridState.props = null;
    gridState.api = null;
  });

  const userFilter = {
    column_id: "user_id",
    filter_config: {
      filter_type: "text", filter_op: "equals", filter_value: "shared@example.test",
    },
  };
  const today = [userFilter, {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime", filter_op: "between",
      filter_value: ["2026-09-09T00:00:00Z", "2026-09-09T23:59:59Z"],
    },
  }];
  const pastSevenDays = [userFilter, {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime", filter_op: "between",
      filter_value: ["2026-09-03T00:00:00Z", "2026-09-09T23:59:59Z"],
    },
  }];
  const subject = (props, filters) => (
    <SessionGrid
      updateObj={{ session_id: true }}
      columns={[{ id: "session_id", isVisible: true }]}
      projectId={null}
      cellHeight="Short"
      {...props}
      filters={filters}
    />
  );

  it.each(["success", "failure"])(
    "releases the real concurrency-one queue before cancelled transport late %s",
    async (outcome) => {
      const old = deferredCompletion();
      const current = deferredCompletion();
      getMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
      const props = { ref: React.createRef(), setColumns: vi.fn() };
      const view = render(subject(props, today));
      const grid = await completionGrid(gridState.props.serverSideDatasource);
      try {
        await waitFor(() => expect(getMock).toHaveBeenCalledOnce());
        const oldSignal = getMock.mock.calls[0][1].signal;
        view.rerender(subject(props, pastSevenDays));
        // Let the real AG Grid loader dispatch the new range, not the test.
        grid.replace(gridState.props.serverSideDatasource);
        expect(oldSignal.aborted).toBe(true);
        await act(async () => { await grid.reads[0].settled; });
        await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
        expect(grid.reads[0].fail).toHaveBeenCalledOnce();
        expect(grid.reads[0].success).not.toHaveBeenCalled();
        expect(JSON.parse(getMock.mock.calls[1][1].params.filters)).toEqual(pastSevenDays);
        expect(getMock.mock.calls[1][1].params).not.toHaveProperty("project_id");
        await act(async () => {
          if (outcome === "success") old.resolve(sessionResponse());
          else old.reject(new Error("obsolete transport failure"));
          await grid.reads[0].settled;
        });
        expect(enqueueSnackbarMock).not.toHaveBeenCalled();
        expect(grid.api.getDisplayedRowAtIndex(0)?.data).toBeUndefined();
        expect(grid.reads[1].success).not.toHaveBeenCalled();
        expect(grid.reads[1].fail).not.toHaveBeenCalled();
        await act(async () => {
          current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
          await grid.reads[1].settled;
        });
        expect(grid.api.getDisplayedRowAtIndex(0)?.data).toEqual(row(99));
        expect(grid.reads[0].fail).toHaveBeenCalledOnce();
        expect(grid.reads[0].success).not.toHaveBeenCalled();
        expect(grid.reads[1].success).toHaveBeenCalledOnce();
        expect(grid.reads[1].fail).not.toHaveBeenCalled();
      } finally {
        grid.close();
        await act(async () => {
          old.resolve(sessionResponse());
          current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
          await Promise.all(grid.reads.map((read) => read.settled));
        });
      }
    },
  );

  it.each(["success", "failure"])(
    "completes stale page %s without changing replacement state",
    async (outcome) => {
      const old = deferredCompletion();
      const current = deferredCompletion();
      const loadPage = vi.spyOn(listCursorPagination, "loadExactListPage")
        .mockReturnValueOnce(old.promise);
      getMock.mockReturnValueOnce(current.promise);
      const props = { ref: React.createRef(), setColumns: vi.fn() };
      const view = render(subject(props, today));
      const grid = await completionGrid(gridState.props.serverSideDatasource);
      try {
        await waitFor(() => expect(loadPage).toHaveBeenCalledOnce());
        view.rerender(subject(props, pastSevenDays));
        grid.replace(gridState.props.serverSideDatasource);
        props.setColumns.mockClear();
        await act(async () => {
          if (outcome === "success") old.resolve({ rows: [], response: { data: {} }, isLastPage: true });
          else old.reject(new Error("obsolete page failure"));
          await grid.reads[0].settled;
        });
        expect(enqueueSnackbarMock).not.toHaveBeenCalled();
        expect(props.setColumns).not.toHaveBeenCalled();
        expect(grid.reads[0].success).not.toHaveBeenCalled();
        expect(grid.reads[0].fail).toHaveBeenCalledOnce();
        await waitFor(() => expect(grid.reads).toHaveLength(2));
        await act(async () => {
          current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
          await grid.reads[1].settled;
        });
        expect(grid.api.getDisplayedRowAtIndex(0)?.data).toEqual(row(99));
        expect(grid.reads[1].success).toHaveBeenCalledOnce();
        expect(grid.reads[1].fail).not.toHaveBeenCalled();
      } finally {
        grid.close();
        await act(async () => {
          old.resolve({ rows: [], response: { data: {} }, isLastPage: true });
          current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
          await Promise.all(grid.reads.map((read) => read.settled));
        });
        loadPage.mockRestore();
      }
    },
  );

  it("discards a queued manual reload when filters advance the generation", async () => {
    const old = deferredCompletion();
    const loadPage = vi.spyOn(listCursorPagination, "loadExactListPage")
      .mockReturnValueOnce(old.promise);
    getMock.mockResolvedValue(sessionResponse({ rows: [row(99)], hasMore: false, nextCursor: null }));
    const props = { ref: React.createRef(), setColumns: vi.fn() };
    const view = render(subject(props, today));
    const grid = await completionGrid(gridState.props.serverSideDatasource);
    const refresh = vi.spyOn(grid.api, "refreshServerSide");
    try {
      await waitFor(() => expect(loadPage).toHaveBeenCalledOnce());
      act(() => window.dispatchEvent(new Event("observe-refresh")));
      view.rerender(subject(props, pastSevenDays));
      grid.replace(gridState.props.serverSideDatasource);
      await act(async () => {
        old.resolve({ rows: [], response: { data: {} }, isLastPage: true });
        await grid.reads[0].settled;
      });
      await waitFor(() => expect(grid.api.getDisplayedRowAtIndex(0)?.data).toEqual(row(99)));
      expect(refresh).not.toHaveBeenCalled();
      expect(getMock).toHaveBeenCalledOnce();
      expect(JSON.parse(getMock.mock.calls[0][1].params.filters)).toEqual(pastSevenDays);
      expect(grid.reads[0].fail).toHaveBeenCalledOnce();
      expect(grid.reads[0].success).not.toHaveBeenCalled();
      expect(enqueueSnackbarMock).not.toHaveBeenCalled();
    } finally {
      grid.close();
      await act(async () => {
        old.resolve({ rows: [], response: { data: {} }, isLastPage: true });
        await Promise.all(grid.reads.map((read) => read.settled));
      });
      refresh.mockRestore();
      loadPage.mockRestore();
    }
  });

  it("does not let an already-dead API release another read's refresh guard", async () => {
    const current = deferredCompletion();
    getMock.mockReturnValueOnce(current.promise);
    renderGrid();
    const params = makeParams();
    gridState.api = params.api;
    let read;
    act(() => { read = gridState.props.serverSideDatasource.getRows(params); });
    try {
      await waitFor(() => expect(getMock).toHaveBeenCalledOnce());
      const dead = makeParams();
      dead.api.isDestroyed = () => true;
      await getRows(dead);
      gridState.api = params.api;
      act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
      expect.soft(params.api.refreshServerSide).not.toHaveBeenCalled();
      expect.soft(dead.fail).toHaveBeenCalledOnce();
      expect(dead.success).not.toHaveBeenCalled();
      expect(getMock).toHaveBeenCalledOnce();
    } finally {
      await act(async () => {
        current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
        await read;
      });
    }
  });
});

describe("SessionGrid refresh cache regression", () => {
  beforeEach(() => {
    getMock.mockReset();
    enqueueSnackbarMock.mockReset();
    gridState.props = null;
    gridState.api = null;
  });

  it("does not refresh a remounted grid through a reused forwarded ref after cancellation", async () => {
    const old = deferredCompletion();
    const current = deferredCompletion();
    getMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const ref = React.createRef();
    const oldView = renderGrid({ ref });
    const oldParams = makeParams();
    let oldDestroyed = false;
    oldParams.api.isDestroyed = () => oldDestroyed;
    gridState.api = oldParams.api;
    let oldRead;
    let currentRead;
    let currentView;
    act(() => { oldRead = gridState.props.serverSideDatasource.getRows(oldParams); });
    try {
      await waitFor(() => expect(getMock).toHaveBeenCalledOnce());
      const oldSignal = getMock.mock.calls[0][1].signal;
      // Keep the real cancellable page wrapper: the obsolete transport itself
      // stays unresolved while React unmounts/remounts with the same parent ref.
      act(() => window.dispatchEvent(new Event("observe-refresh")));
      expect(oldSignal.aborted).toBe(true);
      oldView.unmount();
      oldDestroyed = true;
      expect(ref.current).toBeNull();
      const currentParams = makeParams();
      gridState.api = currentParams.api;
      currentView = renderGrid({ ref, projectId: "project-2" });
      expect(ref.current.api).toBe(currentParams.api);
      act(() => { currentRead = gridState.props.serverSideDatasource.getRows(currentParams); });

      await act(async () => { await oldRead; });
      await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
      expect(currentParams.api.refreshServerSide).not.toHaveBeenCalled();
      expect(currentParams.api.paginationGoToFirstPage).not.toHaveBeenCalled();
      expect(oldParams.fail).toHaveBeenCalledOnce();
      expect(oldParams.success).not.toHaveBeenCalled();
      expect(currentParams.success).not.toHaveBeenCalled();
      expect(currentParams.fail).not.toHaveBeenCalled();
      expect(getMock.mock.calls[1][1].params.project_id).toBe("project-2");
      expect(getMock.mock.calls[1][1].signal.aborted).toBe(false);
      expect(enqueueSnackbarMock).not.toHaveBeenCalled();

      await act(async () => {
        old.resolve(sessionResponse({ rows: [row(1)], hasMore: false, nextCursor: null }));
        current.resolve(sessionResponse({ rows: [row(99)], hasMore: false, nextCursor: null }));
        await currentRead;
      });
      expect(currentParams.success).toHaveBeenCalledExactlyOnceWith({ rowData: [row(99)], rowCount: 1 });
      expect(oldParams.fail).toHaveBeenCalledOnce();
      expect(oldParams.success).not.toHaveBeenCalled();
      expect(currentParams.api.refreshServerSide).not.toHaveBeenCalled();
    } finally {
      oldView.unmount();
      currentView?.unmount();
      await act(async () => {
        old.resolve(sessionResponse());
        current.resolve(sessionResponse({ rows: [row(99)], hasMore: false, nextCursor: null }));
        await Promise.all([oldRead, currentRead]);
      });
    }
  });

  it.each(["observe-refresh", OBSERVE_LIST_REFRESH_EVENT])(
    "%s rereads the same query and keeps real grid rows until an exact empty replacement",
    async (eventName) => {
      const replacement = deferredCompletion();
      getMock
        .mockResolvedValueOnce(sessionResponse({ rows: [row(1)], hasMore: false, nextCursor: null }))
        .mockReturnValueOnce(replacement.promise);
      renderGrid();
      const datasource = gridState.props.serverSideDatasource;
      const grid = await completionGrid(datasource);
      const refresh = vi.spyOn(grid.api, "refreshServerSide");
      try {
        await waitFor(() => expect(grid.api.getDisplayedRowAtIndex(0)?.data).toEqual(row(1)));
        expect(getMock).toHaveBeenCalledOnce();

        act(() => window.dispatchEvent(new Event(eventName)));
        await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
        expect(refresh).toHaveBeenCalledExactlyOnceWith({ purge: false });
        expect(gridState.props.serverSideDatasource).toBe(datasource);
        expect(gridState.props.loading).toBe(false);
        expect(grid.api.getDisplayedRowAtIndex(0)?.data).toEqual(row(1));
        expect(grid.reads[1].success).not.toHaveBeenCalled();
        expect(getMock.mock.calls[1][1].params).toEqual(getMock.mock.calls[0][1].params);

        await act(async () => {
          replacement.resolve(sessionResponse({ rows: [], hasMore: false, nextCursor: null }));
          await grid.reads[1].settled;
        });
        expect(grid.reads[1].success).toHaveBeenCalledExactlyOnceWith({ rowData: [], rowCount: 0 });
        expect(grid.reads[1].fail).not.toHaveBeenCalled();
        expect(grid.api.getDisplayedRowCount()).toBe(0);
        expect(enqueueSnackbarMock).not.toHaveBeenCalled();
      } finally {
        grid.close();
        await act(async () => {
          replacement.resolve(sessionResponse({ rows: [], hasMore: false, nextCursor: null }));
          await Promise.all(grid.reads.map((read) => read.settled));
        });
        refresh.mockRestore();
      }
    },
  );

  it("retains cached reads and page-2 cursors until manual reload, while auto refresh stays paused", async () => {
    const firstRows = Array.from({ length: 25 }, (_, index) => row(index));
    getMock
      .mockResolvedValueOnce(sessionResponse({ rows: firstRows, hasMore: true, nextCursor: "opaque-page-2" }))
      .mockResolvedValueOnce(sessionResponse({ rows: [row(25)], hasMore: false, nextCursor: null }))
      .mockResolvedValueOnce(sessionResponse({ rows: [row(99)], hasMore: false, nextCursor: null }));
    renderGrid();
    const first = makeParams();
    await getRows(first);
    await getRows(first);
    expect(getMock).toHaveBeenCalledOnce();
    expect(first.success).toHaveBeenLastCalledWith({ rowData: firstRows, rowCount: 26 });

    await userEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    const second = makeParams({ startRow: 25 });
    await getRows(second);
    expect(getMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls[1][1].params.cursor).toBe("opaque-page-2");
    act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
    expect(second.api.refreshServerSide).not.toHaveBeenCalled();
    expect(second.api.paginationGoToFirstPage).not.toHaveBeenCalled();
    await getRows(second);
    expect(getMock).toHaveBeenCalledTimes(2);
    expect(second.success).toHaveBeenLastCalledWith({ rowData: [row(25)], rowCount: 26 });

    act(() => window.dispatchEvent(new Event("observe-refresh")));
    expect(second.api.paginationGoToFirstPage).toHaveBeenCalledOnce();
    expect(second.api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({ purge: false });
    const reloaded = makeParams();
    await getRows(reloaded);
    expect(getMock).toHaveBeenCalledTimes(3);
    expect(getMock.mock.calls[2][1].params).toEqual(getMock.mock.calls[0][1].params);
    expect(reloaded.success).toHaveBeenCalledExactlyOnceWith({ rowData: [row(99)], rowCount: 1 });
  });

  it.each(["success", "failure"])(
    "manual reload supersedes an active read without letting late %s release the replacement guard",
    async (outcome) => {
      const old = deferredCompletion();
      const current = deferredCompletion();
      getMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
      renderGrid();
      const grid = await completionGrid(gridState.props.serverSideDatasource);
      const refresh = vi.spyOn(grid.api, "refreshServerSide");
      try {
        await waitFor(() => expect(getMock).toHaveBeenCalledOnce());
        const oldSignal = getMock.mock.calls[0][1].signal;
        act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
        expect(refresh).not.toHaveBeenCalled();
        expect(oldSignal.aborted).toBe(false);

        act(() => {
          window.dispatchEvent(new Event("observe-refresh"));
          window.dispatchEvent(new Event("observe-refresh"));
        });
        expect(oldSignal.aborted).toBe(true);
        await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
        expect(refresh).toHaveBeenCalledExactlyOnceWith({ purge: false });
        expect(grid.reads[0].fail).toHaveBeenCalledOnce();
        expect(grid.reads[0].success).not.toHaveBeenCalled();
        refresh.mockClear();
        await act(async () => {
          if (outcome === "success") old.resolve(sessionResponse({ rows: [row(1)], hasMore: false }));
          else old.reject(new Error("obsolete transport failure"));
          await grid.reads[0].settled;
        });
        act(() => window.dispatchEvent(new Event(OBSERVE_LIST_REFRESH_EVENT)));
        expect(refresh).not.toHaveBeenCalled();
        expect(getMock).toHaveBeenCalledTimes(2);
        expect(getMock.mock.calls[1][1].signal.aborted).toBe(false);
        expect(grid.reads[1].success).not.toHaveBeenCalled();
        expect(grid.reads[1].fail).not.toHaveBeenCalled();
        expect(grid.api.getDisplayedRowAtIndex(0)?.data).toBeUndefined();
        expect(enqueueSnackbarMock).not.toHaveBeenCalled();

        await act(async () => {
          current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
          await grid.reads[1].settled;
        });
        expect(grid.api.getDisplayedRowAtIndex(0)?.data).toEqual(row(99));
        expect(grid.reads[0].fail).toHaveBeenCalledOnce();
        expect(grid.reads[0].success).not.toHaveBeenCalled();
        expect(grid.reads[1].success).toHaveBeenCalledOnce();
        expect(grid.reads[1].fail).not.toHaveBeenCalled();
      } finally {
        grid.close();
        await act(async () => {
          old.resolve(sessionResponse());
          current.resolve(sessionResponse({ rows: [row(99)], hasMore: false }));
          await Promise.all(grid.reads.map((read) => read.settled));
        });
        refresh.mockRestore();
      }
    },
  );
});

describe("SessionGrid cursor continuation", () => {
  beforeEach(() => {
    getMock.mockReset();
    enqueueSnackbarMock.mockReset();
    gridState.props = null;
    gridState.api = null;
  });

  it.each(["project-1", null])("hands the successful %s list context to the drawer", async (projectId) => {
    const filters = [{ column_id: "created_at", filter_config: { filter_type: "datetime",
      filter_op: "between", filter_value: ["2026-07-01", "2026-08-01"] } },
      { column_id: "company_id", property_id: "custom_attribute:company_id", source: "traces", filter_config: {
        col_type: "SPAN_ATTRIBUTE", filter_type: "text", filter_op: "in", filter_value: [2, 5, 10],
        attribute_value_types: ["number", "number", "number"] } }];
    const selected = row(1);
    getMock.mockResolvedValue(sessionResponse({ rows: [selected], hasMore: false }));
    renderGrid({ projectId, filters, userIdForUserMode: projectId ? undefined : "public-user" });
    await waitFor(() => expect(gridState.props).not.toBeNull());
    const params = makeParams({ sortModel: [{ colId: "total_tokens", sort: "asc" }] });
    await getRows(params);
    await act(async () => gridState.props.onRowClicked({ data: params.success.mock.calls[0][0].rowData[0] }));
    expect(gridState.drawer.navigationContext).toEqual({
      project_id: projectId, workspace_id: "workspace-1", filters,
      sort_params: [{ column_id: "total_tokens", direction: "asc" }],
      cursor_mode: false,
      ...(projectId ? {} : { user_id: "public-user" }),
    });
    filters[1].filter_config.filter_value[0] = 99;
    expect(gridState.drawer.navigationContext.filters[1].filter_config.filter_value).toEqual([2, 5, 10]);
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

    expect(params.fail).toHaveBeenCalledOnce();
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

  it("settles cancelled session reads without releasing the replacement page loader", async () => {
    let resolveOld;
    let rejectCurrent;
    getMock
      .mockResolvedValueOnce(
        sessionResponse({
          rows: Array.from({ length: 25 }, (_, index) => row(index)),
          hasMore: true,
          nextCursor: "page-2",
          totalRows: 26,
        }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectCurrent = reject;
          }),
      );
    renderGrid();
    await getRows(makeParams());
    await userEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    const oldParams = makeParams({ startRow: 25 });
    const settled = vi.fn();
    let oldRead;
    act(() => {
      oldRead = gridState.props.serverSideDatasource
        .getRows(oldParams)
        .then(settled);
    });
    await waitFor(() => expect(resolveOld).toBeTypeOf("function"));
    const oldSignal = getMock.mock.calls[1][1].signal;
    const currentParams = makeParams({
      startRow: 25,
      sortModel: [{ colId: "started_at", sort: "desc" }],
    });
    gridState.api = currentParams.api;
    let currentRead;
    act(() => {
      currentRead = gridState.props.serverSideDatasource.getRows(currentParams);
    });
    await waitFor(() => expect(rejectCurrent).toBeTypeOf("function"));
    try {
      await waitFor(() => expect(settled).toHaveBeenCalledOnce());
      expect(oldSignal.aborted).toBe(true);
      expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
      expect(screen.getByRole("button", { name: "page 2" })).toBeDisabled();
      expect(oldParams.success).not.toHaveBeenCalled();
      expect(oldParams.fail).toHaveBeenCalledOnce();
      expect(currentParams.success).not.toHaveBeenCalled();
      expect(enqueueSnackbarMock).not.toHaveBeenCalled();
      expect(currentParams.api.showNoRowsOverlay).not.toHaveBeenCalled();
      expect(getMock.mock.calls[2][1].params).not.toHaveProperty("cursor");
      await act(async () => {
        rejectCurrent(new Error("replacement transport failed"));
        await currentRead;
      });
      expect(currentParams.fail).toHaveBeenCalledOnce();
      expect(currentParams.success).not.toHaveBeenCalled();
      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        "Session data could not be loaded. Please retry.",
        { variant: "error" },
      );
      expect(currentParams.api.showNoRowsOverlay).not.toHaveBeenCalled();
      await waitFor(() =>
        expect(screen.queryByText("Loading page…")).not.toBeInTheDocument(),
      );
    } finally {
      await act(async () => {
        resolveOld(sessionResponse({ rows: [row(1)] }));
        rejectCurrent(new Error("replacement transport failed"));
        await Promise.all([oldRead, currentRead]);
      });
    }
    expect(oldParams.success).not.toHaveBeenCalled();
    expect(oldParams.fail).toHaveBeenCalledOnce();
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
    expect(staleParams.fail).toHaveBeenCalledOnce();
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
    expect(params.fail).toHaveBeenCalledOnce();
    expect(enqueueSnackbarMock).not.toHaveBeenCalled();
  });
});
