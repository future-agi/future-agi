import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import axios, { endpoints } from "src/utils/axios";
import { useCursorAttributeInventory } from "src/sections/projects/LLMTracing/useCursorAttributeInventory";
import {
  PROPERTY_CATALOG_CACHE_TIME_MS,
  PROPERTY_CATALOG_PAGE_SIZE,
  PROPERTY_CATALOG_STALE_TIME_MS,
} from "src/config/runtime_limits";
import {
  ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  compactAttributeKeyRetryPage,
  getNextAttributeKeyPageParam,
  isAttributeKeyCursorChainStopped,
  readAttributeKeyPage,
} from "src/sections/projects/LLMTracing/attributeKeyCursorPagination";

/** Rollout-only retained span-key adapter kept for compatibility coverage. */
export const useLegacyRunInsightAttributeKeys = (projectId) => {
  const queryClient = useQueryClient();
  const queryKey = ["run-insights-span-attribute-keys", projectId];
  const queryIdentity = JSON.stringify(queryKey);
  const freshChainRetryRef = useRef(null);
  const [freshChainRetrying, setFreshChainRetrying] = useState(false);
  const requestPage = (cursor, signal) =>
    axios
      .get(endpoints.project.spanAttributeKeys(), {
        signal,
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
        params: {
          project_id: projectId,
          page_size: PROPERTY_CATALOG_PAGE_SIZE,
          ...(cursor ? { cursor } : {}),
        },
      })
      .then(({ data }) => data || {});
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        pageSize: PROPERTY_CATALOG_PAGE_SIZE,
        publishedData: queryClient.getQueryData(queryKey),
        signal,
        requestPage: (cursor, requestSignal = signal) =>
          requestPage(cursor, requestSignal),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: Boolean(projectId),
    retry: false,
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_CACHE_TIME_MS,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    meta: { errorHandled: true },
  });

  useEffect(
    () => () => {
      const activeRequest = freshChainRetryRef.current;
      if (activeRequest?.identity === queryIdentity) {
        activeRequest.controller.abort();
        freshChainRetryRef.current = null;
      }
    },
    [queryIdentity],
  );

  const cursorChainStopped = isAttributeKeyCursorChainStopped(query.data);
  const retryCursorChain = () => {
    const activeRequest = freshChainRetryRef.current;
    if (activeRequest?.identity === queryIdentity) return activeRequest.promise;

    const controller = new AbortController();
    setFreshChainRetrying(true);
    const request = (async () => {
      await queryClient.cancelQueries({ queryKey, exact: true });
      const previousData = queryClient.getQueryData(queryKey);
      const freshPage = await readAttributeKeyPage({
        pageParam: null,
        pageSize: PROPERTY_CATALOG_PAGE_SIZE,
        publishedData: undefined,
        signal: controller.signal,
        requestPage: (cursor, signal = controller.signal) =>
          requestPage(cursor, signal),
      });
      const compactedPage = compactAttributeKeyRetryPage(
        previousData,
        freshPage,
      );
      // Keep already exposed fields available to ComplexFilter while replacing
      // the stopped transport history with exactly one cursorless page.
      queryClient.setQueryData(queryKey, {
        pages: [compactedPage],
        pageParams: [null],
      });
      return compactedPage;
    })();
    const trackedRequest = {
      identity: queryIdentity,
      controller,
      promise: null,
    };
    const settledPromise = request.finally(() => {
      if (freshChainRetryRef.current === trackedRequest) {
        freshChainRetryRef.current = null;
        setFreshChainRetrying(false);
      }
    });
    trackedRequest.promise = settledPromise;
    freshChainRetryRef.current = trackedRequest;
    return settledPromise;
  };
  const attributeKeys = useMemo(() => {
    const seenKeys = new Set();
    return (query.data?.pages || []).flatMap((page) =>
      (Array.isArray(page?.result) ? page.result : []).filter(({ key }) => {
        if (!key || seenKeys.has(key)) return false;
        seenKeys.add(key);
        return true;
      }),
    );
  }, [query.data?.pages]);

  return {
    ...query,
    attributeKeys,
    cursorChainStopped,
    retryCursorChain,
    isRetryingCursorChain: freshChainRetrying,
  };
};

export const useRunInsightAttributeKeys = (projectId) => {
  const inventory = useCursorAttributeInventory({
    projectId,
    rowType: "spans",
    discoveryMode: "filter",
    enabled: Boolean(projectId),
    pageSize: PROPERTY_CATALOG_PAGE_SIZE,
  });

  return {
    ...inventory,
    attributeKeys: inventory.rawAttributes,
    cursorChainStopped: inventory.cursorRetryExhausted,
    retryCursorChain: inventory.inventoryControlProps.onRetry,
    isRetryingCursorChain: inventory.isFetchingNextPage,
  };
};
