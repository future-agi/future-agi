import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const DEFAULT_PAGE_SIZE = 50;
const FIRST_PAGE_SIZE = 200;

// Lazy + server-search. Use for autocomplete / dialog UX.
export function useEvalAttributesInfinite({
  projectId,
  rowType,
  filters,
  search = "",
  pageSize = DEFAULT_PAGE_SIZE,
  enabled = true,
} = {}) {
  const mergedFilters = useMemo(
    () => ({ project_id: projectId, ...(filters || {}) }),
    [projectId, filters],
  );

  const query = useInfiniteQuery({
    queryKey: [
      "eval-attributes-v2",
      projectId,
      rowType ?? null,
      mergedFilters,
      search,
      pageSize,
    ],
    enabled: Boolean(enabled && projectId),
    initialPageParam: 0,
    queryFn: async ({ pageParam = 0 }) => {
      const response = await axios.get(
        endpoints.project.getEvalAttributeList(),
        {
          params: {
            filters: JSON.stringify(mergedFilters),
            ...(rowType ? { row_type: rowType } : {}),
            page_number: pageParam,
            page_size: pageSize,
            ...(search ? { search } : {}),
          },
        },
      );
      return response.data?.result || {};
    },
    getNextPageParam: (lastPage, allPages) => {
      const total = lastPage?.metadata?.total_rows ?? 0;
      const loaded = allPages.reduce(
        (acc, p) => acc + (p?.items?.length || 0),
        0,
      );
      return loaded < total ? allPages.length : undefined;
    },
  });

  const items = useMemo(
    () => (query.data?.pages || []).flatMap((p) => p?.items || []),
    [query.data],
  );
  const totalRows = query.data?.pages?.[0]?.metadata?.total_rows ?? 0;

  return {
    items,
    totalRows,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isFetchingNextPage: query.isFetchingNextPage,
    hasMore: Boolean(query.hasNextPage),
    fetchMore: query.fetchNextPage,
    error: query.error,
    refetch: query.refetch,
  };
}

// One page, no auto-paginate. `totalRows` may exceed `items.length` —
// switch to ``useEvalAttributesInfinite`` if every attribute must be
// reachable (e.g. via search).
export function useEvalAttributesPage({
  projectId,
  rowType,
  filters,
  pageSize = FIRST_PAGE_SIZE,
  enabled = true,
} = {}) {
  const mergedFilters = useMemo(
    () => ({ project_id: projectId, ...(filters || {}) }),
    [projectId, filters],
  );

  const query = useQuery({
    queryKey: [
      "eval-attributes-page",
      projectId,
      rowType ?? null,
      mergedFilters,
      pageSize,
    ],
    enabled: Boolean(enabled && projectId),
    queryFn: async () => {
      const response = await axios.get(
        endpoints.project.getEvalAttributeList(),
        {
          params: {
            filters: JSON.stringify(mergedFilters),
            ...(rowType ? { row_type: rowType } : {}),
            page_number: 0,
            page_size: pageSize,
          },
        },
      );
      return response.data?.result || {};
    },
  });

  return {
    items: query.data?.items || [],
    totalRows: query.data?.metadata?.total_rows ?? 0,
    isLoading: query.isLoading,
    error: query.error,
    refetch: query.refetch,
  };
}
