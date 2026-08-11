import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { getQueryReadState } from "src/utils/queryReadState";
import {
  ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  getAttributeKeyCursorStopSignature,
  getNextAttributeKeyPageParam,
  isAttributeKeyCursorChainStopped,
  readAttributeKeyPage,
} from "./attributeKeyCursorPagination";

const ATTRIBUTE_BROWSE_STATUSES = new Set([
  "continuation",
  "exhausted",
  "limit_reached",
]);

export function getAttributeKeyPageReadState(page, { exact = false } = {}) {
  if (exact && page?.lookup_mode === "exact" && page?.exact_match === true) {
    // A typed latest-state row verified the requested key. The surrounding
    // one-year absence proof may be bounded, but the positive exact match is
    // authoritative and must not inherit browse-sampling UI.
    return "complete";
  }
  if (page?.browse_mode === "recent_suggestions") {
    return page?.query_complete === true &&
      page?.query_status === "complete" &&
      ATTRIBUTE_BROWSE_STATUSES.has(page?.browse_status)
      ? "complete"
      : "degraded";
  }
  return getQueryReadState(page);
}

export function useExactTraceAttributeProperties({
  projectId,
  search,
  source = "traces",
  enabled = true,
}) {
  const debouncedSearch = useDebounce(String(search || "").trim(), 350);
  const supportedSource = source === "traces" || source === "spans";
  const retainedQueryKey = ["trace-attribute-retained", projectId, source];
  const exactQueryKey = [
    "trace-attribute-exact",
    projectId,
    source,
    debouncedSearch,
  ];
  const retainedRetryIdentity = JSON.stringify([projectId, source]);
  const exactRetryIdentity = JSON.stringify([
    projectId,
    source,
    debouncedSearch,
  ]);
  const [cursorRetryState, setCursorRetryState] = useState({
    retained: null,
    exact: null,
  });

  const retainedQuery = useInfiniteQuery({
    // Attribute names describe the retained project schema. Task/dashboard
    // row filters and scheduling windows deliberately do not participate in
    // this cache key or request. Search is supplemental, so typing never
    // discards cursor progress through the retained catalog.
    queryKey: retainedQueryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        signal,
        requestPage: (cursor) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: 10,
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data }) => data || {}),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: enabled && supportedSource && Boolean(projectId),
    retry: false,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    // The picker owns a concise retry state; never let the global handler
    // display backend exception text to the customer.
    meta: { errorHandled: true },
  });

  const exactQuery = useInfiniteQuery({
    // The exact cursor uses the indexed typed-Map lane first, then continues
    // the same bounded retained-data walk for JSON-only keys. It supplements
    // the stable catalog so partial text still filters already loaded names.
    queryKey: exactQueryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        signal,
        requestPage: (cursor) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: 10,
                q: debouncedSearch,
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data }) => data || {}),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled:
      enabled &&
      supportedSource &&
      Boolean(projectId) &&
      Boolean(debouncedSearch),
    retry: false,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
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
  // During an exact lookup, prefer its typed row over a duplicate from an
  // earlier generic catalog page. The exact row may be the only one carrying
  // authoritative mixed/structured type metadata needed by filter controls.
  const pages = debouncedSearch
    ? [...exactPages, ...retainedPages]
    : retainedPages;
  const seenKeys = new Set();
  const properties = pages.flatMap((page) =>
    (Array.isArray(page?.result) ? page.result : []).flatMap(
      ({ key, type, types, types_exact: typesExact }) => {
        if (!key || seenKeys.has(key)) return [];
        seenKeys.add(key);
        return [
          {
            id: key,
            name: key,
            category: "attribute",
            rawCategory: "custom_attribute",
            type,
            attributeTypes:
              Array.isArray(types) && types.length > 0 ? types : [type],
            // Key discovery is deliberately bounded. Even a positive exact-key
            // lookup proves existence, not that the first observed storage type
            // is the only type in the full window. Consumers may pin a typed
            // value query only when the server explicitly certifies coverage.
            attributeTypesExact: typesExact === true,
            apiColType: "SPAN_ATTRIBUTE",
          },
        ];
      },
    ),
  );
  const pageReadStates = [
    ...retainedPages.map((page) => getAttributeKeyPageReadState(page)),
    ...exactPages.map((page) =>
      getAttributeKeyPageReadState(page, { exact: true }),
    ),
  ];
  const queryIsError =
    retainedQuery.isError || (Boolean(debouncedSearch) && exactQuery.isError);
  const queryReadState = queryIsError
    ? "error"
    : retainedCursorStopped || exactCursorStopped
      ? "degraded"
      : pageReadStates.includes("degraded")
        ? "degraded"
        : pageReadStates.includes("sampled")
          ? "sampled"
          : "complete";
  const retainedLastPage = retainedPages.at(-1);
  const exactLastPage = exactPages.at(-1);
  const exactSearchMatched = Boolean(
    debouncedSearch &&
      exactPages.some(
        (page) =>
          page?.exact_match === true ||
          (Array.isArray(page?.result) &&
            page.result.some(({ key }) => key === debouncedSearch)),
      ),
  );
  const browseStatus = debouncedSearch
    ? exactLastPage?.browse_status || retainedLastPage?.browse_status
    : retainedLastPage?.browse_status;
  const retainedHasNextPage =
    retainedQuery.hasNextPage || retainedStoppedRetryAvailable;
  const shouldAdvanceExact = Boolean(debouncedSearch) && !exactSearchMatched;
  const exactHasNextPage =
    shouldAdvanceExact &&
    (exactQuery.hasNextPage || exactStoppedRetryAvailable);
  // The base cursor remains cached for browse/partial-search continuation, but
  // a verified exact key is terminal for the current search. Advancing the
  // unrelated base catalog after that point produced a no-op Load more loop.
  const shouldAdvanceRetained = !exactSearchMatched;
  const hasNextPage =
    exactHasNextPage || (shouldAdvanceRetained && retainedHasNextPage);
  const fetchNextPage = (...args) => {
    const reads = [];
    if (shouldAdvanceRetained && retainedStoppedRetryAvailable) {
      setCursorRetryState((current) => ({
        ...current,
        retained: {
          identity: retainedRetryIdentity,
          signature: retainedStopSignature,
        },
      }));
      reads.push(retainedQuery.refetch(...args));
    } else if (shouldAdvanceRetained && retainedQuery.hasNextPage) {
      reads.push(retainedQuery.fetchNextPage(...args));
    }
    if (shouldAdvanceExact && exactStoppedRetryAvailable) {
      setCursorRetryState((current) => ({
        ...current,
        exact: {
          identity: exactRetryIdentity,
          signature: exactStopSignature,
        },
      }));
      reads.push(exactQuery.refetch(...args));
    } else if (shouldAdvanceExact && exactQuery.hasNextPage) {
      reads.push(exactQuery.fetchNextPage(...args));
    }
    return reads.length === 1 ? reads[0] : Promise.allSettled(reads);
  };
  const refetch = (...args) => {
    const reads = [retainedQuery.refetch(...args)];
    if (debouncedSearch) reads.push(exactQuery.refetch(...args));
    return Promise.allSettled(reads);
  };

  return {
    data: properties,
    queryReadState,
    browseStatus,
    browseLimit: retainedLastPage?.browse_limit,
    browseLimitReached: browseStatus === "limit_reached" && !hasNextPage,
    // This is intentionally raw-key/backend identity, not the picker's fuzzy
    // punctuation-normalized match. Consumers may use it to terminate search
    // pagination without conflating keys such as `trace_id` and `trace.id`.
    exactSearchMatched,
    debouncedSearch,
    isFetching:
      retainedQuery.isFetching ||
      (Boolean(debouncedSearch) && exactQuery.isFetching),
    isLoading:
      retainedQuery.isLoading ||
      (Boolean(debouncedSearch) && exactQuery.isLoading),
    isError: queryIsError,
    isSuccess:
      retainedQuery.isSuccess && (!debouncedSearch || exactQuery.isSuccess),
    error: exactQuery.error || retainedQuery.error,
    hasNextPage,
    // One scroll advances both independent cursors until an exact key is
    // verified. A long-running/absent exact lookup therefore cannot starve
    // discovery of older catalog keys that match partial text locally.
    fetchNextPage,
    refetch,
    isFetchingNextPage:
      (shouldAdvanceRetained && retainedQuery.isFetchingNextPage) ||
      (shouldAdvanceExact && exactQuery.isFetchingNextPage) ||
      (shouldAdvanceRetained &&
        retainedCursorStopped &&
        retainedQuery.isFetching) ||
      (shouldAdvanceExact && exactCursorStopped && exactQuery.isFetching),
    isFetchNextPageError:
      (shouldAdvanceRetained && retainedQuery.isFetchNextPageError) ||
      (shouldAdvanceExact && exactQuery.isFetchNextPageError) ||
      (shouldAdvanceRetained && retainedStoppedRetryAvailable) ||
      (shouldAdvanceExact && exactStoppedRetryAvailable),
    cursorRetryExhausted:
      (shouldAdvanceRetained &&
        retainedCursorStopped &&
        retainedStopRetryAttempted) ||
      (shouldAdvanceExact && exactCursorStopped && exactStopRetryAttempted),
    // Consumers use this completion revision to unlock exactly one new
    // scroll-to-load action even when a valid continuation page contains no
    // previously unseen keys and therefore leaves `data.length` unchanged.
    pageCount: retainedPages.length + exactPages.length,
  };
}
