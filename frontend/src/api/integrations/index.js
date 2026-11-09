import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { getErrorMessage } from "src/sections/settings/integrations/utils";
import {
  scopedConfig,
  useIntegrationsWorkspaceId,
} from "src/sections/settings/integrations/scope";
import useSyncCompletion, { SYNC_READ_TIMEOUT_MS } from "./useSyncCompletion";

// ---------------------------------------------------------------------------
// Query Keys
// ---------------------------------------------------------------------------

export const integrationKeys = {
  all: ["integrations"],
  // Every connection query: the list and each connection's own.
  connections: () => [...integrationKeys.all, "connections"],
  // The lists alone, so a change to a list never refetches a connection
  // that is gone (a deleted connection's page would answer 404); one list
  // per workspace page (scope.js), "current" for the current workspace's.
  lists: () => [...integrationKeys.all, "connections", "list"],
  list: (workspaceId) => [...integrationKeys.lists(), workspaceId || "current"],
  connection: (id) => [...integrationKeys.all, "connections", id],
  syncLogs: (connectionId) => [
    ...integrationKeys.all,
    "sync-logs",
    connectionId,
  ],
};

// Surfaces that consume integration state outside the integrations page (e.g.
// the error feed's Linear "Connect/Create issue" button) cache their own
// derived state. Listing them here so connection mutations can fan out
// invalidations without taking an import cycle on those packages.
const CROSS_FEATURE_INTEGRATION_KEYS = [["errorFeed", "linearTeams"]];

const invalidateCrossFeatureIntegrationCaches = (queryClient) => {
  for (const queryKey of CROSS_FEATURE_INTEGRATION_KEYS) {
    queryClient.invalidateQueries({ queryKey });
  }
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export const useIntegrationConnections = (options = {}) => {
  const workspaceId = useIntegrationsWorkspaceId();
  return useQuery({
    queryKey: integrationKeys.list(workspaceId),
    queryFn: ({ signal }) =>
      axios.get(
        endpoints.integrations.connections.list,
        scopedConfig(workspaceId, { signal, timeout: SYNC_READ_TIMEOUT_MS }),
      ),
    select: (d) => {
      const raw = d.data;
      // Backend wraps in { result: { connections: [...], metadata: {...} } }
      const result = raw?.result || raw;
      return (
        result?.connections ||
        result?.results ||
        (Array.isArray(result) ? result : [])
      );
    },
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useSlackChannels = (connectionId, options = {}) =>
  useInfiniteQuery({
    queryKey: [...integrationKeys.all, "slack-channels", connectionId],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.integrations.slack.channels(connectionId), {
        params: pageParam ? { cursor: pageParam } : undefined,
      }),
    select: (response) => ({
      ...response,
      channels: response.pages.flatMap((page) => {
        const result = page?.data?.result || page?.data || {};
        return result?.channels || [];
      }),
    }),
    getNextPageParam: (lastPage) =>
      lastPage?.data?.result?.next_cursor || undefined,
    initialPageParam: undefined,
    enabled: !!connectionId && (options.enabled ?? true),
    staleTime: 30_000,
    ...options,
  });

export const useStartSlackInstall = () => {
  const workspaceId = useIntegrationsWorkspaceId();
  return useMutation({
    mutationFn: (connectionId) =>
      axios.post(
        endpoints.integrations.slack.install,
        connectionId ? { connection_id: connectionId } : {},
        scopedConfig(workspaceId),
      ),
    // Each Slack entry point shows why it could not start, inline.
    meta: { errorHandled: true },
  });
};

export const useIntegrationConnection = (connectionId, options = {}) => {
  const workspaceId = useIntegrationsWorkspaceId();
  return useQuery({
    queryKey: integrationKeys.connection(connectionId),
    queryFn: ({ signal }) =>
      axios.get(
        endpoints.integrations.connections.detail(connectionId),
        scopedConfig(workspaceId, { signal, timeout: SYNC_READ_TIMEOUT_MS }),
      ),
    select: (d) => {
      const raw = d.data;
      const result = raw?.result || raw;
      return result;
    },
    enabled: !!connectionId,
    staleTime: 1000 * 60 * 2,
    // The connection's page shows a failed read inline (IntegrationDetail).
    meta: { errorHandled: true },
    ...options,
  });
};

export const useSyncLogs = (connectionId, options = {}) => {
  const workspaceId = useIntegrationsWorkspaceId();
  return useQuery({
    queryKey: integrationKeys.syncLogs(connectionId),
    queryFn: ({ signal }) =>
      axios.get(
        endpoints.integrations.syncLogs,
        scopedConfig(workspaceId, {
          params: { connection_id: connectionId },
          signal,
          timeout: SYNC_READ_TIMEOUT_MS,
        }),
      ),
    select: (d) => {
      const raw = d.data;
      const result = raw?.result || raw;
      return (
        result?.sync_logs ||
        result?.results ||
        (Array.isArray(result) ? result : [])
      );
    },
    enabled: !!connectionId,
    staleTime: 1000 * 30,
    // Sync History shows a failed read inline (IntegrationSyncHistory).
    meta: { errorHandled: true },
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useValidateCredentials = () => {
  const workspaceId = useIntegrationsWorkspaceId();
  return useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.integrations.validate,
        data,
        scopedConfig(workspaceId),
      ),
    // Each credentials step shows why the check failed, inline.
    meta: { errorHandled: true },
  });
};

export const useCreateConnection = () => {
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();

  return useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.integrations.connections.create,
        data,
        scopedConfig(workspaceId),
      ),
    onSuccess: () => {
      enqueueSnackbar("Integration connected successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      invalidateCrossFeatureIntegrationCaches(queryClient);
    },
    // The sync settings step (and Linear's step) shows why, inline.
    meta: { errorHandled: true },
  });
};

export const useUpdateConnection = () => {
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();

  return useMutation({
    mutationFn: ({ id, data }) =>
      axios.patch(
        endpoints.integrations.connections.update(id),
        data,
        scopedConfig(workspaceId),
      ),
    onSuccess: (_data, variables) => {
      enqueueSnackbar("Integration updated successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connection(variables.id),
      });
      invalidateCrossFeatureIntegrationCaches(queryClient);
    },
    // The edit dialog shows why the change was refused, inline.
    meta: { errorHandled: true },
  });
};

export const useDeleteConnection = () => {
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();

  return useMutation({
    mutationFn: (id) =>
      axios.delete(
        endpoints.integrations.connections.delete(id),
        scopedConfig(workspaceId),
      ),
    onSuccess: () => {
      enqueueSnackbar("Integration deleted", { variant: "success" });
      // The list only: the deleted connection's own page is left as it was
      // until the page navigates away, so it never reads a 404.
      queryClient.invalidateQueries({ queryKey: integrationKeys.lists() });
      invalidateCrossFeatureIntegrationCaches(queryClient);
    },
    onError: (error) => {
      enqueueSnackbar(getErrorMessage(error), { variant: "error" });
    },
    // Reported once, by the toast above.
    meta: { errorHandled: true },
  });
};

export const useSyncNow = (connectionId) => {
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();
  const completion = useSyncCompletion(
    queryClient,
    workspaceId,
    integrationKeys,
    connectionId,
  );

  const mutation = useMutation({
    onMutate: completion.prepare,
    mutationFn: completion.dispatch,
    // Says what was started: "Sync triggered." or, on a connection in
    // Error, "Retry started." (views/integration_connection.py sync_now).
    onSuccess: async (response, current) => {
      if (current.controller.signal.aborted) return;
      const message = response?.data?.result?.message;
      if (typeof message === "string" && message.trim()) {
        enqueueSnackbar(message, { variant: "success" });
      }
      // Keep Sync Now pending across dispatch/queue latency, until a new
      // terminal history row and the connection's final state are observed.
      await completion.complete(current.id, current);
    },
    onError: (error, current) => {
      completion.fail(current);
      if ((error?.transportCode || error?.code) !== "ERR_CANCELED") {
        enqueueSnackbar(getErrorMessage(error), { variant: "error" });
      }
    },
    // Reported once, by the toast above.
    meta: { errorHandled: true },
  });
  const mutateAsync = (id, options) =>
    completion.run(id, (current) =>
      mutation.mutateAsync(current, {
        ...options,
        // The operation is internal; callers still pass/receive a connection
        // ID and the original React Query callback context.
        onSuccess: options?.onSuccess
          ? (data, _current, ...rest) => options.onSuccess(data, id, ...rest)
          : undefined,
        onError: options?.onError
          ? (error, _current, ...rest) => options.onError(error, id, ...rest)
          : undefined,
        onSettled: options?.onSettled
          ? (data, error, _current, ...rest) =>
              options.onSettled(data, error, id, ...rest)
          : undefined,
      }),
    );
  return {
    ...mutation,
    variables: mutation.variables?.id,
    mutateAsync,
    mutate: (id, options) => {
      // Match useMutation's fire-and-forget API; onError reports failures once.
      mutateAsync(id, options).catch(() => {});
    },
    isPending: mutation.isPending || completion.status === "checking",
    completionUnconfirmed: completion.status === "unconfirmed",
    checkStatus: completion.checkStatus,
  };
};

export const usePauseConnection = () => {
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();

  return useMutation({
    mutationFn: (id) =>
      axios.post(
        endpoints.integrations.connections.pause(id),
        {},
        scopedConfig(workspaceId),
      ),
    onSuccess: (_data, id) => {
      enqueueSnackbar("Integration paused", { variant: "info" });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connection(id),
      });
    },
    onError: (error) => {
      enqueueSnackbar(getErrorMessage(error), { variant: "error" });
    },
    // Reported once, by the toast above.
    meta: { errorHandled: true },
  });
};

export const useResumeConnection = () => {
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();

  return useMutation({
    mutationFn: (id) =>
      axios.post(
        endpoints.integrations.connections.resume(id),
        {},
        scopedConfig(workspaceId),
      ),
    onSuccess: (_data, id) => {
      enqueueSnackbar("Integration resumed", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connection(id),
      });
    },
    onError: (error) => {
      enqueueSnackbar(getErrorMessage(error), { variant: "error" });
    },
    // Reported once, by the toast above.
    meta: { errorHandled: true },
  });
};
