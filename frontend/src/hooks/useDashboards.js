import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import axios, { endpoints } from "src/utils/axios";
import { getFilterValueReadState } from "src/utils/queryReadState";
import { accumulateUniqueListContinuations } from "src/sections/projects/LLMTracing/listCursorPagination";
import { truncateUtf8String } from "src/api/contracts/filter-contract";
import {
  ANALYTICS_REQUEST_TIMEOUT_MS,
  CURSOR_MAX_EMPTY_CONTINUATIONS,
  FILTER_VALUE_MIN_VISIBLE_RESULTS,
  FILTER_VALUE_PAGE_SIZE,
  FILTER_VALUE_CACHE_TIME_MS,
  FILTER_VALUE_REQUEST_TIMEOUT_MS as CONFIGURED_FILTER_VALUE_REQUEST_TIMEOUT_MS,
  FILTER_VALUE_STALE_TIME_MS,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
  PROPERTY_CATALOG_CACHE_TIME_MS,
  PROPERTY_CATALOG_PAGE_SIZE,
  PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES,
  PROPERTY_CATALOG_STALE_TIME_MS,
} from "src/config/runtime_limits";

export const boundPropertyCatalogSearch = (search) =>
  truncateUtf8String(search, PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES);

const DASHBOARD_KEYS = {
  all: ["dashboards"],
  list: () => [...DASHBOARD_KEYS.all, "list"],
  detail: (id) => [...DASHBOARD_KEYS.all, "detail", id],
  metricsPaginated: (
    category,
    search,
    source,
    projectIds,
    perEvalConfig,
    excludeCustomAttributes,
    pageSize,
  ) => [
    ...DASHBOARD_KEYS.all,
    "metrics",
    "paginated",
    category,
    search,
    source,
    [...(projectIds || [])].map(String).sort(),
    Boolean(perEvalConfig),
    excludeCustomAttributes,
    pageSize,
  ],
  propertyCatalog: (
    category,
    search,
    source,
    projectIds,
    agentDefinitionId,
    perEvalConfig,
    role,
    pageSize,
    cacheScopeKey,
  ) => [
    ...DASHBOARD_KEYS.all,
    "property-catalog",
    category,
    search,
    source,
    [...(projectIds || [])].map(String).sort(),
    agentDefinitionId || "",
    Boolean(perEvalConfig),
    role || "",
    pageSize,
    cacheScopeKey || "",
  ],
};

const PROPERTY_CATALOG_CURSOR_STOPPED_KEY = "__propertyCatalogCursorStopped";
const PROPERTY_CATALOG_COUNT_KEYS = [
  "all",
  "system_metric",
  "eval_metric",
  "annotation_metric",
  "custom_attribute",
  "custom_column",
];

const validPropertyCatalogCategoryCounts = (page) => {
  const hasCounts = Object.prototype.hasOwnProperty.call(
    page || {},
    "category_counts",
  );
  const hasExactFlag = Object.prototype.hasOwnProperty.call(
    page || {},
    "category_counts_exact",
  );
  // Keep rolling deploys safe: an older activated-catalog response has
  // neither field. Once either field is present, require the complete exact
  // contract so a partial response cannot masquerade as trustworthy counts.
  if (!hasCounts && !hasExactFlag) return true;
  if (!hasCounts || !hasExactFlag) return false;
  const counts = page?.category_counts;
  if (
    page?.category_counts_exact !== true ||
    !counts ||
    typeof counts !== "object" ||
    Array.isArray(counts) ||
    Object.keys(counts).length !== PROPERTY_CATALOG_COUNT_KEYS.length ||
    !PROPERTY_CATALOG_COUNT_KEYS.every(
      (key) => Number.isSafeInteger(counts[key]) && counts[key] >= 0,
    )
  ) {
    return false;
  }
  return (
    counts.all ===
    PROPERTY_CATALOG_COUNT_KEYS.slice(1).reduce(
      (total, key) => total + counts[key],
      0,
    )
  );
};

/**
 * The legacy definition readers are a rollout compatibility path, not a
 * generic error fallback. Only the server's explicit DEV/rollout readiness
 * response may select them; auth, validation, cursor, and catalog-integrity
 * failures stay visible and fail closed.
 */
export const isPropertyCatalogNotReadyError = (error) => {
  // `src/utils/axios` deliberately flattens API failures before callers see
  // them. Keep the raw Axios shape for tests/alternate clients, but recognize
  // the application shape used by every live picker as well. Without this,
  // the rollout-only legacy reader never opens for a non-activated workspace.
  const status = error?.response?.status ?? error?.statusCode;
  const code = error?.response?.data?.code ?? error?.code;
  return status === 503 && code === "property_catalog_not_ready";
};

const canonicalizePropertyDefinition = (value) => {
  if (Array.isArray(value)) return value.map(canonicalizePropertyDefinition);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalizePropertyDefinition(value[key])]),
    );
  }
  return value;
};

const serializedPropertyDefinition = (metric) =>
  JSON.stringify(canonicalizePropertyDefinition(metric));

const samePropertyCatalogActivation = (page, baseline) =>
  page?.catalog_epoch === baseline?.catalog_epoch &&
  page?.catalog_revision === baseline?.catalog_revision &&
  page?.activation_fingerprint === baseline?.activation_fingerprint;

const stopPropertyCatalogCursor = (page, reason) => ({
  ...(page || {}),
  [PROPERTY_CATALOG_CURSOR_STOPPED_KEY]: reason,
});

export const validatePropertyCatalogPage = (
  page,
  consumedCursors = new Set(),
) => {
  if (
    !page ||
    page.query_complete !== true ||
    page.query_exact !== true ||
    page.query_status !== "complete" ||
    page.query_provenance !== "activated_property_catalog" ||
    !Number.isSafeInteger(page.catalog_epoch) ||
    page.catalog_epoch < 1 ||
    !Number.isSafeInteger(page.catalog_revision) ||
    page.catalog_revision < 1 ||
    !/^[0-9a-f]{64}$/.test(page.activation_fingerprint || "") ||
    !Array.isArray(page.metrics) ||
    page.total !== null ||
    page.total_is_exact !== false ||
    !validPropertyCatalogCategoryCounts(page)
  ) {
    return stopPropertyCatalogCursor(page, "malformed_page");
  }
  if (page.has_more === true) {
    if (
      typeof page.next_cursor !== "string" ||
      page.next_cursor.length === 0 ||
      consumedCursors.has(page.next_cursor)
    ) {
      return stopPropertyCatalogCursor(page, "malformed_cursor");
    }
    return page;
  }
  if (page.has_more === false && page.next_cursor == null) return page;
  return stopPropertyCatalogCursor(page, "malformed_cursor");
};

const isPropertyCatalogCursorStopped = (page) =>
  typeof page?.[PROPERTY_CATALOG_CURSOR_STOPPED_KEY] === "string";

// A bounded value walk may report `limit_reached` together with an advancing
// signed cursor. That is a resumable checkpoint; only `exhausted` is terminal.
const FILTER_VALUE_TERMINAL_BROWSE_STATUSES = new Set(["exhausted"]);
const FILTER_VALUE_FOLLOWED_CURSORS_KEY = "__filterValueFollowedCursors";
const FILTER_VALUE_CURSOR_STOPPED_KEY = "__filterValueCursorStopped";
const DASHBOARD_QUERY_REFRESH_PARAMS = Object.freeze({ refresh: true });

// Keep the browser deadline independently configurable so it also bounds a
// stalled proxy or a response that never reaches the application server.
export const PROPERTY_CATALOG_REQUEST_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

const hasOwn = (value, key) =>
  Object.prototype.hasOwnProperty.call(value || {}, key);

const normalizeFilterValuePage = (page = {}) =>
  FILTER_VALUE_TERMINAL_BROWSE_STATUSES.has(page?.browse_status)
    ? { ...page, has_more: false, next_cursor: null }
    : page;

const stopFilterValueCursor = (page, reason) => ({
  ...page,
  [FILTER_VALUE_CURSOR_STOPPED_KEY]: reason,
});

const isFilterValueCursorStopped = (page) =>
  typeof page?.[FILTER_VALUE_CURSOR_STOPPED_KEY] === "string";

const validateFilterValueCursor = (page, consumedCursors = new Set()) => {
  const normalized = normalizeFilterValuePage(page);
  const hasMoreField = hasOwn(normalized, "has_more");
  const nextCursorField = hasOwn(normalized, "next_cursor");

  // Keep compatibility with an older, wholly cursor-less response. A partial
  // cursor contract is never safe to interpret as exact exhaustion, though.
  if (!hasMoreField && !nextCursorField) return normalized;
  if (!hasMoreField || !nextCursorField) {
    return stopFilterValueCursor(normalized, "malformed_cursor");
  }

  if (normalized.has_more === true) {
    const cursor = normalized.next_cursor;
    if (typeof cursor !== "string" || cursor.length === 0) {
      return stopFilterValueCursor(normalized, "malformed_cursor");
    }
    if (consumedCursors.has(cursor)) {
      return stopFilterValueCursor(normalized, "repeated_cursor");
    }
    return normalized;
  }

  if (normalized.has_more === false && normalized.next_cursor == null) {
    return normalized;
  }
  return stopFilterValueCursor(normalized, "malformed_cursor");
};

// Each physical request has its own configurable browser deadline, including a
// stalled proxy. Sparse system pages use the separately bounded page-fill wall
// below. A signed cursor always keeps the vocabulary resumable.
export const FILTER_VALUE_REQUEST_TIMEOUT_MS =
  CONFIGURED_FILTER_VALUE_REQUEST_TIMEOUT_MS;
// Sparse system dimensions (for example Model) are walked in exact physical
// time-slice checkpoints. Follow checkpoint-only pages until the first useful
// result, publish it immediately, and leave every later signed checkpoint for
// the picker's explicit Load more action. This keeps a slow later slice from
// hiding values that the API already returned inside the configured action wall.
const SYSTEM_FILTER_VALUE_PAGE_FILL_DEADLINE_MS = ANALYTICS_REQUEST_TIMEOUT_MS;
const SYSTEM_FILTER_VALUE_PAGE_FILL_MAX_CONTINUATIONS =
  CURSOR_MAX_EMPTY_CONTINUATIONS;

const getFilterValueIdentity = (option) => {
  const value =
    option && typeof option === "object" && "value" in option
      ? option.value
      : option;
  const storageType =
    option && typeof option === "object" ? option.type || "" : "";
  return `${storageType}:${typeof value}:${JSON.stringify(value)}`;
};

const getFilterValueNextCursor = (page) => {
  if (isFilterValueCursorStopped(page)) return undefined;
  const normalized = normalizeFilterValuePage(page);
  const cursor = normalized?.next_cursor;
  return normalized?.has_more === true &&
    typeof cursor === "string" &&
    cursor.length > 0
    ? cursor
    : undefined;
};

const isFilterValueCursorChainStopped = (data) => {
  const pages = Array.isArray(data?.pages) ? data.pages : [];
  if (pages.some(isFilterValueCursorStopped)) return true;
  if (pages.length === 0) return false;

  const pageParams = Array.isArray(data?.pageParams) ? data.pageParams : [];
  const nextCursor = getFilterValueNextCursor(pages.at(-1));
  if (!nextCursor) return false;

  const consumedCursors = new Set(
    pageParams.filter(
      (cursor) => typeof cursor === "string" && cursor.length > 0,
    ),
  );
  for (const page of pages) {
    for (const cursor of page?.[FILTER_VALUE_FOLLOWED_CURSORS_KEY] || []) {
      consumedCursors.add(cursor);
    }
  }
  return consumedCursors.has(nextCursor);
};

const compactFilterValueRetryPage = (previousData, freshPage) => {
  const seen = new Set();
  const values = [
    ...(previousData?.pages || []).flatMap((page) => page?.values || []),
    ...(freshPage?.values || []),
  ].filter((option) => {
    const identity = getFilterValueIdentity(option);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });

  // Fresh response metadata owns the new cursor chain. Previously loaded rows
  // remain selectable, but old cursor/page history is deliberately discarded
  // so a Retry can never replay an arbitrarily long infinite-query cache.
  return { ...freshPage, values };
};

export function useDashboardList() {
  return useQuery({
    queryKey: DASHBOARD_KEYS.list(),
    queryFn: () => axios.get(endpoints.dashboard.list),
    select: (res) => res.data?.result || [],
  });
}

export function useDashboardDetail(id) {
  return useQuery({
    queryKey: DASHBOARD_KEYS.detail(id),
    queryFn: () => axios.get(endpoints.dashboard.detail(id)),
    select: (res) => res.data?.result || null,
    enabled: Boolean(id),
  });
}

export function useCreateDashboard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) => axios.post(endpoints.dashboard.create, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.all });
    },
  });
}

export function useUpdateDashboard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }) =>
      axios.patch(endpoints.dashboard.update(id), data),
    onMutate: async ({ id, data }) => {
      await queryClient.cancelQueries({ queryKey: DASHBOARD_KEYS.detail(id) });
      const previousDetail = queryClient.getQueryData(
        DASHBOARD_KEYS.detail(id),
      );
      queryClient.setQueryData(DASHBOARD_KEYS.detail(id), (old) => {
        if (!old) return old;
        const result = old.data?.result || old;
        const updated = { ...result, ...data };
        return old.data
          ? { ...old, data: { ...old.data, result: updated } }
          : updated;
      });
      return { previousDetail };
    },
    onError: (_, { id }, context) => {
      if (context?.previousDetail) {
        queryClient.setQueryData(
          DASHBOARD_KEYS.detail(id),
          context.previousDetail,
        );
      }
    },
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.detail(id) });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useDeleteDashboard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => axios.delete(endpoints.dashboard.delete(id)),
    onSuccess: (_, id) => {
      queryClient.removeQueries({ queryKey: DASHBOARD_KEYS.detail(id) });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useLegacyDashboardMetricsPaginated({
  category = "",
  source = "",
  search = "",
  projectIds = [],
  perEvalConfig = false,
  pageSize = PROPERTY_CATALOG_PAGE_SIZE,
  excludeCustomAttributes = false,
  enabled = true,
} = {}) {
  const boundedSearch = boundPropertyCatalogSearch(search);
  const canonicalProjectIds = [
    ...new Set((projectIds || []).map(String)),
  ].sort();
  const query = useInfiniteQuery({
    queryKey: DASHBOARD_KEYS.metricsPaginated(
      category,
      boundedSearch,
      source,
      canonicalProjectIds,
      perEvalConfig,
      excludeCustomAttributes,
      pageSize,
    ),
    queryFn: ({ pageParam = 1, signal }) =>
      axios.get(endpoints.dashboard.metrics, {
        signal,
        timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
        params: {
          ...(category ? { category } : {}),
          ...(source ? { source } : {}),
          ...(boundedSearch ? { search: boundedSearch } : {}),
          ...(canonicalProjectIds.length
            ? { project_ids: canonicalProjectIds.join(",") }
            : {}),
          ...(perEvalConfig ? { per_eval_config: true } : {}),
          ...(excludeCustomAttributes
            ? { exclude_custom_attributes: true }
            : {}),
          page: pageParam,
          page_size: pageSize,
        },
      }),
    getNextPageParam: (lastPage) => {
      const result = lastPage.data?.result;
      return result?.has_more ? result.page + 1 : undefined;
    },
    initialPageParam: 1,
    enabled,
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_CACHE_TIME_MS,
    meta: { errorHandled: true },
  });

  // Flatten all pages into a single metrics array
  const metrics =
    query.data?.pages.reduce((acc, page) => {
      const items = page.data?.result?.metrics || [];
      return acc.concat(items);
    }, []) || [];

  const total = query.data?.pages[0]?.data?.result?.total ?? 0;

  return {
    ...query,
    metrics,
    total,
    continuationKey: query.hasNextPage
      ? `legacy-page:${Number(query.data?.pages?.at(-1)?.data?.result?.page || 1) + 1}`
      : null,
    pageCount: query.data?.pages?.length || 0,
  };
}

// Backward-compatible export for callers outside the migrated first-party
// surfaces. New definition readers must use `usePropertyCatalog` and may
// enable this page-number reader only after the typed rollout not-ready 503.
export const useDashboardMetricsPaginated = useLegacyDashboardMetricsPaginated;

export function usePropertyCatalog({
  category = "",
  source = "",
  search = "",
  projectIds = [],
  agentDefinitionId = "",
  perEvalConfig = false,
  role = "",
  pageSize = PROPERTY_CATALOG_PAGE_SIZE,
  enabled = true,
  allowLegacyNotReadyFallback = false,
  fallbackScopeKey = "",
  cacheScopeKey = "",
} = {}) {
  const boundedSearch = boundPropertyCatalogSearch(search);
  const canonicalProjectIds = [
    ...new Set((projectIds || []).map(String)),
  ].sort();
  const [legacyFallbackScopeKey, setLegacyFallbackScopeKey] = useState(null);
  const legacyFallbackRequired = Boolean(
    allowLegacyNotReadyFallback &&
      fallbackScopeKey &&
      legacyFallbackScopeKey === fallbackScopeKey,
  );
  const query = useInfiniteQuery({
    queryKey: DASHBOARD_KEYS.propertyCatalog(
      category,
      boundedSearch,
      source,
      canonicalProjectIds,
      agentDefinitionId,
      perEvalConfig,
      role,
      pageSize,
      cacheScopeKey,
    ),
    queryFn: ({ pageParam, signal }) =>
      axios
        .get(endpoints.dashboard.metrics, {
          signal,
          timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
          params: {
            cursor_mode: true,
            page_size: pageSize,
            ...(category ? { category } : {}),
            ...(source ? { source } : {}),
            ...(boundedSearch ? { search: boundedSearch } : {}),
            ...(canonicalProjectIds.length
              ? { project_ids: canonicalProjectIds.join(",") }
              : {}),
            ...(agentDefinitionId
              ? { agent_definition_id: agentDefinitionId }
              : {}),
            ...(perEvalConfig ? { per_eval_config: true } : {}),
            ...(role ? { role } : {}),
            ...(pageParam ? { cursor: pageParam } : {}),
          },
        })
        .then(({ data }) => data?.result || {}),
    initialPageParam: null,
    getNextPageParam: (lastPage, allPages) => {
      const consumed = new Set(
        allPages
          .slice(0, -1)
          .flatMap((page) =>
            typeof page?.next_cursor === "string" ? [page.next_cursor] : [],
          ),
      );
      const checked = validatePropertyCatalogPage(lastPage, consumed);
      const firstPage = validatePropertyCatalogPage(allPages[0]);
      return isPropertyCatalogCursorStopped(checked) ||
        isPropertyCatalogCursorStopped(firstPage) ||
        !samePropertyCatalogActivation(checked, firstPage) ||
        !checked.has_more
        ? undefined
        : checked.next_cursor;
    },
    enabled: enabled && !legacyFallbackRequired,
    retry: false,
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_CACHE_TIME_MS,
    refetchOnWindowFocus: false,
    meta: { errorHandled: true },
  });

  useEffect(() => {
    if (
      allowLegacyNotReadyFallback &&
      enabled &&
      fallbackScopeKey &&
      isPropertyCatalogNotReadyError(query.error) &&
      legacyFallbackScopeKey !== fallbackScopeKey
    ) {
      setLegacyFallbackScopeKey(fallbackScopeKey);
    }
  }, [
    allowLegacyNotReadyFallback,
    enabled,
    fallbackScopeKey,
    legacyFallbackScopeKey,
    query.error,
  ]);

  const rawPages = query.data?.pages || [];
  let chainFailureReason = null;
  const checkedPages = rawPages.map((page, index) => {
    const consumed = new Set(
      rawPages
        .slice(0, index)
        .flatMap((earlier) =>
          typeof earlier?.next_cursor === "string" ? [earlier.next_cursor] : [],
        ),
    );
    const checked = validatePropertyCatalogPage(page, consumed);
    if (isPropertyCatalogCursorStopped(checked)) {
      chainFailureReason ||= checked[PROPERTY_CATALOG_CURSOR_STOPPED_KEY];
    }
    return checked;
  });
  const baselinePage = checkedPages[0];
  if (
    baselinePage &&
    checkedPages.some(
      (page) => !samePropertyCatalogActivation(page, baselinePage),
    )
  ) {
    chainFailureReason ||= "activation_mismatch";
  }
  if (
    baselinePage &&
    checkedPages.some(
      (page) =>
        JSON.stringify(page.category_counts) !==
        JSON.stringify(baselinePage.category_counts),
    )
  ) {
    chainFailureReason ||= "category_count_mismatch";
  }
  let duplicateProperty = false;
  let definitionConflict = false;
  const definitionsById = new Map();
  const candidateMetrics = checkedPages.flatMap((page) =>
    (page.metrics || []).filter((metric) => {
      const propertyId = metric?.property_id;
      if (typeof propertyId !== "string" || propertyId.length === 0) {
        definitionConflict = true;
        return false;
      }
      const serialized = serializedPropertyDefinition(metric);
      if (definitionsById.has(propertyId)) {
        duplicateProperty = true;
        if (definitionsById.get(propertyId) !== serialized) {
          definitionConflict = true;
        }
        return false;
      }
      definitionsById.set(propertyId, serialized);
      return true;
    }),
  );
  if (definitionConflict) {
    chainFailureReason ||= "definition_conflict";
  } else if (duplicateProperty) {
    chainFailureReason ||= "duplicate_property";
  }
  const cursorChainStopped = chainFailureReason !== null;
  const metrics = cursorChainStopped ? [] : candidateMetrics;
  const isRemoteCatalogSearchPending = Boolean(
    enabled &&
      !legacyFallbackRequired &&
      boundedSearch &&
      query.isFetching &&
      !query.isFetchingNextPage,
  );
  const isRemoteCatalogNextPagePending = Boolean(
    enabled && !legacyFallbackRequired && query.isFetchingNextPage,
  );

  return {
    ...query,
    continuationKey:
      !cursorChainStopped && query.hasNextPage
        ? checkedPages.at(-1)?.next_cursor || null
        : null,
    pageCount: checkedPages.length,
    hasNextPage: cursorChainStopped ? false : query.hasNextPage,
    metrics,
    total: null,
    totalIsExact: false,
    categoryCounts: cursorChainStopped
      ? null
      : baselinePage?.category_counts || null,
    categoryCountsExact:
      !cursorChainStopped && baselinePage?.category_counts_exact === true,
    cursorChainStopped,
    cursorStopReason: chainFailureReason,
    legacyFallbackRequired,
    isRemoteCatalogSearchPending,
    isRemoteCatalogNextPagePending,
    queryReadState:
      query.isError || cursorChainStopped ? "degraded" : "complete",
  };
}

export function useCreateWidget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ dashboardId, data }) =>
      axios.post(endpoints.dashboard.widgets(dashboardId), data),
    onSuccess: (_, { dashboardId }) => {
      queryClient.invalidateQueries({
        queryKey: DASHBOARD_KEYS.detail(dashboardId),
      });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useUpdateWidget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ dashboardId, widgetId, data }) =>
      axios.patch(
        endpoints.dashboard.widgetDetail(dashboardId, widgetId),
        data,
      ),
    onSuccess: (_, { dashboardId }) => {
      queryClient.invalidateQueries({
        queryKey: DASHBOARD_KEYS.detail(dashboardId),
      });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useDeleteWidget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ dashboardId, widgetId }) =>
      axios.delete(endpoints.dashboard.widgetDetail(dashboardId, widgetId)),
    onSuccess: (_, { dashboardId }) => {
      queryClient.invalidateQueries({
        queryKey: DASHBOARD_KEYS.detail(dashboardId),
      });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useReorderWidgets() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ dashboardId, order }) =>
      axios.post(endpoints.dashboard.widgetReorder(dashboardId), { order }),
    onSuccess: (_, { dashboardId }) => {
      queryClient.invalidateQueries({
        queryKey: DASHBOARD_KEYS.detail(dashboardId),
      });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useDuplicateWidget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ dashboardId, widgetId }) =>
      axios.post(endpoints.dashboard.widgetDuplicate(dashboardId, widgetId)),
    onSuccess: (_, { dashboardId }) => {
      queryClient.invalidateQueries({
        queryKey: DASHBOARD_KEYS.detail(dashboardId),
      });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEYS.list() });
    },
  });
}

export function useWidgetQuery() {
  return useMutation({
    mutationFn: ({ dashboardId, widgetId }) =>
      axios.post(endpoints.dashboard.widgetQuery(dashboardId, widgetId), {}),
    meta: { errorHandled: true },
  });
}

export function usePreviewQuery() {
  return useMutation({
    mutationFn: ({ dashboardId, queryConfig }) =>
      axios.post(endpoints.dashboard.widgetPreview(dashboardId), {
        query_config: queryConfig,
      }),
    meta: { errorHandled: true },
  });
}

export function useDashboardQuery() {
  return useMutation({
    mutationFn: (request) => {
      // Backwards compatible with existing editor callers that pass the query
      // config directly. Saved dashboards use the wrapper shape so an explicit
      // user refresh can bypass the server snapshot cache.
      const wrappedRequest = Boolean(request?.queryConfig);
      const queryConfig = wrappedRequest ? request.queryConfig : request;
      const refresh = wrappedRequest && request.refresh === true;
      const signal = wrappedRequest ? request.signal : undefined;
      const body = { ...queryConfig };

      if (refresh) {
        return axios.post(endpoints.dashboard.query, body, {
          params: DASHBOARD_QUERY_REFRESH_PARAMS,
          ...(signal ? { signal } : {}),
        });
      }
      return signal
        ? axios.post(endpoints.dashboard.query, body, { signal })
        : axios.post(endpoints.dashboard.query, body);
    },
    // Dashboard surfaces render a generic retry state. Keep raw backend/DB
    // details out of the global mutation snackbar.
    meta: { errorHandled: true },
  });
}

export function buildPropertyRegistryId({
  propertyId,
  metricName,
  metricType = "system_metric",
  source = "traces",
}) {
  if (propertyId) return propertyId;
  if (!metricName) return "";
  if (metricType === "custom_attribute")
    return `custom_attribute:${metricName}`;
  if (metricType === "eval_metric") return `eval:${metricName}`;
  if (metricType === "annotation_metric") return `annotation:${metricName}`;
  if (metricType === "custom_column" || source === "dataset_column") {
    return `dataset_column:${metricName}`;
  }
  return `system_attribute:${source}:${metricName}`;
}

export function buildFilterValueRetryScope({
  propertyId,
  metricName,
  metricType,
  projectIds,
  datasetId,
  source,
  workflow,
  pageSize,
  attributeType,
}) {
  return JSON.stringify({
    property_id: propertyId || "",
    metric_name: metricName || "",
    metric_type: metricType || "",
    project_ids: projectIds || [],
    dataset_id: datasetId || "",
    source: source || "",
    workflow: workflow || "",
    page_size: pageSize || null,
    attribute_type: attributeType || "",
  });
}

export function useDashboardFilterValues({
  propertyId,
  metricName,
  metricType,
  projectIds,
  datasetId,
  source = "traces",
  workflow,
  enabled = true,
  search = "",
  searchGesture = search,
  pageSize = FILTER_VALUE_PAGE_SIZE,
  attributeType,
}) {
  const queryClient = useQueryClient();
  const boundedSearch = boundPropertyCatalogSearch(search);
  const normalizedSearchGesture = String(searchGesture || "").trim();
  const valueSearchGestureStateRef = useRef({
    scope: null,
    previous: null,
    pendingRetry: null,
  });
  const freshChainRetryRef = useRef(null);
  const [freshChainRetryIdentity, setFreshChainRetryIdentity] = useState(null);
  const resolvedPropertyId = buildPropertyRegistryId({
    propertyId,
    metricName,
    metricType,
    source,
  });
  const valueRetryScope = buildFilterValueRetryScope({
    propertyId: resolvedPropertyId,
    metricName,
    metricType,
    projectIds,
    datasetId,
    source,
    workflow,
    pageSize,
    attributeType,
  });
  const queryKey = [
    ...DASHBOARD_KEYS.all,
    "filterValues",
    metricName,
    resolvedPropertyId,
    metricType,
    projectIds,
    datasetId,
    source,
    workflow,
    boundedSearch,
    pageSize,
    attributeType,
  ];
  const queryIdentity = JSON.stringify(queryKey);
  const requestFilterValuePage = (cursor, signal) =>
    axios
      .get(endpoints.dashboard.filterValues, {
        signal,
        timeout: FILTER_VALUE_REQUEST_TIMEOUT_MS,
        params: {
          ...(resolvedPropertyId ? { property_id: resolvedPropertyId } : {}),
          metric_name: metricName,
          metric_type: metricType,
          project_ids: (projectIds || []).join(","),
          ...(datasetId ? { dataset_id: datasetId } : {}),
          source,
          ...(workflow ? { workflow } : {}),
          ...(boundedSearch ? { search: boundedSearch } : {}),
          ...(pageSize ? { page_size: pageSize } : {}),
          ...(cursor ? { cursor } : {}),
          ...(attributeType ? { attribute_type: attributeType } : {}),
        },
      })
      .then((res) => res.data?.result || {});
  const readFilterValuePage = async ({ signal, pageParam, publishedData }) => {
    const actionStartedAt = Date.now();
    const requestPage = (cursor, requestSignal = signal) =>
      requestFilterValuePage(cursor, requestSignal);
    const cachedPages = publishedData?.pages || [];
    const isFreshChainRead = pageParam == null;
    const knownValueIdentities = isFreshChainRead
      ? []
      : cachedPages.flatMap((page) =>
          (page?.values || []).map(getFilterValueIdentity),
        );
    const consumedCursors = new Set(
      [
        ...(isFreshChainRead ? [] : publishedData?.pageParams || []),
        ...(isFreshChainRead
          ? []
          : cachedPages.flatMap(
              (page) => page?.[FILTER_VALUE_FOLLOWED_CURSORS_KEY] || [],
            )),
        pageParam,
      ].filter((cursor) => typeof cursor === "string" && cursor.length > 0),
    );
    const initialPage = await requestPage(pageParam);
    const checkedMetadata = (response) =>
      validateFilterValueCursor(response, consumedCursors);
    const {
      response: page,
      rows: values,
      followedCursors,
    } = await accumulateUniqueListContinuations({
      initialResponse: initialPage,
      rowsFromResponse: (response) => response?.values || [],
      identityFromRow: getFilterValueIdentity,
      knownIdentities: knownValueIdentities,
      targetRowCount:
        metricType === "system_metric"
          ? FILTER_VALUE_MIN_VISIBLE_RESULTS
          : isFreshChainRead
            ? FILTER_VALUE_MIN_VISIBLE_RESULTS
            : pageSize || FILTER_VALUE_PAGE_SIZE,
      // A private marker records a protocol stop for the picker. Project it
      // as terminal only for this bounded follower so no malformed/repeated
      // cursor is requested and the published response remains retryable.
      metadataFromResponse: (response) => {
        const checked = checkedMetadata(response);
        return isFilterValueCursorStopped(checked)
          ? { ...checked, has_more: false, next_cursor: null }
          : checked;
      },
      nextResponse: requestPage,
      onContinuation: (metadata) => {
        const nextCursor = getFilterValueNextCursor(metadata);
        if (nextCursor) consumedCursors.add(nextCursor);
      },
      isCurrent: () => !signal?.aborted,
      cancellationSignal: signal,
      startedAt: actionStartedAt,
      // A system gesture may cross empty exact time slices, but stops as soon
      // as one non-empty slice is available. The public signed cursor keeps
      // the remaining vocabulary explicitly pageable. Other metric families
      // retain their one-request behavior.
      maxContinuations:
        metricType === "system_metric"
          ? SYSTEM_FILTER_VALUE_PAGE_FILL_MAX_CONTINUATIONS
          : 0,
      maxElapsedMs:
        metricType === "system_metric"
          ? SYSTEM_FILTER_VALUE_PAGE_FILL_DEADLINE_MS
          : FILTER_VALUE_REQUEST_TIMEOUT_MS,
    });
    const checkedPage = checkedMetadata(page);
    return {
      ...checkedPage,
      values,
      [FILTER_VALUE_FOLLOWED_CURSORS_KEY]: followedCursors,
    };
  };
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ signal, pageParam }) =>
      readFilterValuePage({
        signal,
        pageParam,
        publishedData: queryClient.getQueryData(queryKey),
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage, allPages, lastPageParam, allPageParams) => {
      const nextCursor = getFilterValueNextCursor(lastPage);
      if (!nextCursor) return undefined;
      const requestedCursors = new Set(
        (allPageParams || []).filter(
          (cursor) => typeof cursor === "string" && cursor.length > 0,
        ),
      );
      for (const page of allPages || []) {
        for (const cursor of page?.[FILTER_VALUE_FOLLOWED_CURSORS_KEY] || []) {
          requestedCursors.add(cursor);
        }
      }
      return nextCursor === lastPageParam || requestedCursors.has(nextCursor)
        ? undefined
        : nextCursor;
    },
    enabled: enabled && Boolean(metricName),
    retry: false,
    staleTime: FILTER_VALUE_STALE_TIME_MS,
    gcTime: FILTER_VALUE_CACHE_TIME_MS,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    // This surface renders a deliberately generic retry state. Prevent the
    // global query handler from echoing a backend/ClickHouse error payload.
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

  const retryFreshPage = () => {
    const activeRequest = freshChainRetryRef.current;
    if (activeRequest?.identity === queryIdentity) return activeRequest.promise;

    const controller = new AbortController();
    setFreshChainRetryIdentity(queryIdentity);
    const request = (async () => {
      await queryClient.cancelQueries({ queryKey, exact: true });
      const previousData = queryClient.getQueryData(queryKey);
      const freshPage = await readFilterValuePage({
        signal: controller.signal,
        pageParam: null,
        publishedData: undefined,
      });
      const compactedPage = compactFilterValueRetryPage(
        previousData,
        freshPage,
      );
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
        setFreshChainRetryIdentity(null);
      }
    });
    trackedRequest.promise = settledPromise;
    freshChainRetryRef.current = trackedRequest;
    return settledPromise;
  };

  useEffect(() => {
    const identity = JSON.stringify([valueRetryScope, normalizedSearchGesture]);
    const state = valueSearchGestureStateRef.current;
    if (state.scope !== valueRetryScope) {
      state.scope = valueRetryScope;
      state.previous = null;
      state.pendingRetry = null;
    }
    if (state.previous === identity) return;
    state.previous = identity;
    if (!enabled || !normalizedSearchGesture) {
      state.pendingRetry = null;
      return;
    }
    state.pendingRetry = identity;
  }, [enabled, normalizedSearchGesture, valueRetryScope]);

  useEffect(() => {
    const identity = JSON.stringify([valueRetryScope, normalizedSearchGesture]);
    const state = valueSearchGestureStateRef.current;
    if (
      state.pendingRetry !== identity ||
      !normalizedSearchGesture ||
      String(search || "").trim() !== normalizedSearchGesture
    ) {
      return;
    }
    if (query.isFetching) {
      state.pendingRetry = null;
      return;
    }
    const continuationFailed = query.isFetchNextPageError && query.hasNextPage;
    const cachedReadFailed = query.isError || query.isRefetchError;
    if (!continuationFailed && !cachedReadFailed) {
      state.pendingRetry = null;
      return;
    }

    state.pendingRetry = null;
    if (continuationFailed) void query.fetchNextPage();
    else void retryFreshPage().catch(() => {});
  }, [normalizedSearchGesture, query, search, valueRetryScope]);

  const pages = query.data?.pages || [];
  const cursorChainStopped = isFilterValueCursorChainStopped(query.data);
  const seenValues = new Set();
  const values = pages.flatMap((page) =>
    (page?.values || []).filter((option) => {
      const identity = getFilterValueIdentity(option);
      if (seenValues.has(identity)) return false;
      seenValues.add(identity);
      return true;
    }),
  );
  const pageReadStates = pages.map((page) => getFilterValueReadState(page));
  const queryReadState = query.isError
    ? "error"
    : cursorChainStopped || pageReadStates.includes("degraded")
      ? "degraded"
      : pageReadStates.includes("sampled")
        ? "sampled"
        : "complete";
  const lastPage = pages.at(-1);
  const browseStatus = lastPage?.browse_status;

  return {
    ...query,
    data: values,
    continuationKey:
      !cursorChainStopped && query.hasNextPage
        ? getFilterValueNextCursor(lastPage) || null
        : null,
    pageCount: pages.length,
    queryReadState,
    browseStatus,
    browseLimitReached: browseStatus === "limit_reached" && !query.hasNextPage,
    attributeType: pages.find((page) => page?.attribute_type)?.attribute_type,
    cursorChainStopped,
    retryFreshPage,
    // Keep the historical public name safe too. An infinite-query refetch
    // replays every cached page; retrying this interactive picker must always
    // be one fresh bounded request.
    refetch: retryFreshPage,
    isRetryingFreshPage: freshChainRetryIdentity === queryIdentity,
  };
}

export function useDatasetColumnValues({
  datasetId,
  columnId,
  enabled = true,
}) {
  // Distinct non-empty cell values for a single (dataset, column) pair.
  // Backs the dataset filter panel's Basic-tab value dropdown and seeds
  // the AI-filter smart-mode value grounding indirectly (smart mode
  // fetches server-side; this hook is strictly for the manual picker).
  const query = useDashboardFilterValues({
    propertyId: columnId ? `dataset_column:${columnId}` : "",
    metricName: columnId,
    metricType: "custom_column",
    datasetId,
    source: "dataset_column",
    pageSize: FILTER_VALUE_PAGE_SIZE,
    enabled: enabled && Boolean(datasetId) && Boolean(columnId),
  });
  const raw = query.isError ? undefined : query.data;
  // Normalize both string[] and {value,label}[] shapes to string[] while
  // retaining the infinite-query controls. Dataset vocabularies can exceed a
  // single exact page, so the picker must expose the signed continuation.
  const values =
    raw === undefined
      ? undefined
      : raw
          .map((value) => (typeof value === "string" ? value : value?.value))
          .filter((value) => typeof value === "string" && value.length > 0);
  return { ...query, data: values };
}

export function useSimulationAgents() {
  return useQuery({
    queryKey: [...DASHBOARD_KEYS.all, "simulationAgents"],
    queryFn: () => axios.get(endpoints.dashboard.simulationAgents),
    select: (res) => res.data?.result?.agents || [],
    staleTime: 5 * 60 * 1000,
  });
}
