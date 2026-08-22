import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Hook to fetch paginated eval template list with filtering and search.
 */
export function useEvalsList({
  page = 0,
  pageSize = 25,
  search = null,
  ownerFilter = "all",
  filters = null,
  filterCombinator = "and",
  sortBy = "updated_at",
  sortOrder = "desc",
  enabled = true,
} = {}) {
  return useQuery({
    queryKey: [
      "evals",
      "list",
      page,
      pageSize,
      search,
      ownerFilter,
      filters,
      filterCombinator,
      sortBy,
      sortOrder,
    ],
    queryFn: async () => {
      const { data } = await axios.post(
        endpoints.develop.eval.listEvalTemplates,
        {
          page,
          page_size: pageSize,
          search: search || null,
          owner_filter: ownerFilter,
          filters,
          filter_combinator: filterCombinator,
          sort_by: sortBy,
          sort_order: sortOrder,
        },
      );
      return data?.result;
    },
    enabled,
    keepPreviousData: true,
  });
}

/**
 * Hook to fetch 30-day chart data for a list of template IDs.
 * Called separately so the table renders instantly while charts load async.
 * Uses ClickHouse for fast analytics.
 */
export function useEvalsListCharts(templateIds = []) {
  return useQuery({
    queryKey: ["evals", "list-charts", ...templateIds],
    queryFn: async () => {
      if (!templateIds.length) return {};
      const { data } = await axios.post(
        endpoints.develop.eval.listEvalTemplateCharts,
        { template_ids: templateIds },
      );
      return data?.result?.charts || {};
    },
    enabled: templateIds.length > 0,
    staleTime: 30 * 1000, // 30s — charts don't change frequently
  });
}

/**
 * Hook to bulk delete eval templates.
 * Invalidates the eval list cache on success.
 */
export function useBulkDeleteEvals() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (templateIds) => {
      const { data } = await axios.post(
        endpoints.develop.eval.bulkDeleteEvalTemplates,
        {
          template_ids: templateIds,
        },
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evals", "list"] });
    },
  });
}
