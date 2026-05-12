import { useQuery, keepPreviousData } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";

// ---------------------------------------------------------------------------
// Maps camelCase filter keys to the snake_case param names expected by the API.
// Keys that are already identical in both conventions are omitted.
// ---------------------------------------------------------------------------
const FILTER_KEY_MAP = {
  gatewayId: "gateway_id",
  statusCode: "status_code",
  isError: "is_error",
  cacheHit: "cache_hit",
  guardrailTriggered: "guardrail_triggered",
  userId: "user_id",
  sessionId: "session_id",
  apiKeyId: "api_key_id",
  minLatency: "min_latency",
  maxLatency: "max_latency",
  minCost: "min_cost",
  maxCost: "max_cost",
  startedAfter: "started_after",
  startedBefore: "started_before",
};

/**
 * Convert a camelCase filters object into query-param-ready snake_case params.
 * Null / undefined / empty-string values are omitted.
 */
export function buildFilterParams(filters = {}) {
  const params = {};

  Object.entries(filters).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") return;
    const paramKey = FILTER_KEY_MAP[key] || key;
    params[paramKey] = value;
  });

  return params;
}

/**
 * Fetches paginated request logs with optional filters.
 *
 * @param {Object}  options
 * @param {Object}  [options.filters={}]   - camelCase filter values
 * @param {number}  [options.page=1]       - 1-based page number
 * @param {number}  [options.pageSize=25]  - rows per page
 * @returns {{ data: Object|undefined, isLoading: boolean, error: Error|null, refetch: Function }}
 */
export default function useRequestLogs({
  filters = {},
  page = 1,
  pageSize = 25,
} = {}) {
  const sort = filters.sort || "-started_at";
  const searchTerm = (filters.search || "").trim();

  return useQuery({
    queryKey: ["requestLogs", filters, page, pageSize],
    queryFn: async () => {
      const params = {
        ...buildFilterParams(filters),
        page,
        limit: pageSize,
        ordering: sort,
      };

      // Remove keys that are URL-only, not API params
      delete params.sort;
      delete params.view;
      delete params.pageSize;
      delete params.search;

      // Use the dedicated search endpoint when a search term is present
      if (searchTerm.length >= 2) {
        params.q = searchTerm;
        const res = await axiosInstance.get(
          endpoints.gateway.requestLogSearch,
          {
            params,
          },
        );
        return res.data;
      }

      const res = await axiosInstance.get(endpoints.gateway.requestLogs, {
        params,
      });
      return res.data;
    },
    placeholderData: keepPreviousData,
  });
}
