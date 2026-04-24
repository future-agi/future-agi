import { useMemo, useCallback } from "react";
import { useSearchParams } from "react-router-dom";

// ---------------------------------------------------------------------------
// Bidirectional mapping: camelCase <-> snake_case URL param names.
// Keys that are identical in both conventions (model, provider, ordering)
// are intentionally omitted -- they pass through unchanged.
// ---------------------------------------------------------------------------
const CAMEL_TO_SNAKE = {
  gatewayId: "gateway_id",
  startedAfter: "started_after",
  startedBefore: "started_before",
  minLatency: "min_latency",
  maxLatency: "max_latency",
  minCost: "min_cost",
  maxCost: "max_cost",
  isError: "is_error",
  cacheHit: "cache_hit",
  guardrailTriggered: "guardrail_triggered",
  statusCode: "status_code",
  userId: "user_id",
  sessionId: "session_id",
  apiKeyId: "api_key_id",
};

// Keys that are NOT content filters (excluded from active filter count).
const NON_CONTENT_KEYS = new Set([
  "view",
  "page",
  "pageSize",
  "sort",
  "search",
]);

// Build the reverse map once at module load time.
const SNAKE_TO_CAMEL = Object.fromEntries(
  Object.entries(CAMEL_TO_SNAKE).map(([camel, snake]) => [snake, camel]),
);

/** Convert a camelCase filter key to its snake_case URL param name. */
function toUrlKey(camelKey) {
  return CAMEL_TO_SNAKE[camelKey] || camelKey;
}

/** Convert a snake_case URL param name to its camelCase JS key. */
function fromUrlKey(snakeKey) {
  return SNAKE_TO_CAMEL[snakeKey] || snakeKey;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Syncs filter state with the browser URL search params.
 *
 * URL params use snake_case; the returned `filters` object uses camelCase.
 *
 * @returns {{
 *   filters: Object,
 *   setFilter: (key: string, value: string|null|undefined) => void,
 *   setFilters: (obj: Object) => void,
 *   clearFilters: () => void,
 *   activeFilterCount: number,
 * }}
 */
export default function useFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Build a camelCase filters object from the current URL params.
  const filters = useMemo(() => {
    const obj = {};
    for (const [snakeKey, value] of searchParams.entries()) {
      const camelKey = fromUrlKey(snakeKey);
      obj[camelKey] = value;
    }
    return obj;
  }, [searchParams]);

  // Set a single filter. Passing null / undefined removes it.
  const setFilter = useCallback(
    (key, value) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const urlKey = toUrlKey(key);

          if (value === null || value === undefined || value === "") {
            next.delete(urlKey);
          } else {
            next.set(urlKey, value);
          }

          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Replace all URL params with the provided object.
  // null / undefined / empty-string values are omitted.
  const setFilters = useCallback(
    (obj) => {
      const next = new URLSearchParams();

      Object.entries(obj).forEach(([key, value]) => {
        if (value === null || value === undefined || value === "") return;
        const urlKey = toUrlKey(key);
        next.set(urlKey, value);
      });

      setSearchParams(next, { replace: true });
    },
    [setSearchParams],
  );

  // Remove every filter param from the URL.
  const clearFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  // Count of content-filter values (exclude pagination/sort/view/search).
  const activeFilterCount = useMemo(
    () => Object.keys(filters).filter((k) => !NON_CONTENT_KEYS.has(k)).length,
    [filters],
  );

  return { filters, setFilter, setFilters, clearFilters, activeFilterCount };
}
