import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDebounce } from "src/hooks/use-debounce";
import {
  isPropertyCatalogNotReadyError,
  usePropertyCatalog,
} from "src/hooks/useDashboards";
import axios, { endpoints } from "src/utils/axios";
import {
  ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  compactAttributeKeyRetryPage,
  getAttributeKeyCursorStopSignature,
  getAttributeKeyNextCursor,
  getNextAttributeKeyPageParam,
  isAttributeKeyCursorChainStopped,
  readAttributeKeyPage,
} from "./attributeKeyCursorPagination";

const TRACE_PUBLIC_FIELDS = new Set([
  "input",
  "output",
  "name",
  "error",
  "tags",
  "metadata",
  "external_id",
]);

const SESSION_PUBLIC_FIELDS = new Set(["name", "bookmarked"]);

const SPAN_PUBLIC_FIELDS = new Set([
  "latency_ms",
  "prompt_tokens",
  "completion_tokens",
  "total_tokens",
  "cost",
  "response_time",
  "model",
  "name",
  "observation_type",
  "status",
  "status_message",
  "provider",
  "input",
  "output",
]);

const EMPTY_PAGES = Object.freeze([]);
const EMPTY_KEYS = Object.freeze([]);

const ROW_TYPE_ALIASES = Object.freeze({
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
});

export const normalizeAttributeInventoryRowType = (rowType) =>
  ROW_TYPE_ALIASES[String(rowType || "spans").toLowerCase()] || "spans";

export const attributeInventoryKey = (attribute) =>
  typeof attribute === "string" ? attribute : attribute?.key;

export function mergeCursorAttributeRows(attributes = []) {
  const merged = [];
  const indexByKey = new Map();
  for (const attribute of attributes) {
    const key = attributeInventoryKey(attribute);
    if (!key) continue;
    const existingIndex = indexByKey.get(key);
    if (existingIndex === undefined) {
      indexByKey.set(key, merged.length);
      merged.push(attribute);
      continue;
    }

    const existing = merged[existingIndex];
    const existingRow =
      typeof existing === "string" ? syntheticAttribute(existing) : existing;
    const incomingRow =
      typeof attribute === "string" ? syntheticAttribute(attribute) : attribute;
    const types = [
      existingRow.type,
      ...(existingRow.types || []),
      incomingRow.type,
      ...(incomingRow.types || []),
    ].filter(
      (valueType, index, values) =>
        valueType && values.indexOf(valueType) === index,
    );
    merged[existingIndex] = {
      ...existingRow,
      ...incomingRow,
      key,
      type: existingRow.type || incomingRow.type || types[0] || "string",
      types,
      // Two independently bounded occurrences cannot certify that no third
      // storage family exists elsewhere in the retained workspace.
      types_exact: false,
    };
  }
  return merged;
}

const syntheticAttribute = (key) => ({
  key,
  type: "string",
  types: ["string"],
  types_exact: false,
});

const withExpandedKey = (attribute, key) =>
  typeof attribute === "string"
    ? syntheticAttribute(key)
    : { ...attribute, key };

const representativePathGrammar = (rowType) => {
  if (rowType === "traces") {
    return {
      staticPaths: [
        ...TRACE_PUBLIC_FIELDS,
        ...[...SPAN_PUBLIC_FIELDS].map((field) => `spans.0.${field}`),
      ],
      rawPrefix: "spans.0.",
    };
  }
  if (rowType === "sessions") {
    return {
      staticPaths: [
        ...SESSION_PUBLIC_FIELDS,
        ...[...TRACE_PUBLIC_FIELDS].map((field) => `traces.0.${field}`),
        ...[...SPAN_PUBLIC_FIELDS].map((field) => `traces.0.spans.0.${field}`),
      ],
      rawPrefix: "traces.0.spans.0.",
    };
  }
  return { staticPaths: [], rawPrefix: "" };
};

export const requestedAttributePathPrefix = (search, rowType) => {
  const normalizedRowType = normalizeAttributeInventoryRowType(rowType);
  const value = String(search || "").trim();
  if (normalizedRowType === "traces") {
    return value.match(/^(spans\.\d+\.)/u)?.[1] || "";
  }
  if (normalizedRowType === "sessions") {
    return value.match(/^(traces\.\d+\.spans\.\d+\.)/u)?.[1] || "";
  }
  return "";
};

export const rawAttributeSearchFromPath = (search, rowType) => {
  const normalizedRowType = normalizeAttributeInventoryRowType(rowType);
  const value = String(search || "").trim();
  if (!value) return "";
  const prefix = requestedAttributePathPrefix(value, normalizedRowType);
  return prefix ? value.slice(prefix.length) : value;
};

/**
 * Combine cursor-backed raw keys with constant-size representative paths.
 *
 * Index zero is a browse suggestion, not a data limit. A typed numeric path
 * prefix is synthesized directly from exact/retained raw keys, so arbitrary
 * valid nonzero paths stay reachable without enumerating cardinalities.
 */
export function expandCursorAttributeInventory({
  rawAttributes = [],
  rowType = "spans",
  preservedKeys = [],
  search = "",
}) {
  const normalizedRowType = normalizeAttributeInventoryRowType(rowType);
  const { staticPaths, rawPrefix } =
    representativePathGrammar(normalizedRowType);
  const requestedPrefix = requestedAttributePathPrefix(
    search,
    normalizedRowType,
  );
  const output = [];
  const seen = new Set();

  const append = (value) => {
    const key = attributeInventoryKey(value);
    if (!key || seen.has(key)) return;
    seen.add(key);
    output.push(value);
  };

  for (const path of staticPaths) append(path);
  if (requestedPrefix) {
    for (const field of SPAN_PUBLIC_FIELDS) {
      append(`${requestedPrefix}${field}`);
    }
  }

  for (const attribute of rawAttributes || []) {
    const key = attributeInventoryKey(attribute);
    if (!key) continue;
    append(withExpandedKey(attribute, `${rawPrefix}${key}`));
    if (requestedPrefix && requestedPrefix !== rawPrefix) {
      append(withExpandedKey(attribute, `${requestedPrefix}${key}`));
    }
  }

  // A saved key may not be present in the cursor pages loaded so far. Keep it
  // visible and cursor-value-enabled; a later enriched cursor row replaces it
  // through the same key identity.
  for (const key of preservedKeys || []) {
    if (typeof key === "string" && key) append(syntheticAttribute(key));
  }
  return output;
}

const exactSearchMatched = (pages, rawSearch) =>
  Boolean(
    rawSearch &&
      (pages || []).some(
        (page) =>
          page?.exact_match === true ||
          (Array.isArray(page?.result) &&
            page.result.some(({ key }) => key === rawSearch)),
      ),
  );

/** Rollout-only adapter for the retained span-key endpoint. */
export function useLegacyCursorAttributeInventory({
  projectId,
  workspaceScope = false,
  workspaceScopeKey = "",
  rowType = "spans",
  discoveryMode = "filter",
  search = "",
  preservedKeys = EMPTY_KEYS,
  enabled = true,
  pageSize = 50,
}) {
  const queryClient = useQueryClient();
  const workspaceScoped = workspaceScope === true;
  const scopeIdentity = workspaceScoped
    ? `workspace:${workspaceScopeKey || ""}`
    : `project:${projectId || ""}`;
  const scopeReady = workspaceScoped
    ? Boolean(workspaceScopeKey)
    : Boolean(projectId);
  const normalizedRowType = normalizeAttributeInventoryRowType(rowType);
  const normalizedSearch = String(search || "").trim();
  const rawSearch = rawAttributeSearchFromPath(
    normalizedSearch,
    normalizedRowType,
  );
  const debouncedRawSearch = useDebounce(rawSearch, 350);
  const retainedQueryKey = [
    "cursor-attribute-inventory",
    "retained",
    scopeIdentity,
    discoveryMode,
    pageSize,
  ];
  const exactQueryKey = [
    "cursor-attribute-inventory",
    "exact",
    scopeIdentity,
    discoveryMode,
    debouncedRawSearch,
    pageSize,
  ];
  const retainedRetryIdentity = JSON.stringify([
    scopeIdentity,
    discoveryMode,
    pageSize,
  ]);
  const exactRetryIdentity = JSON.stringify([
    scopeIdentity,
    discoveryMode,
    debouncedRawSearch,
    pageSize,
  ]);
  const exactRetryGestureIdentity = JSON.stringify([
    scopeIdentity,
    discoveryMode,
    rawSearch,
    pageSize,
  ]);
  const [cursorRetryState, setCursorRetryState] = useState({
    retained: null,
    exact: null,
  });
  const freshChainRequestRef = useRef(null);
  const [freshChainRecoveryLane, setFreshChainRecoveryLane] = useState(null);
  const previousRetainedRetryIdentityRef = useRef(retainedRetryIdentity);
  const exactSearchGestureStateRef = useRef({
    scope: null,
    previous: null,
    pendingRetry: null,
  });
  const failedExactContinuationIdentityRef = useRef(null);

  useEffect(() => {
    const retainedChanged =
      previousRetainedRetryIdentityRef.current !== retainedRetryIdentity;
    previousRetainedRetryIdentityRef.current = retainedRetryIdentity;
    setCursorRetryState((current) => {
      const exactChanged =
        current.exact &&
        current.exact.gestureIdentity !== exactRetryGestureIdentity;
      if (!retainedChanged && !exactChanged) return current;
      return {
        retained: retainedChanged ? null : current.retained,
        exact: exactChanged ? null : current.exact,
      };
    });
  }, [exactRetryGestureIdentity, retainedRetryIdentity]);

  const requestKeyPage = ({ queryKey, cursor, signal, exact }) =>
    readAttributeKeyPage({
      pageParam: cursor,
      pageSize,
      publishedData: queryClient.getQueryData(queryKey),
      signal,
      requestPage: (nextCursor, requestSignal = signal) =>
        axios
          .get(endpoints.project.spanAttributeKeys(), {
            signal: requestSignal,
            timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
            params: {
              ...(workspaceScoped
                ? { workspace_scope: true }
                : { project_id: projectId }),
              page_size: pageSize,
              discovery_mode: discoveryMode,
              ...(exact ? { q: debouncedRawSearch } : {}),
              ...(nextCursor ? { cursor: nextCursor } : {}),
            },
          })
          .then(({ data }) => data || {}),
      dedupeByType: workspaceScoped,
    });

  const retainedQuery = useInfiniteQuery({
    queryKey: retainedQueryKey,
    queryFn: ({ signal, pageParam }) =>
      requestKeyPage({
        queryKey: retainedQueryKey,
        cursor: pageParam,
        signal,
        exact: false,
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: enabled && scopeReady,
    retry: false,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    meta: { errorHandled: true },
  });

  const exactQuery = useInfiniteQuery({
    queryKey: exactQueryKey,
    queryFn: ({ signal, pageParam }) =>
      requestKeyPage({
        queryKey: exactQueryKey,
        cursor: pageParam,
        signal,
        exact: true,
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: enabled && scopeReady && Boolean(debouncedRawSearch),
    retry: false,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    meta: { errorHandled: true },
  });

  useEffect(() => {
    const scope = JSON.stringify([scopeIdentity, discoveryMode, pageSize]);
    const identity = JSON.stringify([
      scopeIdentity,
      discoveryMode,
      rawSearch,
      pageSize,
    ]);
    const state = exactSearchGestureStateRef.current;
    if (state.scope !== scope) {
      state.scope = scope;
      state.previous = null;
      state.pendingRetry = null;
    }
    if (state.previous === identity) return;
    state.previous = identity;
    if (!enabled || !rawSearch) {
      state.pendingRetry = null;
      return;
    }
    state.pendingRetry = identity;
  }, [discoveryMode, enabled, pageSize, rawSearch, scopeIdentity]);

  const retainedPages = retainedQuery.data?.pages || EMPTY_PAGES;
  const exactPages = exactQuery.data?.pages || EMPTY_PAGES;
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
  const matched = exactSearchMatched(exactPages, debouncedRawSearch);
  const retainedHasNextPage =
    Boolean(retainedQuery.hasNextPage) || retainedStoppedRetryAvailable;
  // The exact-q chain is supplemental. A failed continuation retains already
  // published exact rows but immediately yields subsequent gestures to the
  // authoritative retained catalog.
  const exactContinuationFailed = Boolean(
    debouncedRawSearch && exactQuery.isFetchNextPageError,
  );
  useEffect(() => {
    if (
      exactContinuationFailed &&
      getAttributeKeyNextCursor(exactPages.at(-1))
    ) {
      failedExactContinuationIdentityRef.current = exactRetryIdentity;
    }
  }, [exactContinuationFailed, exactPages, exactRetryIdentity]);
  const shouldAdvanceExact =
    Boolean(debouncedRawSearch) &&
    !exactContinuationFailed &&
    (!matched || (workspaceScoped && Boolean(exactQuery.hasNextPage)));
  const exactHasNextPage =
    shouldAdvanceExact &&
    (Boolean(exactQuery.hasNextPage) || exactStoppedRetryAvailable);
  const shouldAdvanceRetained = !debouncedRawSearch || !exactHasNextPage;
  const hasNextPage =
    exactHasNextPage || (shouldAdvanceRetained && retainedHasNextPage);
  const continuationKey = exactHasNextPage
    ? `exact:${
        getAttributeKeyNextCursor(exactPages.at(-1)) ||
        exactStopSignature ||
        "fresh-chain-retry"
      }`
    : shouldAdvanceRetained && retainedHasNextPage
      ? `retained:${
          getAttributeKeyNextCursor(retainedPages.at(-1)) ||
          retainedStopSignature ||
          "fresh-chain-retry"
        }`
      : null;

  const rawAttributes = useMemo(() => {
    return mergeCursorAttributeRows(
      [...exactPages, ...retainedPages].flatMap((page) =>
        Array.isArray(page?.result) ? page.result : [],
      ),
    );
  }, [exactPages, retainedPages]);

  const attributes = useMemo(
    () =>
      expandCursorAttributeInventory({
        rawAttributes,
        rowType: normalizedRowType,
        preservedKeys,
        search: normalizedSearch,
      }),
    [normalizedRowType, normalizedSearch, preservedKeys, rawAttributes],
  );

  const normalizedLocalSearch = normalizedSearch.toLocaleLowerCase();
  const filteredAttributes = useMemo(
    () =>
      normalizedLocalSearch
        ? attributes.filter((attribute) =>
            attributeInventoryKey(attribute)
              ?.toLocaleLowerCase()
              .includes(normalizedLocalSearch),
          )
        : attributes,
    [attributes, normalizedLocalSearch],
  );

  const fetchFreshChainPage = ({ lane, queryKey, exact, retryState }) => {
    if (freshChainRequestRef.current) return freshChainRequestRef.current;

    setFreshChainRecoveryLane(lane);
    const request = requestKeyPage({
      queryKey,
      cursor: null,
      signal: undefined,
      exact,
    }).then((page) => {
      // Replace the stopped transport chain only after its one fresh page has
      // completed. Calling an infinite-query refetch here would replay every
      // cached page sequentially; one explicit recovery gesture must perform
      // only one bounded page request.
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
    });
    const settledRequest = request.finally(() => {
      if (freshChainRequestRef.current === settledRequest) {
        freshChainRequestRef.current = null;
        setFreshChainRecoveryLane(null);
      }
    });
    freshChainRequestRef.current = settledRequest;
    return settledRequest;
  };

  useEffect(() => {
    const identity = JSON.stringify([
      scopeIdentity,
      discoveryMode,
      rawSearch,
      pageSize,
    ]);
    const state = exactSearchGestureStateRef.current;
    if (
      state.pendingRetry !== identity ||
      !rawSearch ||
      debouncedRawSearch !== rawSearch
    ) {
      return;
    }
    if (failedExactContinuationIdentityRef.current === exactRetryIdentity) {
      failedExactContinuationIdentityRef.current = null;
      state.pendingRetry = null;
      void exactQuery.fetchNextPage();
      return;
    }
    if (exactQuery.isFetching) {
      state.pendingRetry = null;
      return;
    }
    const continuationFailed =
      exactQuery.isFetchNextPageError &&
      Boolean(getAttributeKeyNextCursor(exactPages.at(-1)));
    const cachedReadFailed = exactQuery.isError || exactQuery.isRefetchError;
    if (!continuationFailed && !cachedReadFailed) {
      state.pendingRetry = null;
      return;
    }
    state.pendingRetry = null;
    if (continuationFailed) void exactQuery.fetchNextPage();
    else {
      // Re-entering a cached failed exact search must not invoke the infinite
      // query's N-page replay. Recover with one cursorless compacting read.
      void fetchFreshChainPage({
        lane: "exact",
        queryKey: exactQueryKey,
        exact: true,
        retryState: cursorRetryState.exact,
      }).catch(() => undefined);
    }
  }, [
    cursorRetryState.exact,
    debouncedRawSearch,
    discoveryMode,
    exactPages,
    exactQuery,
    pageSize,
    rawSearch,
    scopeIdentity,
  ]);

  const fetchNextPage = (...args) => {
    if (shouldAdvanceExact && exactStoppedRetryAvailable) {
      return fetchFreshChainPage({
        lane: "exact",
        queryKey: exactQueryKey,
        exact: true,
        retryState: {
          identity: exactRetryIdentity,
          gestureIdentity: exactRetryGestureIdentity,
          signature: exactStopSignature,
        },
      });
    }
    if (shouldAdvanceExact && exactQuery.hasNextPage) {
      return exactQuery.fetchNextPage(...args);
    }
    if (shouldAdvanceRetained && retainedStoppedRetryAvailable) {
      return fetchFreshChainPage({
        lane: "retained",
        queryKey: retainedQueryKey,
        exact: false,
        retryState: {
          identity: retainedRetryIdentity,
          signature: retainedStopSignature,
        },
      });
    }
    if (shouldAdvanceRetained && retainedQuery.hasNextPage) {
      return retainedQuery.fetchNextPage(...args);
    }
    return Promise.resolve();
  };

  const isFetchingNextPage =
    freshChainRecoveryLane !== null ||
    (exactHasNextPage && exactQuery.isFetchingNextPage) ||
    (shouldAdvanceRetained && retainedQuery.isFetchingNextPage) ||
    (shouldAdvanceExact && exactCursorStopped && exactQuery.isFetching) ||
    (shouldAdvanceRetained &&
      retainedCursorStopped &&
      retainedQuery.isFetching);
  const retainedInitialError =
    retainedQuery.isError && retainedPages.length === 0;
  const exactInitialError = Boolean(
    debouncedRawSearch && exactQuery.isError && exactPages.length === 0,
  );
  const isFetchNextPageError =
    (shouldAdvanceRetained && retainedQuery.isFetchNextPageError) ||
    exactContinuationFailed ||
    (shouldAdvanceRetained && retainedStoppedRetryAvailable) ||
    (shouldAdvanceExact && exactStoppedRetryAvailable);
  const cursorRetryExhausted =
    (shouldAdvanceRetained &&
      retainedCursorStopped &&
      retainedStopRetryAttempted) ||
    (shouldAdvanceExact && exactCursorStopped && exactStopRetryAttempted);
  const canRetryInventory = retainedInitialError || exactInitialError;
  const retryInventory = () => {
    if (retainedInitialError) return retainedQuery.refetch();
    if (exactInitialError) return exactQuery.refetch();
    return Promise.resolve();
  };

  return {
    attributes,
    filteredAttributes,
    rawAttributes,
    hasNextPage,
    continuationKey,
    fetchNextPage,
    isFetchingNextPage,
    isLoading: retainedQuery.isLoading,
    isFetching: retainedQuery.isFetching || exactQuery.isFetching,
    isError: retainedQuery.isError,
    error: retainedQuery.error,
    exactSearchMatched: matched,
    debouncedSearch: debouncedRawSearch,
    isFetchNextPageError,
    cursorRetryExhausted,
    pageCount: retainedPages.length + exactPages.length,
    inventoryControlProps: {
      hasNextPage,
      continuationKey,
      onLoadMore: fetchNextPage,
      isFetchingNextPage,
      isError: retainedInitialError,
      isExactSearchError: exactInitialError,
      isExactSearchDegraded: exactContinuationFailed,
      isFetchNextPageError,
      cursorRetryExhausted,
      canRetry: canRetryInventory,
      onRetry: retryInventory,
    },
  };
}

const propertyMetricToRawAttribute = (metric) => {
  const type = metric?.type || metric?.data_type || "string";
  const declaredTypes = metric?.attribute_types || metric?.attributeTypes;
  const types = Array.isArray(declaredTypes)
    ? declaredTypes.filter((valueType) => typeof valueType === "string")
    : [];
  return {
    key: metric?.name,
    property_id: metric?.property_id,
    type,
    types: types.length > 0 ? types : [type],
    types_exact:
      metric?.attribute_types_exact === true ||
      metric?.attributeTypesExact === true,
  };
};

/**
 * Unified property-definition inventory. The legacy span-key reader is kept
 * above as an explicit rollout adapter and is enabled only after the backend
 * returns the typed 503 `property_catalog_not_ready` response.
 */
export function useCursorAttributeInventory({
  projectId,
  workspaceScope = false,
  workspaceScopeKey = "",
  rowType = "spans",
  discoveryMode = "filter",
  search = "",
  preservedKeys = EMPTY_KEYS,
  enabled = true,
  pageSize = 50,
  cacheScopeKey = "",
}) {
  const workspaceScoped = workspaceScope === true;
  const scopeIdentity = workspaceScoped
    ? `workspace:${workspaceScopeKey || ""}`
    : `project:${projectId || ""}`;
  const scopeReady = workspaceScoped
    ? Boolean(workspaceScopeKey)
    : Boolean(projectId);
  const normalizedRowType = normalizeAttributeInventoryRowType(rowType);
  const normalizedSearch = String(search || "").trim();
  const rawSearch = rawAttributeSearchFromPath(
    normalizedSearch,
    normalizedRowType,
  );
  const debouncedRawSearch = useDebounce(rawSearch, 350);
  const fallbackScopeKey = JSON.stringify([scopeIdentity, "custom_attribute"]);
  const catalog = usePropertyCatalog({
    category: "custom_attribute",
    source: "traces",
    search: debouncedRawSearch,
    projectIds: workspaceScoped || !projectId ? [] : [projectId],
    pageSize,
    enabled: enabled && scopeReady,
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey,
    cacheScopeKey,
  });
  const useLegacyFallback = catalog.legacyFallbackRequired;
  const legacy = useLegacyCursorAttributeInventory({
    projectId,
    workspaceScope,
    workspaceScopeKey,
    rowType,
    discoveryMode,
    search,
    preservedKeys,
    enabled: enabled && useLegacyFallback,
    pageSize,
  });

  if (useLegacyFallback) return legacy;

  const rawAttributes = mergeCursorAttributeRows(
    catalog.metrics.map(propertyMetricToRawAttribute),
  );
  const attributes = expandCursorAttributeInventory({
    rawAttributes,
    rowType: normalizedRowType,
    preservedKeys,
    search: normalizedSearch,
  });
  const normalizedLocalSearch = normalizedSearch.toLocaleLowerCase();
  const filteredAttributes = normalizedLocalSearch
    ? attributes.filter((attribute) =>
        attributeInventoryKey(attribute)
          ?.toLocaleLowerCase()
          .includes(normalizedLocalSearch),
      )
    : attributes;
  const catalogNotReady = isPropertyCatalogNotReadyError(catalog.error);
  const initialError = Boolean(catalog.isError && !catalogNotReady);
  const nextPageError = Boolean(
    catalog.isFetchNextPageError || catalog.cursorChainStopped,
  );
  const exactSearchMatched = Boolean(
    debouncedRawSearch &&
      rawAttributes.some(({ key }) => key === debouncedRawSearch),
  );
  const fetchNextPage = (...args) =>
    catalog.hasNextPage ? catalog.fetchNextPage(...args) : Promise.resolve();
  const retryInventory = () =>
    initialError ? catalog.refetch() : Promise.resolve();
  const isLoading = catalog.isLoading || catalogNotReady;

  return {
    attributes,
    filteredAttributes,
    rawAttributes,
    hasNextPage: Boolean(catalog.hasNextPage),
    continuationKey: catalog.continuationKey,
    fetchNextPage,
    isFetchingNextPage: catalog.isFetchingNextPage,
    isLoading,
    isFetching: catalog.isFetching,
    isError: initialError,
    error: initialError ? catalog.error : null,
    exactSearchMatched,
    debouncedSearch: debouncedRawSearch,
    isFetchNextPageError: nextPageError,
    cursorRetryExhausted: catalog.cursorChainStopped,
    pageCount: catalog.data?.pages?.length || 0,
    inventoryControlProps: {
      hasNextPage: Boolean(catalog.hasNextPage),
      continuationKey: catalog.continuationKey,
      onLoadMore: fetchNextPage,
      isFetchingNextPage: catalog.isFetchingNextPage,
      isError: initialError,
      isExactSearchError: false,
      isExactSearchDegraded: false,
      isFetchNextPageError: nextPageError,
      cursorRetryExhausted: catalog.cursorChainStopped,
      canRetry: initialError,
      onRetry: retryInventory,
    },
  };
}
