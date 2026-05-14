import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

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
    },
  });
}

export function useWidgetQuery() {
  return useMutation({
    mutationFn: ({ dashboardId, widgetId }) =>
      axios.post(endpoints.dashboard.widgetQuery(dashboardId, widgetId)),
  });
}

export function usePreviewQuery() {
  return useMutation({
    mutationFn: ({ dashboardId, queryConfig }) =>
      axios.post(endpoints.dashboard.widgetPreview(dashboardId), {
        query_config: queryConfig,
      }),
  });
}

export function useDashboardQuery() {
  return useMutation({
    mutationFn: (queryConfig) =>
      axios.post(endpoints.dashboard.query, queryConfig),
  });
}

export function useDashboardFilterValues({
  metricName,
  metricType,
  projectIds,
  source = "traces",
  workflow,
  enabled = true,
}) {
  return useQuery({
    queryKey: [
      ...DASHBOARD_KEYS.all,
      "filterValues",
      metricName,
      metricType,
      projectIds,
      source,
      workflow,
    ],
    queryFn: async () => {
      try {
        const res = await axios.get(endpoints.dashboard.filterValues, {
          params: {
            metric_name: metricName,
            metric_type: metricType,
            project_ids: (projectIds || []).join(","),
            source,
            ...(workflow ? { workflow } : {}),
          },
        });
        return res;
      } catch {
        // Return empty on error (e.g. column doesn't exist in CH)
        return { data: { result: { values: [] } } };
      }
    },
    select: (res) => res.data?.result?.values || [],
    enabled: enabled && Boolean(metricName),
    retry: false,
    staleTime: 5 * 60 * 1000,
    gcTime: 15 * 60 * 1000,
  });
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
