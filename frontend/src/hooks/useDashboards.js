import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getFilterValueReadState } from "src/utils/queryReadState";
import { followEmptyListContinuations } from "src/sections/projects/LLMTracing/listCursorPagination";

const DASHBOARD_KEYS = {
  all: ["dashboards"],
  list: () => [...DASHBOARD_KEYS.all, "list"],
  detail: (id) => [...DASHBOARD_KEYS.all, "detail", id],
  metrics: (projectIds, workflow) => [
    ...DASHBOARD_KEYS.all,
    "metrics",
    projectIds,
    workflow,
  ],
  metricsPaginated: (category, search, source) => [
    ...DASHBOARD_KEYS.all,
    "metrics",
    "paginated",
    category,
    search,
    source,
  ],
};

const FILTER_VALUE_TERMINAL_BROWSE_STATUSES = new Set([
  "exhausted",
  "limit_reached",
]);
const FILTER_VALUE_FOLLOWED_CURSORS_KEY = "__filterValueFollowedCursors";

// The shared Axios instance intentionally has no global timeout. Distinct
// value browsing is interactive and the API has a 30-second read ceiling, so
// release the picker shortly after that boundary instead of leaving Load more
// in a permanent spinner when a proxy/request stalls.
export const FILTER_VALUE_REQUEST_TIMEOUT_MS = 35_000;

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
  if (FILTER_VALUE_TERMINAL_BROWSE_STATUSES.has(page?.browse_status)) {
    return undefined;
  }
  const cursor = page?.next_cursor;
  return page?.has_more === true &&
    typeof cursor === "string" &&
    cursor.length > 0
    ? cursor
    : undefined;
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

export function useDashboardMetrics(projectIds, workflow) {
  return useQuery({
    queryKey: DASHBOARD_KEYS.metrics(projectIds, workflow),
    queryFn: () =>
      axios.get(endpoints.dashboard.metrics, {
        params: {
          project_ids: (projectIds || []).join(","),
          ...(workflow ? { workflow } : {}),
        },
      }),
    select: (res) => res.data?.result || {},
  });
}

export function useDashboardMetricsPaginated({
  category = "",
  source = "",
  search = "",
  pageSize = 50,
  enabled = true,
} = {}) {
  const query = useInfiniteQuery({
    queryKey: DASHBOARD_KEYS.metricsPaginated(category, search, source),
    queryFn: ({ pageParam = 1 }) =>
      axios.get(endpoints.dashboard.metrics, {
        params: {
          ...(category ? { category } : {}),
          ...(source ? { source } : {}),
          ...(search ? { search } : {}),
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
      axios.post(endpoints.dashboard.widgetQuery(dashboardId, widgetId), {
        allow_sampled: false,
      }),
    meta: { errorHandled: true },
  });
}

export function usePreviewQuery() {
  return useMutation({
    mutationFn: ({ dashboardId, queryConfig }) =>
      axios.post(endpoints.dashboard.widgetPreview(dashboardId), {
        query_config: queryConfig,
        allow_sampled: false,
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
      const body = {
        ...queryConfig,
        allow_sampled: false,
      };

      return refresh
        ? axios.post(endpoints.dashboard.query, body, {
            params: { refresh: true },
          })
        : axios.post(endpoints.dashboard.query, body);
    },
    // Dashboard surfaces render a generic retry state. Keep raw backend/DB
    // details out of the global mutation snackbar.
    meta: { errorHandled: true },
  });
}

export function useDashboardFilterValues({
  metricName,
  metricType,
  projectIds,
  source = "traces",
  workflow,
  enabled = true,
  search = "",
  pageSize,
  attributeType,
}) {
  const queryClient = useQueryClient();
  const queryKey = [
    ...DASHBOARD_KEYS.all,
    "filterValues",
    metricName,
    metricType,
    projectIds,
    source,
    workflow,
    search,
    pageSize,
    attributeType,
  ];
  const query = useInfiniteQuery({
    queryKey,
    queryFn: async ({ signal, pageParam }) => {
      const requestPage = (cursor) =>
        axios
          .get(endpoints.dashboard.filterValues, {
            signal,
            timeout: FILTER_VALUE_REQUEST_TIMEOUT_MS,
            params: {
              metric_name: metricName,
              metric_type: metricType,
              project_ids: (projectIds || []).join(","),
              source,
              ...(workflow ? { workflow } : {}),
              ...(search ? { search } : {}),
              ...(pageSize ? { page_size: pageSize } : {}),
              ...(cursor ? { cursor } : {}),
              ...(attributeType ? { attribute_type: attributeType } : {}),
            },
          })
          .then((res) => res.data?.result || {});
      const cachedData = queryClient.getQueryData(queryKey);
      const cachedPages = cachedData?.pages || [];
      const knownValueIdentities = new Set(
        cachedPages.flatMap((page) =>
          (page?.values || []).map(getFilterValueIdentity),
        ),
      );
      const followedCursors = new Set(
        [
          ...(cachedData?.pageParams || []),
          ...cachedPages.flatMap(
            (page) => page?.[FILTER_VALUE_FOLLOWED_CURSORS_KEY] || [],
          ),
          pageParam,
        ].filter((cursor) => typeof cursor === "string" && cursor.length > 0),
      );
      const initialPage = await requestPage(pageParam);
      const page = await followEmptyListContinuations({
        initialResponse: initialPage,
        rowsFromResponse: (response) =>
          (response?.values || []).filter((option) => {
            const identity = getFilterValueIdentity(option);
            if (knownValueIdentities.has(identity)) return false;
            knownValueIdentities.add(identity);
            return true;
          }),
        metadataFromResponse: (response) => {
          const nextCursor = getFilterValueNextCursor(response);
          if (!nextCursor || followedCursors.has(nextCursor)) {
            return { ...response, has_more: false, next_cursor: null };
          }
          return response;
        },
        nextResponse: requestPage,
        onContinuation: (metadata) => {
          const nextCursor = getFilterValueNextCursor(metadata);
          if (nextCursor) followedCursors.add(nextCursor);
        },
        isCurrent: () => !signal.aborted,
      });
      return {
        ...page,
        [FILTER_VALUE_FOLLOWED_CURSORS_KEY]: [...followedCursors],
      };
    },
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
    staleTime: 5 * 60 * 1000,
    gcTime: 15 * 60 * 1000,
    // This surface renders a deliberately generic retry state. Prevent the
    // global query handler from echoing a backend/ClickHouse error payload.
    meta: { errorHandled: true },
  });

  const pages = query.data?.pages || [];
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
    : pageReadStates.includes("degraded")
      ? "degraded"
      : pageReadStates.includes("sampled")
        ? "sampled"
        : "complete";
  const lastPage = pages.at(-1);
  const browseStatus = lastPage?.browse_status;

  return {
    ...query,
    data: values,
    queryReadState,
    browseStatus,
    browseLimitReached: browseStatus === "limit_reached",
    attributeType: pages.find((page) => page?.attribute_type)?.attribute_type,
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
  return useQuery({
    queryKey: [
      ...DASHBOARD_KEYS.all,
      "datasetColumnValues",
      datasetId,
      columnId,
    ],
    queryFn: async () => {
      try {
        const res = await axios.get(endpoints.dashboard.filterValues, {
          params: {
            metric_name: columnId,
            metric_type: "system_metric",
            source: "dataset_column",
            dataset_id: datasetId,
          },
        });
        return res;
      } catch {
        return { data: { result: { values: [] } } };
      }
    },
    select: (res) => {
      const raw = res.data?.result?.values || [];
      // Normalize both string[] and {value,label}[] shapes to string[].
      return raw
        .map((v) => (typeof v === "string" ? v : v?.value))
        .filter((v) => typeof v === "string" && v.length > 0);
    },
    enabled: enabled && Boolean(datasetId) && Boolean(columnId),
    retry: false,
    staleTime: 60_000,
  });
}

export function useSimulationAgents() {
  return useQuery({
    queryKey: [...DASHBOARD_KEYS.all, "simulationAgents"],
    queryFn: () => axios.get(endpoints.dashboard.simulationAgents),
    select: (res) => res.data?.result?.agents || [],
    staleTime: 5 * 60 * 1000,
  });
}
