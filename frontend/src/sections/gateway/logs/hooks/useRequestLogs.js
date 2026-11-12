import { useQuery, keepPreviousData } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import {
  AGENT_BILLING_FILTER_PARAMS,
  dateFilterParam,
} from "../explorerFilters";
import { timeZoneParam } from "../../utils/timeZone";

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
  fallbackUsed: "fallback_used",
  isStream: "is_stream",
  requestId: "request_id",
  minTokens: "min_tokens",
  maxTokens: "max_tokens",
  minLatency: "min_latency",
  maxLatency: "max_latency",
  minCost: "min_cost",
  maxCost: "max_cost",
  startedAfter: "started_after",
  startedBefore: "started_before",
  ...AGENT_BILLING_FILTER_PARAMS,
};

// Date filters go out with an offset: a zone-less value (a link saved before
// the filters carried one) is the local time it was typed in, never UTC.
const DATE_PARAMS = new Set(["started_after", "started_before"]);

/**
 * Convert a camelCase filters object into query-param-ready snake_case params.
 * Null / undefined / empty-string values are omitted; a zone-less time is
 * sent as the local instant the page shows (dateFilterParam).
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

// Keys that are URL state, not API filters (search goes as q).
const URL_ONLY = ["sort", "view", "page", "pageSize", "search", "log"];

/** The table's filters as the list API takes them; a search goes as q. */
function matchingParams(filters) {
  const params = buildFilterParams(filters);
  URL_ONLY.forEach((key) => delete params[key]);
  const searchTerm = (filters.search || "").trim();
  if (searchTerm.length >= 2) params.q = searchTerm;
  return params;
}

// The query the Requests table sends for these filters, without its page:
// the filters, the search as `q` (2+ characters, as the table searches), and
// the table's order. Export counts and downloads with exactly this, so what
// it downloads is what the table lists.
export function requestLogQueryParams(filters = {}) {
  return {
    ...matchingParams(filters),
    ordering: filters.sort || "-started_at",
  };
}

/** One page of logs for the filters, from the list or (with a search) the search endpoint. */
async function fetchLogs(filters, { page, pageSize, ordering }) {
  const params = {
    ...matchingParams(filters),
    page,
    limit: pageSize,
    ordering,
  };
  if (params.q) {
    const res = await axiosInstance.get(endpoints.gateway.requestLogSearch, {
      params,
    });
    return res.data;
  }
  const res = await axiosInstance.get(endpoints.gateway.requestLogs, {
    params,
  });
  return res.data;
}

/**
 * When the oldest request that matches the filters started (null when none
 * does): the start of the table's window when it has no start time, so the
 * Logs volume strip covers the same requests as the table.
 */
export function useOldestRequestTime(filters = {}, { enabled = true } = {}) {
  const { page: _page, sort: _sort, log: _log, ...matching } = filters;
  return useQuery({
    queryKey: ["requestLogs", "oldest", matching],
    queryFn: async () => {
      const data = await fetchLogs(matching, {
        page: 1,
        pageSize: 1,
        ordering: "started_at",
      });
      const first = (data?.result?.results ?? data?.results ?? [])[0];
      return first?.started_at || null;
    },
    enabled,
    staleTime: 60000,
    refetchOnWindowFocus: false,
    meta: { errorHandled: true },
  });
}

/**
 * Requests and errors per bucket under the table's own filters (search
 * included), over `window` ({ start, end, granularity }): the Logs volume
 * strip, so it counts the requests the table lists. Day buckets are the
 * viewer's days when the API takes `tz` (`time_zone` in the result).
 */
export function useRequestVolume(filters = {}, window = {}, options = {}) {
  const { page: _page, sort: _sort, log: _log, ...matching } = filters;
  const { start, end, granularity } = window;
  return useQuery({
    queryKey: ["requestLogs", "volume", matching, start, end, granularity],
    queryFn: async () => {
      const params = {
        ...matchingParams(matching),
        start,
        end,
        granularity,
        ...timeZoneParam(),
      };
      const res = await axiosInstance.get(endpoints.gateway.requestLogVolume, {
        params,
      });
      return res.data?.result;
    },
    staleTime: 30000,
    refetchOnWindowFocus: false,
    ...options,
  });
}

/**
 * Fetches paginated request logs with optional filters.
 *
 * @param {Object}  options
 * @param {Object}  [options.filters={}]   - camelCase filter values
 * @param {number}  [options.page=1]       - 1-based page number
 * @param {number}  [options.pageSize=25]  - rows per page
 * @param {number|false} [options.refetchInterval] - auto-refresh period in ms
 * @returns {{ data: Object|undefined, isLoading: boolean, error: Error|null, refetch: Function }}
 */
export default function useRequestLogs({
  filters = {},
  page = 1,
  pageSize = 25,
  refetchInterval = false,
} = {}) {
  const sort = filters.sort || "-started_at";

  return useQuery({
    queryKey: ["requestLogs", filters, page, pageSize],
    queryFn: () => fetchLogs(filters, { page, pageSize, ordering: sort }),
    placeholderData: keepPreviousData,
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}
