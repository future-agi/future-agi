import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { getQueryReadState } from "src/utils/queryReadState";
import {
  ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  INTERACTIVE_TABLE_PAGE_SIZE,
  PROPERTY_CATALOG_CACHE_TIME_MS,
  PROPERTY_CATALOG_STALE_TIME_MS,
} from "src/config/runtime_limits";
import {
  attributeInventoryKey,
  useCursorAttributeInventory,
} from "src/sections/projects/LLMTracing/useCursorAttributeInventory";
import {
  ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  compactAttributeKeyRetryPage,
  getAttributeKeyCursorStopSignature,
  getNextAttributeKeyPageParam,
  isAttributeKeyCursorChainStopped,
  readAttributeKeyPage,
} from "src/sections/projects/LLMTracing/attributeKeyCursorPagination";

const EXACT_ATTRIBUTE_ROW_TYPES = {
  span: "spans",
  spans: "spans",
  trace: "traces",
  traces: "traces",
  session: "sessions",
  sessions: "sessions",
  voice: "voiceCalls",
  voicecall: "voiceCalls",
  voicecalls: "voiceCalls",
  voice_calls: "voiceCalls",
};

export function normalizeExactAttributeRowType(rowType) {
  return EXACT_ATTRIBUTE_ROW_TYPES[String(rowType || "").toLowerCase()] || null;
}

export function mergeTracingFieldNames(genericFields, exactFields) {
  return [
    ...new Set(
      [...(genericFields || []), ...(exactFields || [])].filter(
        (field) => typeof field === "string" && field,
      ),
    ),
  ];
}

export function retainedAttributeFieldName(attributeKey, rowType) {
  if (typeof attributeKey !== "string" || !attributeKey) return null;
  const normalizedRowType = normalizeExactAttributeRowType(rowType);
  if (normalizedRowType === "traces") return `spans.0.${attributeKey}`;
  if (normalizedRowType === "sessions") {
    return `traces.0.spans.0.${attributeKey}`;
  }
  return attributeKey;
}

function combineQueryReadStates(...states) {
  if (states.includes("error")) return "error";
  if (states.includes("degraded")) return "degraded";
  if (states.includes("sampled")) return "sampled";
  return "complete";
}

/** Rollout-only retained span-key adapter kept for compatibility coverage. */
export function useLegacyExactEvalAttributeFields({
  projectId,
  rowType,
  search,
  enabled = true,
}) {
  const queryClient = useQueryClient();
  const normalizedRowType = normalizeExactAttributeRowType(rowType);
  const rawSearch = String(search || "").trim();
  const debouncedSearch = useDebounce(
    rawSearch,
    ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  );
  let exactSearch = debouncedSearch;
  if (normalizedRowType === "traces" && exactSearch.startsWith("spans.0.")) {
    exactSearch = exactSearch.slice("spans.0.".length);
  } else if (
    normalizedRowType === "sessions" &&
    exactSearch.startsWith("traces.0.spans.0.")
  ) {
    exactSearch = exactSearch.slice("traces.0.spans.0.".length);
  }
  const retainedQueryKey = [
    "eval-attribute-retained",
    projectId,
    normalizedRowType,
  ];
  const exactQueryKey = [
    "eval-attribute-exact",
    projectId,
    normalizedRowType,
    exactSearch,
  ];
  const retainedRetryIdentity = JSON.stringify([projectId, normalizedRowType]);
  const exactRetryIdentity = JSON.stringify([
    projectId,
    normalizedRowType,
    exactSearch,
  ]);
  const exactRetryGestureIdentity = JSON.stringify([
    projectId,
    normalizedRowType,
    rawSearch,
  ]);
  const [cursorRetryState, setCursorRetryState] = useState({
    retained: null,
    exact: null,
  });
  const previousRetryIdentity = useRef({
    retained: retainedRetryIdentity,
    exact: exactRetryIdentity,
    exactGesture: exactRetryGestureIdentity,
  });
  const [freshRetryPending, setFreshRetryPending] = useState(false);

  // A stopped-cursor retry is one-shot only within one settled query identity.
  // Clear its marker when the project/row/search identity changes so returning
  // to a formerly cached search starts a fresh, bounded retry contract instead
  // of inheriting an exhausted marker from an earlier interaction.
  useEffect(() => {
    const retainedChanged =
      previousRetryIdentity.current.retained !== retainedRetryIdentity;
    const exactChanged =
      previousRetryIdentity.current.exact !== exactRetryIdentity ||
      previousRetryIdentity.current.exactGesture !== exactRetryGestureIdentity;
    previousRetryIdentity.current = {
      retained: retainedRetryIdentity,
      exact: exactRetryIdentity,
      exactGesture: exactRetryGestureIdentity,
    };
    if (!retainedChanged && !exactChanged) return;
    setCursorRetryState((current) => ({
      retained: retainedChanged ? null : current.retained,
      exact: exactChanged ? null : current.exact,
    }));
  }, [retainedRetryIdentity, exactRetryGestureIdentity, exactRetryIdentity]);

  const retainedQuery = useInfiniteQuery({
    // The retained project schema is deliberately independent of the task's
    // preview filters and scheduling window. Search also stays local so typing
    // cannot discard cursor progress through older retained rows.
    queryKey: retainedQueryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        pageSize: INTERACTIVE_TABLE_PAGE_SIZE,
        publishedData: queryClient.getQueryData(retainedQueryKey),
        signal,
        requestPage: (cursor, requestSignal = signal) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal: requestSignal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: INTERACTIVE_TABLE_PAGE_SIZE,
                discovery_mode: "eval_mapping",
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data }) => data || {}),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: enabled && Boolean(projectId) && Boolean(normalizedRowType),
    retry: false,
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_CACHE_TIME_MS,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    meta: { errorHandled: true },
  });

  // Search the same retained-data endpoint as a supplemental fast path while
  // the base cursor walks older project data.  This request is deliberately
  // non-authoritative: a slow/failed exact lookup must not disable the mapping
  // control, publish a warning, or hide names already loaded by the catalog.
  const exactQuery = useInfiniteQuery({
    queryKey: exactQueryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        pageSize: INTERACTIVE_TABLE_PAGE_SIZE,
        publishedData: queryClient.getQueryData(exactQueryKey),
        signal,
        requestPage: (cursor, requestSignal = signal) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal: requestSignal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: INTERACTIVE_TABLE_PAGE_SIZE,
                discovery_mode: "eval_mapping",
                q: exactSearch,
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data }) => data || {}),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled:
      enabled &&
      Boolean(projectId) &&
      Boolean(normalizedRowType) &&
      Boolean(exactSearch),
    retry: false,
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_CACHE_TIME_MS,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    // The mapping picker keeps free-text entry available on a failed read;
    // suppress the global backend-exception snackbar for this optional probe.
    meta: { errorHandled: true },
  });

  const retainedPages = retainedQuery.data?.pages || [];
  const exactPages = exactQuery.data?.pages || [];
  const retainedCursorStopped = isAttributeKeyCursorChainStopped(
    retainedQuery.data,
  );
  const exactCursorStopped = isAttributeKeyCursorChainStopped(exactQuery.data);
  const retainedStopSignature = getAttributeKeyCursorStopSignature(
    retainedQuery.data,
  );
  const exactStopSignature = getAttributeKeyCursorStopSignature(
    exactQuery.data,
  );
  const retainedStopRetryAttempted = Boolean(
    retainedStopSignature &&
      cursorRetryState.retained?.identity === retainedRetryIdentity &&
      cursorRetryState.retained?.signature === retainedStopSignature,
  );
  const exactStopRetryAttempted = Boolean(
    exactStopSignature &&
      cursorRetryState.exact?.identity === exactRetryIdentity &&
      cursorRetryState.exact?.signature === exactStopSignature,
  );
  const retainedStoppedRetryAvailable =
    retainedCursorStopped && !retainedStopRetryAttempted;
  const exactStoppedRetryAvailable =
    exactCursorStopped && !exactStopRetryAttempted;
  const seenRetainedKeys = new Set();
  const retainedFields = retainedPages.flatMap((page) =>
    (Array.isArray(page?.result) ? page.result : []).flatMap(({ key }) => {
      if (!key || seenRetainedKeys.has(key)) return [];
      seenRetainedKeys.add(key);
      const field = retainedAttributeFieldName(key, normalizedRowType);
      return field ? [field] : [];
    }),
  );
  const retainedReadState = retainedQuery.isError
    ? "error"
    : retainedCursorStopped
      ? "degraded"
      : combineQueryReadStates(...retainedPages.map(getQueryReadState));
  const seenExactKeys = new Set();
  const exactFields = exactPages.flatMap((page) =>
    (Array.isArray(page?.result) ? page.result : []).flatMap(({ key }) => {
      if (!key || seenExactKeys.has(key)) return [];
      seenExactKeys.add(key);
      const field = retainedAttributeFieldName(key, normalizedRowType);
      return field ? [field] : [];
    }),
  );
  const queryReadState = retainedReadState;
  const exactSearchMatched = Boolean(
    exactSearch &&
      exactPages.some(
        (page) =>
          page?.exact_match === true ||
          (Array.isArray(page?.result) &&
            page.result.some(({ key }) => key === exactSearch)),
      ),
  );
  const retainedInitialError =
    retainedQuery.isError && retainedPages.length === 0;
  const retainedHasNextPage =
    retainedQuery.hasNextPage ||
    retainedStoppedRetryAvailable ||
    retainedInitialError;
  // A failed continuation belongs only to the optional exact-q accelerator.
  // Do not make every later read-more gesture retry the same failed cursor:
  // retain the exact rows already published and resume the authoritative
  // no-q catalog on the next explicit gesture instead.
  const exactContinuationFailed = Boolean(
    exactSearch && exactQuery.isFetchNextPageError,
  );
  const shouldAdvanceExact =
    Boolean(exactSearch) && !exactSearchMatched && !exactContinuationFailed;
  const exactHasNextPage =
    shouldAdvanceExact &&
    (exactQuery.hasNextPage || exactStoppedRetryAvailable);
  // The exact-q cursor is only a supplemental accelerator. Give it first
  // priority while it can still advance, then resume the independent retained
  // catalog so an exact `foo` hit cannot hide later siblings such as
  // `foo_archive` or `foo.bar`. The two predicates are mutually exclusive, so
  // one explicit read-more gesture advances only one cursor chain.
  const shouldAdvanceRetained = !exactSearch || !exactHasNextPage;
  const hasNextPage =
    exactHasNextPage || (shouldAdvanceRetained && retainedHasNextPage);

  const fetchFreshPage = async ({ queryKey, exact, lane, retryState }) => {
    if (freshRetryPending) return undefined;
    setFreshRetryPending(true);
    try {
      const page = await readAttributeKeyPage({
        pageParam: null,
        pageSize: INTERACTIVE_TABLE_PAGE_SIZE,
        publishedData: undefined,
        requestPage: (cursor, signal) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: INTERACTIVE_TABLE_PAGE_SIZE,
                discovery_mode: "eval_mapping",
                ...(exact ? { q: exactSearch } : {}),
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data }) => data || {}),
      });
      const compactedPage = compactAttributeKeyRetryPage(
        queryClient.getQueryData(queryKey),
        page,
      );
      queryClient.setQueryData(queryKey, {
        pages: [compactedPage],
        pageParams: [null],
      });
      setCursorRetryState((current) => ({
        ...current,
        [lane]: retryState,
      }));
      return compactedPage;
    } finally {
      setFreshRetryPending(false);
    }
  };

  const fetchNextPage = (...args) => {
    const reads = [];
    if (shouldAdvanceRetained && retainedInitialError) {
      reads.push(
        fetchFreshPage({
          queryKey: retainedQueryKey,
          exact: false,
          lane: "retained",
          retryState: cursorRetryState.retained,
        }),
      );
    } else if (shouldAdvanceRetained && retainedStoppedRetryAvailable) {
      reads.push(
        fetchFreshPage({
          queryKey: retainedQueryKey,
          exact: false,
          lane: "retained",
          retryState: {
            identity: retainedRetryIdentity,
            signature: retainedStopSignature,
          },
        }),
      );
    } else if (shouldAdvanceRetained && retainedQuery.hasNextPage) {
      reads.push(retainedQuery.fetchNextPage(...args));
    }
    if (shouldAdvanceExact && exactStoppedRetryAvailable) {
      reads.push(
        fetchFreshPage({
          queryKey: exactQueryKey,
          exact: true,
          lane: "exact",
          retryState: {
            identity: exactRetryIdentity,
            signature: exactStopSignature,
          },
        }),
      );
    } else if (shouldAdvanceExact && exactQuery.hasNextPage) {
      reads.push(exactQuery.fetchNextPage(...args));
    }
    return reads.length === 1 ? reads[0] : Promise.allSettled(reads);
  };

  return {
    data: mergeTracingFieldNames(retainedFields, exactFields),
    queryReadState,
    debouncedSearch,
    isSupportedRowType: Boolean(normalizedRowType),
    // Only the retained inventory controls loading/error UI.  Exact search is
    // an opportunistic accelerator and free-text mapping remains available.
    isFetching: retainedQuery.isFetching,
    isLoading: retainedQuery.isLoading,
    isError: retainedQuery.isError,
    isSuccess: retainedQuery.isSuccess,
    error: retainedQuery.error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage:
      (shouldAdvanceRetained && retainedQuery.isFetchingNextPage) ||
      (shouldAdvanceExact && exactQuery.isFetchingNextPage) ||
      freshRetryPending ||
      (shouldAdvanceRetained &&
        retainedCursorStopped &&
        retainedQuery.isFetching) ||
      (shouldAdvanceExact && exactCursorStopped && exactQuery.isFetching),
    isFetchNextPageError:
      (shouldAdvanceRetained &&
        (retainedInitialError || retainedQuery.isFetchNextPageError)) ||
      (shouldAdvanceExact && exactQuery.isFetchNextPageError) ||
      (shouldAdvanceRetained && retainedStoppedRetryAvailable) ||
      (shouldAdvanceExact && exactStoppedRetryAvailable),
    cursorRetryExhausted:
      (shouldAdvanceRetained &&
        retainedCursorStopped &&
        retainedStopRetryAttempted) ||
      (shouldAdvanceExact && exactCursorStopped && exactStopRetryAttempted),
    pageCount: retainedPages.length + exactPages.length,
    browseStatus:
      (exactSearch ? exactPages.at(-1)?.browse_status : undefined) ||
      retainedPages.at(-1)?.browse_status,
  };
}

export function useExactEvalAttributeFields({
  projectId,
  rowType,
  search,
  enabled = true,
}) {
  const normalizedRowType = normalizeExactAttributeRowType(rowType);
  const inventory = useCursorAttributeInventory({
    projectId,
    rowType: normalizedRowType || "spans",
    discoveryMode: "eval_mapping",
    search,
    enabled: enabled && Boolean(normalizedRowType),
    pageSize: INTERACTIVE_TABLE_PAGE_SIZE,
  });
  const data = mergeTracingFieldNames(
    [],
    inventory.rawAttributes
      .map((attribute) =>
        retainedAttributeFieldName(
          attributeInventoryKey(attribute),
          normalizedRowType,
        ),
      )
      .filter(Boolean),
  );
  const queryReadState = inventory.isError
    ? "error"
    : inventory.cursorRetryExhausted
      ? "degraded"
      : "complete";

  return {
    data,
    queryReadState,
    debouncedSearch: inventory.debouncedSearch,
    isSupportedRowType: Boolean(normalizedRowType),
    isFetching: inventory.isFetching,
    isLoading: inventory.isLoading,
    isError: inventory.isError,
    isSuccess: !inventory.isError && !inventory.isLoading,
    error: inventory.error,
    fetchNextPage: inventory.fetchNextPage,
    hasNextPage: inventory.hasNextPage,
    isFetchingNextPage: inventory.isFetchingNextPage,
    isFetchNextPageError: inventory.isFetchNextPageError,
    cursorRetryExhausted: inventory.cursorRetryExhausted,
    pageCount: inventory.pageCount,
    browseStatus: inventory.hasNextPage ? "continuation" : "exhausted",
  };
}
