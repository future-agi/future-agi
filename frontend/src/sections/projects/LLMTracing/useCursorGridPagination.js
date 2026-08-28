import { useCallback, useMemo, useRef, useState } from "react";
import {
  OBSERVE_LIST_DEFAULT_PAGE_SIZE,
  OBSERVE_LIST_PAGE_SIZE_OPTIONS,
} from "src/config/runtime_limits";
import { withLiveGridApi } from "src/utils/gridApi";

/**
 * Cursor-backed lists can expose only pages whose opaque cursor chain has
 * already been discovered. Keep AG Grid's synthetic row count and the visible
 * page controls aligned without publishing a guessed global total.
 */
export default function useCursorGridPagination(gridRef) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(OBSERVE_LIST_DEFAULT_PAGE_SIZE);
  const [pageCount, setPageCount] = useState(1);
  const discoveredRowCountRef = useRef(0);

  const resetPagination = useCallback(
    ({ moveGrid = true } = {}) => {
      discoveredRowCountRef.current = 0;
      setPage(1);
      setPageCount(1);
      if (moveGrid) {
        withLiveGridApi(gridRef?.current?.api, (api) =>
          api.paginationGoToFirstPage?.(),
        );
      }
    },
    [gridRef],
  );

  const publishPage = useCallback(({ request, rows, isLastPage }) => {
    const requestPageSize = request.endRow - request.startRow;
    const terminalRowCount = request.startRow + rows.length;
    const nextPageSentinelRowCount = request.endRow + 1;

    if (isLastPage) {
      discoveredRowCountRef.current = terminalRowCount;
    } else {
      discoveredRowCountRef.current = Math.max(
        discoveredRowCountRef.current,
        nextPageSentinelRowCount,
      );
    }

    const discoveredRowCount = discoveredRowCountRef.current;
    setPage(Math.floor(request.startRow / requestPageSize) + 1);
    setPageCount(Math.max(1, Math.ceil(discoveredRowCount / requestPageSize)));
    return discoveredRowCount;
  }, []);

  const onPaginationChanged = useCallback((event) => {
    const currentPage = event.api?.paginationGetCurrentPage?.();
    if (Number.isSafeInteger(currentPage) && currentPage >= 0) {
      setPage(currentPage + 1);
    }
  }, []);

  const goToPage = useCallback(
    (nextPage) => {
      if (
        !Number.isSafeInteger(nextPage) ||
        nextPage < 1 ||
        nextPage > pageCount
      ) {
        return;
      }
      withLiveGridApi(gridRef?.current?.api, (api) =>
        api.paginationGoToPage?.(nextPage - 1),
      );
    },
    [gridRef, pageCount],
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

  return useMemo(
    () => ({
      page,
      pageCount,
      pageSize,
      changePageSize,
      goToPage,
      onPaginationChanged,
      publishPage,
      resetPagination,
    }),
    [
      changePageSize,
      goToPage,
      onPaginationChanged,
      page,
      pageCount,
      pageSize,
      publishPage,
      resetPagination,
    ],
  );
}
