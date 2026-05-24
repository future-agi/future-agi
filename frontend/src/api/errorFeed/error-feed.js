import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { objectSnakeToCamel } from "src/utils/utils";

const KEYS = {
  list: (params) => ["errorFeed", "list", params],
  stats: (params) => ["errorFeed", "stats", params],
  detail: (clusterId) => ["errorFeed", "detail", clusterId],
  overview: (clusterId) => ["errorFeed", "overview", clusterId],
  traces: (clusterId, params) => ["errorFeed", "traces", clusterId, params],
  trends: (clusterId, params) => ["errorFeed", "trends", clusterId, params],
  sidebar: (clusterId, traceId) => ["errorFeed", "sidebar", clusterId, traceId],
  rootCause: (clusterId, traceId) => [
    "errorFeed",
    "rootCause",
    clusterId,
    traceId,
  ],
  linearTeams: ["errorFeed", "linearTeams"],
  projects: ["errorFeed", "projects"],
};

/**
 * Fetch the list of observe projects the user has access to.
 * Used to populate the Project filter dropdown.
 */
export const useObserveProjectList = (options = {}) => {
  return useQuery({
    ...options,
    queryKey: KEYS.projects,
    queryFn: () =>
      axios.get(endpoints.project.projectObserveList, {
        params: {
          project_type: "observe",
          page_number: 0,
          page_size: 200,
        },
      }),
    select: (res) => {
      const rows = res?.data?.result?.table ?? [];
      return rows.map((p) => ({ value: p.id, label: p.name }));
    },
    staleTime: 5 * 60 * 1000,
  });
};

/**
 * Fetch paginated list of error clusters for the Feed table.
 * Server-side filters/sort/pagination.
 * project_id is optional — backend scopes to all accessible projects when absent.
 */
export const useErrorFeedList = (params, options = {}) => {
  return useQuery({
    ...options,
    queryKey: KEYS.list(params),
    queryFn: () => axios.get(endpoints.errorFeed.list, { params }),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    staleTime: 30 * 1000,
    keepPreviousData: true,
  });
};

/**
 * Fetch top stats bar totals (counts by status, affected users).
 */
export const useErrorFeedStats = (params, options = {}) => {
  return useQuery({
    ...options,
    queryKey: KEYS.stats(params),
    queryFn: () => axios.get(endpoints.errorFeed.stats, { params }),
    select: (res) => res?.data?.result,
    staleTime: 30 * 1000,
  });
};

/**
 * Fetch detail core for a single cluster (detail view header + sidebar).
 * project_id is optional — backend will find the cluster by cluster_id alone.
 */
export const useErrorFeedDetail = (clusterId, options = {}) => {
  const enabled = !!clusterId && (options.enabled ?? true);

  return useQuery({
    ...options,
    queryKey: KEYS.detail(clusterId),
    queryFn: () => axios.get(endpoints.errorFeed.detail(clusterId)),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    enabled,
  });
};

/**
 * Overview tab: events over time, pattern summary, representative traces.
 */
export const useErrorFeedOverview = (clusterId, options = {}) => {
  const enabled = !!clusterId && (options.enabled ?? true);

  return useQuery({
    ...options,
    queryKey: KEYS.overview(clusterId),
    queryFn: () => axios.get(endpoints.errorFeed.overview(clusterId)),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    enabled,
  });
};

/**
 * Traces tab: aggregates + paginated trace list.
 */
export const useErrorFeedTraces = (clusterId, params = {}, options = {}) => {
  const enabled = !!clusterId && (options.enabled ?? true);

  return useQuery({
    ...options,
    queryKey: KEYS.traces(clusterId, params),
    queryFn: () => axios.get(endpoints.errorFeed.traces(clusterId), { params }),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    enabled,
    keepPreviousData: true,
  });
};

/**
 * Trends tab: KPI metrics, daily events, score trends, activity heatmap.
 */
export const useErrorFeedTrends = (clusterId, params = {}, options = {}) => {
  const enabled = !!clusterId && (options.enabled ?? true);

  return useQuery({
    ...options,
    queryKey: KEYS.trends(clusterId, params),
    queryFn: () => axios.get(endpoints.errorFeed.trends(clusterId), { params }),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    enabled,
  });
};

/**
 * Sidebar: timeline, AI metadata, evaluations, co-occurring issues.
 *
 * When ``traceId`` is provided, the backend scopes AI Metadata +
 * Evaluations to that specific trace so the sidebar stays in sync
 * with the Overview tab's trace selection. ``traceId`` is part of
 * the query key so switching selection triggers a refetch.
 */
export const useErrorFeedSidebar = (clusterId, traceId, options = {}) => {
  const enabled = !!clusterId && (options.enabled ?? true);

  return useQuery({
    ...options,
    queryKey: KEYS.sidebar(clusterId, traceId ?? null),
    queryFn: () =>
      axios.get(endpoints.errorFeed.sidebar(clusterId), {
        params: traceId ? { trace_id: traceId } : undefined,
      }),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    enabled,
  });
};

/**
 * Deep analysis results for a single trace within a cluster.
 *
 * Drives the "Run Deep Analysis" button's state machine:
 *   idle → running → done (or failed)
 *
 * When the server reports ``running``, the query auto-polls every 5
 * seconds until the state flips. ``traceId`` is required — the
 * backend rejects the request without it. The hook stays disabled
 * until both ``clusterId`` and ``traceId`` are present.
 */
export const useErrorFeedDeepAnalysis = (clusterId, traceId, options = {}) => {
  const enabled = !!clusterId && !!traceId && (options.enabled ?? true);

  return useQuery({
    ...options,
    queryKey: KEYS.rootCause(clusterId, traceId),
    queryFn: () =>
      axios.get(endpoints.errorFeed.rootCause(clusterId), {
        params: { trace_id: traceId },
      }),
    select: (res) => objectSnakeToCamel(res?.data?.result),
    enabled,
    refetchInterval: (data) => (data?.status === "running" ? 5000 : false),
    refetchIntervalInBackground: true,
  });
};

/**
 * Dispatch a deep analysis run.
 *
 * Without ``force``, this is an idempotent "run if not already done"
 * call — the backend returns the current status without re-running.
 * With ``force: true`` (the Re-run button), the backend deletes the
 * existing analysis and kicks off a fresh Temporal activity.
 *
 * On success, invalidates the matching root-cause query so the
 * running state picks up immediately.
 */
export const useRunDeepAnalysis = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ clusterId, traceId, force = false }) =>
      axios.post(endpoints.errorFeed.deepAnalysis(clusterId), {
        trace_id: traceId,
        force,
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: KEYS.rootCause(variables.clusterId, variables.traceId),
      });
    },
  });
};

/**
 * Fetch Linear teams for the team picker dropdown.
 * Returns { connected, teams } — connected=false if no Linear integration.
 */
export const useLinearTeams = (options = {}) => {
  return useQuery({
    ...options,
    queryKey: KEYS.linearTeams,
    queryFn: () => axios.get(endpoints.errorFeed.linearTeams),
    select: (res) => res?.data?.result,
    staleTime: 30 * 1000,
  });
};

/**
 * Create a Linear issue from an error cluster.
 * Invalidates detail + sidebar on success.
 */
export const useCreateLinearIssue = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ clusterId, teamId, title, description, priority }) =>
      axios.post(endpoints.errorFeed.createLinearIssue(clusterId), {
        team_id: teamId,
        ...(title && { title }),
        ...(description && { description }),
        ...(priority !== undefined && { priority }),
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: KEYS.detail(variables.clusterId),
      });
      queryClient.invalidateQueries({
        queryKey: KEYS.sidebar(variables.clusterId),
      });
    },
  });
};

/**
 * PATCH status / severity / assignee on a cluster.
 * Invalidates list, stats, and detail on success.
 */
export const useUpdateErrorFeedIssue = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ clusterId, status, severity, assignee }) =>
      axios.patch(endpoints.errorFeed.update(clusterId), {
        ...(status !== undefined && { status }),
        ...(severity !== undefined && { severity }),
        ...(assignee !== undefined && { assignee }),
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["errorFeed", "list"] });
      queryClient.invalidateQueries({ queryKey: ["errorFeed", "stats"] });
      queryClient.invalidateQueries({
        queryKey: KEYS.detail(variables.clusterId),
      });
    },
  });
};
