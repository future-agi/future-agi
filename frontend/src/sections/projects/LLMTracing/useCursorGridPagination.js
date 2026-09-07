import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  OBSERVE_LIST_DEFAULT_PAGE_SIZE,
  OBSERVE_LIST_PAGE_SIZE_OPTIONS,
  OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS,
} from "src/config/runtime_limits";
import { withLiveGridApi } from "src/utils/gridApi";
import { dispatchObservePageChanged } from "../observeEvents";
import {
  EMPTY_PAGER_FRONTIER,
  getListPagerState,
  hasBufferedOverflowPage,
  pagerFlagsForPage,
} from "./listPagerState";

const requestRenderFrame = (callback) => {
  if (
    typeof window !== "undefined" &&
    typeof window.requestAnimationFrame === "function"
  ) {
    return window.requestAnimationFrame(callback);
  }
  return setTimeout(callback, 0);
};

const cancelRenderFrame = (frameId) => {
  if (
    typeof window !== "undefined" &&
    typeof window.cancelAnimationFrame === "function"
  ) {
    window.cancelAnimationFrame(frameId);
    return;
  }
  clearTimeout(frameId);
};

const renderedRowTokens = (api) => {
  const nodes = api?.getRenderedNodes?.();
  if (!Array.isArray(nodes)) return [];
  return nodes
    .filter((node) => node?.data != null)
    .map((node) => node.id ?? node.data);
};

export const paintedGridRowSignature = (api, gridElementRef) => {
  // Prefer the owning component's live wrapper once AG Grid has mounted into
  // it. Some production builds expose getGui(), but the returned element can
  // lag behind the visible server-side block while AG Grid swaps pages. That
  // stale tree made a transition look painted while the on-screen grid was
  // still blank. Tests and older grid builds can keep using getGui() until the
  // wrapper contains the actual grid DOM.
  const owningGridElement = gridElementRef?.current;
  const ownsLiveGridDom = Boolean(
    owningGridElement?.querySelector?.(".ag-center-cols-container"),
  );
  const gridElement = ownsLiveGridDom
    ? owningGridElement
    : api?.getGui?.() ?? owningGridElement;
  if (!gridElement || typeof gridElement.querySelectorAll !== "function") {
    return undefined;
  }

  const rows = Array.from(
    gridElement.querySelectorAll(
      ".ag-center-cols-container .ag-row[row-index]:not(.ag-row-loading)",
    ),
  );
  if (rows.length === 0) return null;

  // AG Grid resets row-index/row-id on each server-side page. Include the
  // rendered cell text so an old page cannot satisfy the new-page paint check.
  // During a block swap AG Grid can retain row shells after clearing every
  // cell. Those shells are not a painted page and must keep the loader active.
  const paintedRows = rows
    .map((row) => ({ row, text: (row.textContent ?? "").trim() }))
    .filter(({ text }) => text.length > 0);
  if (paintedRows.length === 0) return null;

  return paintedRows
    .map(
      ({ row, text }) =>
        `${row.getAttribute?.("row-index") ?? ""}:${row.getAttribute?.("row-id") ?? ""}:${text}`,
    )
    .join("\n");
};

/**
 * Cursor-backed lists can expose only pages whose opaque cursor chain has
 * already been discovered. Keep AG Grid's synthetic row count and the visible
 * page controls aligned without publishing a guessed global total.
 */
export default function useCursorGridPagination(gridRef, gridElementRef) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(OBSERVE_LIST_DEFAULT_PAGE_SIZE);
  const [pageCount, setPageCount] = useState(1);
  const [isPageLoading, setIsPageLoading] = useState(false);
  // The deepest page published in this pagination generation and the flags the
  // datasource reported there. Pager state has to be a function of the page
  // being *shown*, not of whichever page happened to load last: returning to
  // an already-cached page never re-invokes the datasource, so a
  // last-write-wins flag from the terminal page would stay false and kill
  // forward navigation until the next full refresh.
  const [frontier, setFrontier] = useState(EMPTY_PAGER_FRONTIER);
  const discoveredRowCountRef = useRef(0);
  const pageLoadRequestRef = useRef(0);
  const activePageLoadRequestRef = useRef(null);
  const pageTransitionRef = useRef(null);
  const pageTransitionSequenceRef = useRef(0);
  const renderCheckFrameRef = useRef(null);

  const stopRenderCheck = useCallback(() => {
    if (renderCheckFrameRef.current === null) return;
    cancelRenderFrame(renderCheckFrameRef.current);
    renderCheckFrameRef.current = null;
  }, []);

  const scheduleRenderCheck = useCallback(
    (transitionId) => {
      stopRenderCheck();

      const checkRenderedPage = () => {
        renderCheckFrameRef.current = null;
        const transition = pageTransitionRef.current;
        if (!transition || transition.id !== transitionId) return;

        const api = gridRef?.current?.api;
        const currentPage = api?.paginationGetCurrentPage?.();
        const nextTokens = renderedRowTokens(api);
        const targetPageIsVisible = currentPage === transition.page - 1;
        const rowTokensChanged =
          transition.previousRowTokens.size === 0 ||
          nextTokens.some((token) => !transition.previousRowTokens.has(token));
        const targetRowsAreRendered =
          targetPageIsVisible && nextTokens.length > 0;
        const paintedRowSignature = paintedGridRowSignature(
          api,
          gridElementRef,
        );
        const canInspectPaintedRows =
          transition.previousPaintedRowSignature !== undefined &&
          paintedRowSignature !== undefined;
        const targetRowsHavePainted =
          targetRowsAreRendered &&
          rowTokensChanged &&
          (!canInspectPaintedRows ||
            (paintedRowSignature !== null &&
              paintedRowSignature !== transition.previousPaintedRowSignature));

        if (!targetRowsHavePainted) {
          transition.lastReadyPaintedRowSignature = null;
          transition.readyFrameCount = 0;
        } else if (!canInspectPaintedRows) {
          transition.readyFrameCount += 1;
        } else if (
          transition.lastReadyPaintedRowSignature === paintedRowSignature
        ) {
          transition.readyFrameCount += 1;
        } else {
          transition.lastReadyPaintedRowSignature = paintedRowSignature;
          transition.readyFrameCount = 1;
        }
        const activeRequest = activePageLoadRequestRef.current;
        const targetRequestIsActive = activeRequest?.page === transition.page;
        const timedOutWithoutRequest =
          Date.now() >= transition.deadline &&
          activeRequest?.page !== transition.page;

        // AG Grid can expose the target RowNodes before it commits their DOM.
        // It can also publish those nodes while the server-side datasource is
        // still awaiting the page response. Never settle from that speculative
        // model state: wait for the target request to finish, then require a
        // painted row plus one complete follow-up frame so React's loading
        // state cannot be set and cleared inside the same paint.
        if (
          (!targetRequestIsActive && transition.readyFrameCount >= 2) ||
          timedOutWithoutRequest
        ) {
          pageTransitionRef.current = null;
          if (activeRequest?.page !== transition.page) {
            setIsPageLoading(false);
          }
          return;
        }

        renderCheckFrameRef.current = requestRenderFrame(checkRenderedPage);
      };

      renderCheckFrameRef.current = requestRenderFrame(checkRenderedPage);
    },
    [gridElementRef, gridRef, stopRenderCheck],
  );

  const beginPageLoad = useCallback((pageNumber) => {
    // AG Grid can prefetch a server-side block. Only an explicit navigation
    // owns the page loader; background cache work must not block the controls.
    const transition = pageTransitionRef.current;
    const requestedPage = pageNumber + 1;
    if (!transition || transition.page !== requestedPage) return null;
    const requestId = ++pageLoadRequestRef.current;
    activePageLoadRequestRef.current = { id: requestId, page: requestedPage };
    return requestId;
  }, []);

  const finishPageLoad = useCallback(
    (requestId, { succeeded = false, rowCount = 0 } = {}) => {
      if (requestId === null) return;
      const activeRequest = activePageLoadRequestRef.current;
      if (!activeRequest || activeRequest.id !== requestId) return;

      activePageLoadRequestRef.current = null;
      const transition = pageTransitionRef.current;
      if (
        transition?.page === activeRequest.page &&
        (!succeeded || rowCount === 0)
      ) {
        pageTransitionRef.current = null;
        stopRenderCheck();
        setIsPageLoading(false);
        return;
      }

      // A successful non-empty request is not visually complete until AG Grid
      // swaps the rendered row nodes. The render check started by goToPage()
      // owns the loader until that exact handoff.
      if (!transition) setIsPageLoading(false);
    },
    [stopRenderCheck],
  );

  const resetPagination = useCallback(
    ({ moveGrid = true } = {}) => {
      pageLoadRequestRef.current += 1;
      activePageLoadRequestRef.current = null;
      pageTransitionRef.current = null;
      pageTransitionSequenceRef.current += 1;
      stopRenderCheck();
      setIsPageLoading(false);
      discoveredRowCountRef.current = 0;
      setPage(1);
      setPageCount(1);
      setFrontier(EMPTY_PAGER_FRONTIER);
      if (moveGrid) {
        withLiveGridApi(gridRef?.current?.api, (api) =>
          api.paginationGoToFirstPage?.(),
        );
      }
    },
    [gridRef, stopRenderCheck],
  );

  const publishPage = useCallback(({ request, rows, isLastPage, metadata }) => {
    const requestPageSize = request.endRow - request.startRow;
    const terminalRowCount = request.startRow + rows.length;
    const nextPageSentinelRowCount = request.endRow + 1;
    const publishedPage = Math.floor(request.startRow / requestPageSize) + 1;

    if (isLastPage) {
      discoveredRowCountRef.current = terminalRowCount;
    } else {
      discoveredRowCountRef.current = Math.max(
        discoveredRowCountRef.current,
        nextPageSentinelRowCount,
      );
    }

    const discoveredRowCount = discoveredRowCountRef.current;
    // pageCount stays the navigation bound for goToPage. It is no longer
    // rendered; CursorGridPagination draws the window instead.
    setPageCount(Math.max(1, Math.ceil(discoveredRowCount / requestPageSize)));

    // AG Grid can load a server-side block in the background while an explicit
    // navigation is still in flight. Skip only when a transition is active
    // and targets a different page. This is *not* the same guard as
    // beginPageLoad(), which also rejects when no transition is active at
    // all — a background block published while nothing is navigating still
    // runs setPage() and advances the frontier here. That matches the
    // pre-fix behavior, so it is intentional: without an active transition
    // there is no "another page" for this one to lose to.
    const transition = pageTransitionRef.current;
    if (transition && transition.page !== publishedPage) {
      return discoveredRowCount;
    }

    setPage(publishedPage);

    const pagerState = getListPagerState({
      metadata,
      startRow: request.startRow,
      rowCount: rows.length,
    });
    const bufferedOverflowPage = hasBufferedOverflowPage(isLastPage, metadata);
    setFrontier((previous) =>
      publishedPage < previous.page
        ? previous
        : {
            page: publishedPage,
            hasMore: isLastPage
              ? false
              : pagerState.hasMore || bufferedOverflowPage,
            provenNext: isLastPage
              ? false
              : pagerState.provenNext || bufferedOverflowPage,
          },
    );

    return discoveredRowCount;
  }, []);

  const goToPage = useCallback(
    (nextPage) => {
      if (
        !Number.isSafeInteger(nextPage) ||
        nextPage < 1 ||
        nextPage > pageCount ||
        // Clicking the page already on screen starts a transition whose render
        // check can never see the rows change, leaving "Loading page…" up
        // until the transition times out.
        nextPage === page
      ) {
        return;
      }
      let transitionId = null;
      const moved = withLiveGridApi(gridRef?.current?.api, (api) => {
        transitionId = ++pageTransitionSequenceRef.current;
        pageTransitionRef.current = {
          id: transitionId,
          page: nextPage,
          previousRowTokens: new Set(renderedRowTokens(api)),
          previousPaintedRowSignature: paintedGridRowSignature(
            api,
            gridElementRef,
          ),
          lastReadyPaintedRowSignature: null,
          readyFrameCount: 0,
          deadline: Date.now() + OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS,
        };
        setIsPageLoading(true);
        api.paginationGoToPage?.(nextPage - 1);
      });
      if (moved) {
        // Cursor pagination is driven exclusively by these controls. AG Grid
        // can briefly report page zero again when a terminal row count is
        // published, so keep the requested page authoritative until the
        // datasource confirms it in publishPage().
        setPage(nextPage);
        dispatchObservePageChanged(nextPage);
        scheduleRenderCheck(transitionId);
      } else {
        pageTransitionRef.current = null;
        setIsPageLoading(false);
      }
    },
    [gridElementRef, gridRef, page, pageCount, scheduleRenderCheck],
  );

  useEffect(
    () => () => {
      stopRenderCheck();
      pageLoadRequestRef.current += 1;
      activePageLoadRequestRef.current = null;
      pageTransitionRef.current = null;
    },
    [stopRenderCheck],
  );

  const changePageSize = useCallback(
    (nextPageSize) => {
      if (
        nextPageSize === pageSize ||
        !OBSERVE_LIST_PAGE_SIZE_OPTIONS.includes(nextPageSize)
      ) {
        return;
      }
      resetPagination({ moveGrid: false });
      setPageSize(nextPageSize);
    },
    [pageSize, resetPagination],
  );

  return useMemo(() => {
    const { hasMore, provenNext } = pagerFlagsForPage(page, frontier);
    return {
      beginPageLoad,
      hasMore,
      page,
      pageCount,
      pageSize,
      provenNext,
      changePageSize,
      finishPageLoad,
      goToPage,
      isPageLoading,
      publishPage,
      resetPagination,
    };
  }, [
    beginPageLoad,
    changePageSize,
    finishPageLoad,
    frontier,
    goToPage,
    isPageLoading,
    page,
    pageCount,
    pageSize,
    publishPage,
    resetPagination,
  ]);
}
