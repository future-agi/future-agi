import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Typography,
  CircularProgress,
  Button,
} from "@mui/material";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { LoadingScreen } from "src/components/loading-screen";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router-dom";
import { useDebounce } from "src/hooks/use-debounce";
import {
  isPropertyCatalogNotReadyError,
  usePropertyCatalog,
} from "src/hooks/useDashboards";
import AttributeGroupList from "./AttributeGroupList";
import AttributeKeyList from "./AttributeKeyList";
import AttributeDetail from "./AttributeDetail";
import {
  ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  compactAttributeKeyRetryPage,
  getAttributeKeyCursorStopSignature,
  getNextAttributeKeyPageParam,
  isAttributeKeyCursorChainStopped,
  readAttributeKeyPage,
} from "src/sections/projects/LLMTracing/attributeKeyCursorPagination";
import {
  ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  PROPERTY_CATALOG_CACHE_TIME_MS,
  PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
  PROPERTY_CATALOG_STALE_TIME_MS,
} from "src/config/runtime_limits";

/** Rollout-only retained span-key view selected by the typed not-ready 503. */
export const LegacyAttributesView = () => {
  const { id: projectId } = useParams();
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(
    search.trim(),
    ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  );
  const queryClient = useQueryClient();
  const retainedQueryKey = ["span-attribute-keys", projectId, "retained"];
  const exactQueryKey = [
    "span-attribute-keys",
    projectId,
    "exact",
    debouncedSearch,
  ];
  const retainedRetryIdentity = JSON.stringify([projectId, "retained"]);
  const exactRetryIdentity = JSON.stringify([
    projectId,
    "exact",
    debouncedSearch,
  ]);
  const exactRetryGestureIdentity = JSON.stringify([
    projectId,
    "exact",
    search.trim(),
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

  // Scope one-shot cursor recovery to the current project/search identity.
  // Returning to an older cached query after visiting another identity must
  // receive a fresh bounded retry rather than inherit the earlier terminal
  // marker.
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

  const {
    data: retainedData,
    isLoading: isLoadingRetained,
    isError: isRetainedError,
    isFetching: isFetchingRetained,
    hasNextPage: retainedHasNextPage,
    fetchNextPage: fetchNextRetainedPage,
    isFetchingNextPage: isFetchingNextRetainedPage,
  } = useInfiniteQuery({
    // Retained discovery is authoritative and independent of search text.
    // Keeping this cursor cached means an exact lookup cannot discard progress
    // through older project attributes.
    queryKey: retainedQueryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        pageSize: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
        publishedData: queryClient.getQueryData(retainedQueryKey),
        signal,
        requestPage: (cursor, requestSignal = signal) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal: requestSignal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data: page }) => page || {}),
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

  const {
    data: exactData,
    hasNextPage: exactQueryHasNextPage,
    fetchNextPage: fetchNextExactPage,
    isFetching: isFetchingExact,
    isFetchingNextPage: isFetchingNextExactPage,
    isFetchNextPageError: isExactFetchNextPageError,
  } = useInfiniteQuery({
    // Exact-q is a supplemental accelerator. It stops as soon as the requested
    // identity is proven, while the no-q retained cursor remains available for
    // prefix and substring siblings on later pages.
    queryKey: exactQueryKey,
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        pageSize: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
        publishedData: queryClient.getQueryData(exactQueryKey),
        signal,
        requestPage: (cursor, requestSignal = signal) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal: requestSignal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
                q: debouncedSearch,
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data: page }) => page || {}),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: Boolean(projectId) && Boolean(debouncedSearch),
    retry: false,
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_CACHE_TIME_MS,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    // Failure of this optional probe must not hide retained attributes or
    // expose backend details through the global error handler.
    meta: { errorHandled: true },
  });

  const retainedPages = retainedData?.pages || [];
  const exactPages = exactData?.pages || [];
  const retainedCursorStopped = isAttributeKeyCursorChainStopped(retainedData);
  const exactCursorStopped = isAttributeKeyCursorChainStopped(exactData);
  const retainedStopSignature =
    getAttributeKeyCursorStopSignature(retainedData);
  const exactStopSignature = getAttributeKeyCursorStopSignature(exactData);
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
  const exactSearchMatched = Boolean(
    debouncedSearch &&
      exactPages.some(
        (page) =>
          page?.exact_match === true ||
          (Array.isArray(page?.result) &&
            page.result.some(({ key }) => key === debouncedSearch)),
      ),
  );
  // A failed exact continuation is non-authoritative. Keep its page-one rows,
  // demote that cursor, and let the next deliberate gesture advance the
  // independent retained catalog exactly once.
  const exactContinuationFailed = Boolean(
    debouncedSearch && isExactFetchNextPageError,
  );
  const shouldAdvanceExact =
    Boolean(debouncedSearch) && !exactSearchMatched && !exactContinuationFailed;
  const exactHasNextPage =
    shouldAdvanceExact && (exactQueryHasNextPage || exactStoppedRetryAvailable);
  const retainedCanAdvance =
    retainedHasNextPage || retainedStoppedRetryAvailable;
  const shouldAdvanceRetained = !debouncedSearch || !exactHasNextPage;
  const hasNextPage =
    exactHasNextPage ||
    (shouldAdvanceRetained && retainedCanAdvance && !retainedCursorStopped);

  const fetchFreshPage = async ({ queryKey, exact, lane, retryState }) => {
    if (freshRetryPending) return undefined;
    setFreshRetryPending(true);
    try {
      const page = await readAttributeKeyPage({
        pageParam: null,
        pageSize: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
        publishedData: undefined,
        requestPage: (cursor, signal) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
                ...(exact ? { q: debouncedSearch } : {}),
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data: pageData }) => pageData || {}),
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
    if (shouldAdvanceExact && exactStoppedRetryAvailable) {
      return fetchFreshPage({
        queryKey: exactQueryKey,
        exact: true,
        lane: "exact",
        retryState: {
          identity: exactRetryIdentity,
          signature: exactStopSignature,
        },
      });
    }
    if (exactHasNextPage) return fetchNextExactPage(...args);
    if (
      shouldAdvanceRetained &&
      retainedHasNextPage &&
      !retainedCursorStopped
    ) {
      return fetchNextRetainedPage(...args);
    }
    return Promise.resolve();
  };
  const retryStoppedRetainedCursor = () => {
    if (!retainedStoppedRetryAvailable) return Promise.resolve();
    return fetchFreshPage({
      queryKey: retainedQueryKey,
      exact: false,
      lane: "retained",
      retryState: {
        identity: retainedRetryIdentity,
        signature: retainedStopSignature,
      },
    }).catch(() => undefined);
  };
  // Generic refresh errors belong to the authoritative retained catalog.
  // One Retry performs one cursorless compacting request; it never replays a
  // cached infinite chain or fans out into the supplemental exact search.
  const refetch = () =>
    fetchFreshPage({
      queryKey: retainedQueryKey,
      exact: false,
      lane: "retained",
      retryState: cursorRetryState.retained,
    }).catch(() => undefined);
  const isLoading = isLoadingRetained;
  const isError = isRetainedError;
  const isFetching = isFetchingRetained;
  const isFetchingNextPage =
    (exactHasNextPage && isFetchingNextExactPage) ||
    freshRetryPending ||
    (shouldAdvanceRetained && isFetchingNextRetainedPage) ||
    (shouldAdvanceExact && exactCursorStopped && isFetchingExact);

  // Exact rows come first so their authoritative identity/type wins de-dupe.
  // Retained pages then supply partial siblings without any automatic drain.
  const pages = debouncedSearch
    ? [...exactPages, ...retainedPages]
    : retainedPages;
  const seenAttributeKeys = new Set();
  const discoveredAttributeKeys = pages.flatMap((page) =>
    (page?.result || []).filter(({ key }) => {
      if (!key || seenAttributeKeys.has(key)) return false;
      seenAttributeKeys.add(key);
      return true;
    }),
  );
  const normalizedSearch = debouncedSearch.toLocaleLowerCase();
  const attributeKeys = normalizedSearch
    ? discoveredAttributeKeys.filter(({ key }) =>
        key.toLocaleLowerCase().includes(normalizedSearch),
      )
    : discoveredAttributeKeys;

  // Group attributes by dot-delimited prefix
  const groups = useMemo(() => {
    const grouped = {};
    attributeKeys.forEach(({ key, type, count, count_exact: countExact }) => {
      const parts = key.split(".");
      const prefix = parts.length > 1 ? parts.slice(0, -1).join(".") : key;
      if (!grouped[prefix]) grouped[prefix] = { keys: [], totalCount: 0 };
      grouped[prefix].keys.push({ key, type, count, count_exact: countExact });
      if (countExact && Number.isFinite(count))
        grouped[prefix].totalCount += count;
    });
    return Object.entries(grouped)
      .map(([prefix, data]) => ({ prefix, ...data }))
      .sort(
        (a, b) =>
          b.totalCount - a.totalCount || a.prefix.localeCompare(b.prefix),
      );
  }, [attributeKeys]);

  const filteredKeys = useMemo(() => {
    if (debouncedSearch || !selectedGroup) return attributeKeys;
    return groups.find((g) => g.prefix === selectedGroup)?.keys || [];
  }, [debouncedSearch, selectedGroup, groups, attributeKeys]);

  if (isLoading) {
    return <LoadingScreen sx={{ height: "calc(100vh - 180px)" }} />;
  }

  if (isError && attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              Retry
            </Button>
          }
        >
          Span attributes could not be loaded. Please retry.
        </Alert>
      </Box>
    );
  }

  if (retainedCursorStopped && attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            retainedStoppedRetryAvailable ? (
              <Button
                size="small"
                disabled={isFetchingRetained}
                onClick={() => retryStoppedRetainedCursor()}
              >
                Retry pagination
              </Button>
            ) : null
          }
        >
          Attribute pagination stopped safely. Please retry.
        </Alert>
      </Box>
    );
  }

  if (attributeKeys.length === 0 && hasNextPage) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {isFetchingNextPage ? (
          <CircularProgress size={24} />
        ) : (
          <Button variant="outlined" onClick={() => fetchNextPage()}>
            Continue loading attributes
          </Button>
        )}
        <Typography variant="body2" color="text.secondary">
          Searching older traces for attributes…
        </Typography>
      </Box>
    );
  }

  if (attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Typography variant="h6" color="text.secondary">
          No Span Attributes Found
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Span attributes will appear here once trace data is ingested.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 180px)",
        overflow: "hidden",
      }}
    >
      {isError && (
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              Retry
            </Button>
          }
          sx={{ m: 1, mb: 0, flexShrink: 0 }}
        >
          Span attributes could not be refreshed. Existing attributes are still
          available.
        </Alert>
      )}
      {retainedCursorStopped && (
        <Alert
          severity="warning"
          action={
            retainedStoppedRetryAvailable ? (
              <Button
                size="small"
                disabled={isFetchingRetained}
                onClick={() => retryStoppedRetainedCursor()}
              >
                Retry pagination
              </Button>
            ) : null
          }
          sx={{ m: 1, mb: 0, flexShrink: 0 }}
        >
          Attribute pagination stopped safely. Existing attributes are still
          available.
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <AttributeGroupList
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <AttributeKeyList
          keys={filteredKeys}
          selectedKey={selectedKey}
          onSelectKey={setSelectedKey}
          hasMore={hasNextPage}
          isLoadingMore={isFetchingNextPage}
          onLoadMore={fetchNextPage}
          search={search}
          onSearchChange={setSearch}
        />
        <AttributeDetail projectId={projectId} attributeKey={selectedKey} />
      </Box>
    </Box>
  );
};

const AttributesView = () => {
  const { id: projectId } = useParams();
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(
    search.trim(),
    ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  );
  const catalog = usePropertyCatalog({
    category: "custom_attribute",
    source: "traces",
    search: debouncedSearch,
    projectIds: projectId ? [projectId] : [],
    pageSize: PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
    enabled: Boolean(projectId),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `journey-attribute-property-catalog:${projectId || ""}`,
  });

  const catalogNotReady = isPropertyCatalogNotReadyError(catalog.error);
  const isLoading = catalog.isLoading || catalogNotReady;
  const isError = Boolean(catalog.isError && !catalogNotReady);
  const isFetching = catalog.isFetching;
  const cursorStopped = catalog.cursorChainStopped;
  const hasNextPage = Boolean(catalog.hasNextPage);
  const isFetchingNextPage = catalog.isFetchingNextPage;
  const attributeKeys = useMemo(
    () =>
      catalog.metrics.map((metric) => ({
        key: metric.name,
        property_id: metric.property_id,
        type: metric.type || metric.data_type || "string",
        count: null,
        count_exact: false,
      })),
    [catalog.metrics],
  );
  const groups = useMemo(() => {
    const grouped = {};
    attributeKeys.forEach(({ key, type, count, count_exact: countExact }) => {
      const parts = key.split(".");
      const prefix = parts.length > 1 ? parts.slice(0, -1).join(".") : key;
      if (!grouped[prefix]) grouped[prefix] = { keys: [], totalCount: 0 };
      grouped[prefix].keys.push({ key, type, count, count_exact: countExact });
      if (countExact && Number.isFinite(count)) {
        grouped[prefix].totalCount += count;
      }
    });
    return Object.entries(grouped)
      .map(([prefix, data]) => ({ prefix, ...data }))
      .sort(
        (a, b) =>
          b.totalCount - a.totalCount || a.prefix.localeCompare(b.prefix),
      );
  }, [attributeKeys]);
  const filteredKeys = useMemo(() => {
    if (debouncedSearch || !selectedGroup) return attributeKeys;
    return groups.find((group) => group.prefix === selectedGroup)?.keys || [];
  }, [attributeKeys, debouncedSearch, groups, selectedGroup]);

  if (catalog.legacyFallbackRequired) return <LegacyAttributesView />;

  if (isLoading) {
    return <LoadingScreen sx={{ height: "calc(100vh - 180px)" }} />;
  }

  if (isError && attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => catalog.refetch()}
            >
              Retry
            </Button>
          }
        >
          Span attributes could not be loaded. Please retry.
        </Alert>
      </Box>
    );
  }

  if (cursorStopped) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => catalog.refetch()}
            >
              Retry pagination
            </Button>
          }
        >
          Attribute pagination stopped safely. Please retry.
        </Alert>
      </Box>
    );
  }

  if (attributeKeys.length === 0 && hasNextPage) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {isFetchingNextPage ? (
          <CircularProgress size={24} />
        ) : (
          <Button variant="outlined" onClick={() => catalog.fetchNextPage()}>
            Continue loading attributes
          </Button>
        )}
        <Typography variant="body2" color="text.secondary">
          Searching older traces for attributes…
        </Typography>
      </Box>
    );
  }

  if (attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Typography variant="h6" color="text.secondary">
          No Span Attributes Found
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Span attributes will appear here once trace data is ingested.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 180px)",
        overflow: "hidden",
      }}
    >
      {isError && (
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => catalog.refetch()}
            >
              Retry
            </Button>
          }
          sx={{ m: 1, mb: 0, flexShrink: 0 }}
        >
          Span attributes could not be refreshed. Existing attributes are still
          available.
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <AttributeGroupList
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <AttributeKeyList
          keys={filteredKeys}
          selectedKey={selectedKey}
          onSelectKey={setSelectedKey}
          hasMore={hasNextPage}
          isLoadingMore={isFetchingNextPage}
          onLoadMore={catalog.fetchNextPage}
          search={search}
          onSearchChange={setSearch}
        />
        <AttributeDetail projectId={projectId} attributeKey={selectedKey} />
      </Box>
    </Box>
  );
};

export default AttributesView;
