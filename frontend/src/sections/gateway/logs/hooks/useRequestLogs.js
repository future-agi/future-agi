import { useQuery, keepPreviousData } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import {
  AGENT_BILLING_FILTER_PARAMS,
  dateFilterParam,
} from "../explorerFilters";

// ---------------------------------------------------------------------------
// Maps camelCase filter keys to the snake_case param names expected by the API.
// Keys that are already identical in both conventions are omitted.
// ---------------------------------------------------------------------------
const FILTER_KEY_MAP = {
  gatewayId: "gateway_id",
  statusCode: "status_code",
  statusCodeMin: "min_status_code",
  statusCodeMax: "max_status_code",
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
  ...AGENT_BILLING_FILTER_PARAMS,
};

// Date filters go out with an offset: a naive value is the local time it was
// typed in, never UTC.
const DATE_PARAMS = new Set(["started_after", "started_before"]);

/**
 * Convert a camelCase filters object into query-param-ready snake_case params.
 * Null / undefined / empty-string values are omitted.
 */
export function buildFilterParams(filters = {}) {
  const params = {};

  Object.entries(filters).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") return;
    const paramKey = FILTER_KEY_MAP[key] || key;
    params[paramKey] = DATE_PARAMS.has(paramKey)
      ? dateFilterParam(value)
      : value;
  });

  return params;
}

// The query the Requests table sends for these filters, without its page:
// the filters, the search as `q` (2+ characters, as the table searches), and
// the table's order. Export counts and downloads with exactly this, so what
// it downloads is what the table lists.
export function requestLogQueryParams(filters = {}) {
  const params = {
    ...buildFilterParams(filters),
    ordering: filters.sort || "-started_at",
  };
  delete params.sort;
  delete params.view;
  delete params.page;
  delete params.pageSize;
  delete params.search;
  const searchTerm = (filters.search || "").trim();
  if (searchTerm.length >= 2) params.q = searchTerm;
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
        ...requestLogQueryParams({ ...filters, sort }),
        page,
        limit: pageSize,
      };

      // Use the dedicated search endpoint when a search term is present
      if (searchTerm.length >= 2) {
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
