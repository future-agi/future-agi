import { useMemo, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";

/**
 * Default column configuration for execution grids
 */
export const DEFAULT_COLUMN_DEF = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

/**
 * Default grid options for execution grids
 */
export const DEFAULT_GRID_OPTIONS = {
  pagination: true,
  rowSelection: { mode: "multiRow" },
  paginationAutoPageSize: true,
};

/**
 * Shared hook for execution grid data source and handlers
 *
 * @param {Object} options - Configuration options
 * @param {string} options.entityId - The ID of the entity (simulation/test)
 * @param {string} options.queryKey - The query key prefix for react-query
 * @param {string} options.searchQuery - Search query string
 * @param {Function} options.onTotalRowCountChange - Callback when total row count changes
 * @param {React.RefObject} options.gridRef - Ref to the grid component
 */
export const useExecutionGridDataSource = ({
  entityId,
  queryKey,
  searchQuery = "",
  onTotalRowCountChange,
  gridRef,
}) => {
  const queryClient = useQueryClient();
  const debouncedSearchQuery = useDebounce(searchQuery?.trim() || "", 300);

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;
        const pageSize = request ? request?.endRow - request?.startRow : 10;
        const pageNumber = Math.floor((request?.startRow ?? 1) / pageSize);
        try {
          const { data } = await queryClient.fetchQuery({
            queryKey: [
              queryKey,
              entityId,
              pageNumber,
              pageSize,
              debouncedSearchQuery,
            ],
            queryFn: () =>
              axios.get(endpoints.runTests.detailExecutions(entityId), {
                params: {
                  page: pageNumber + 1,
                  limit: pageSize,
                  search: debouncedSearchQuery || undefined,
                },
              }),
          });
          // Let AG Grid handle overlay automatically based on rowData
          // Only manually show overlay when we're certain there's no data
          if (data?.results?.length === 0 && data?.count === 0) {
            gridRef.current?.api?.showNoRowsOverlay();
          } else {
            gridRef.current?.api?.hideOverlay();
          }
          onTotalRowCountChange?.(data?.count || 0);
          gridRef.current?.api?.setGridOption("context", {
            totalRowCount: data?.count,
          });
          params.success({
            rowData: data?.results,
            rowCount: data?.count,
          });

          // Prefetch next page so scroll feels instant
          const results = data?.results || [];
          if (
            results.length === pageSize &&
            (request?.startRow ?? 0) + results.length < (data?.count || 0)
          ) {
            queryClient.prefetchQuery({
              queryKey: [
                queryKey,
                entityId,
                pageNumber + 1,
                pageSize,
                debouncedSearchQuery,
              ],
              queryFn: () =>
                axios.get(endpoints.runTests.detailExecutions(entityId), {
                  params: {
                    page: pageNumber + 2,
                    limit: pageSize,
                    search: debouncedSearchQuery || undefined,
                  },
                }),
            });
          }
        } catch (error) {
          params.fail();
          // Don't show overlay on error - let the grid handle it
          // This prevents overlay from appearing when there's existing data
        }
      },
      getRowId: (data) => data.id,
    }),
    [
      entityId,
      queryClient,
      debouncedSearchQuery,
      queryKey,
      onTotalRowCountChange,
      gridRef,
    ],
  );

  return {
    dataSource,
    debouncedSearchQuery,
  };
};

/**
 * Hook for debounced cell click handling (prevents double-click issues)
 */
export const useDebouncedCellClick = () => {
  const performedClicks = useRef(0);
  const clickTimeout = useRef(null);

  const debounceCellClick = useCallback((handler, event, delay = 0) => {
    performedClicks.current++;
    clickTimeout.current = setTimeout(() => {
      if (performedClicks.current === 1) {
        performedClicks.current = 0;
        handler(event);
      } else {
        performedClicks.current = 0;
      }
    }, delay);
    if (performedClicks.current > 1 && clickTimeout.current) {
      clearTimeout(clickTimeout.current);
    }
  }, []);

  return debounceCellClick;
};

/**
 * Creates a row selection change handler for server-side selection
 *
 * @param {Function} setSelectionState - Function to update selection state (Zustand store setter)
 */
export const createRowSelectionHandler = (setSelectionState) => {
  return ({ api, context }) => {
    const totalRowCount = context?.totalRowCount;
    const { selectAll, toggledNodes } = api.getServerSideSelectionState();

    if (selectAll && totalRowCount - toggledNodes.length === 0) {
      api.deselectAll();
    }

    setSelectionState({
      toggledNodes: toggledNodes,
      selectAll: selectAll,
    });
  };
};

export default useExecutionGridDataSource;
