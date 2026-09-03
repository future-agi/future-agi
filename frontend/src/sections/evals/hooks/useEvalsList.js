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
      if (!templateIds.length) {
        return {
          charts: {},
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          data_stale: false,
        };
      }
      const { data } = await axios.post(
        endpoints.develop.eval.listEvalTemplateCharts,
        { template_ids: templateIds },
      );
      const result = data?.result;

      // Keep the bounded-read contract alongside the chart payload. Dropping
      // these fields makes a cold degraded response (whose server-safe shape
      // contains zero-filled charts) indistinguishable from a genuine exact
      // zero. A stale cached chart is intentionally renderable, but only with
      // its stale label; a degraded/unmarked result is not chart data.
      if (!result || typeof result !== "object") {
        return {
          charts: {},
          query_complete: false,
          query_status: "degraded",
          query_sampled: false,
          query_error_code: "invalid_response",
          data_stale: false,
        };
      }
      const renderable =
        (result.query_complete === true &&
          result.query_status === "complete" &&
          result.query_sampled === false &&
          result.data_stale === false) ||
        (result.query_complete === false &&
          result.query_status === "stale" &&
          result.query_sampled === false &&
          result.data_stale === true);
      return {
        ...result,
        charts:
          renderable && result.charts && typeof result.charts === "object"
            ? result.charts
            : {},
      };
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
