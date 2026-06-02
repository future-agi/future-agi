import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { getErrorMessage } from "src/sections/settings/integrations/utils";

// ---------------------------------------------------------------------------
// Query Keys
// ---------------------------------------------------------------------------

export const integrationKeys = {
  all: ["integrations"],
  connections: () => [...integrationKeys.all, "connections"],
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
const CROSS_FEATURE_INTEGRATION_KEYS = [
  ["errorFeed", "linearTeams"],
];

const invalidateCrossFeatureIntegrationCaches = (queryClient) => {
  for (const queryKey of CROSS_FEATURE_INTEGRATION_KEYS) {
    queryClient.invalidateQueries({ queryKey });
  }
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export const useIntegrationConnections = (options = {}) => {
  return useQuery({
    queryKey: integrationKeys.connections(),
    queryFn: () => axios.get(endpoints.integrations.connections.list),
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

export const useIntegrationConnection = (connectionId, options = {}) => {
  return useQuery({
    queryKey: integrationKeys.connection(connectionId),
    queryFn: () =>
      axios.get(endpoints.integrations.connections.detail(connectionId)),
    select: (d) => {
      const raw = d.data;
      const result = raw?.result || raw;
      return result;
    },
    enabled: !!connectionId,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useSyncLogs = (connectionId, options = {}) => {
  return useQuery({
    queryKey: integrationKeys.syncLogs(connectionId),
    queryFn: () =>
      axios.get(endpoints.integrations.syncLogs, {
        params: { connection_id: connectionId },
      }),
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
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useValidateCredentials = () => {
  return useMutation({
    mutationFn: (data) => axios.post(endpoints.integrations.validate, data),
    // No onError snackbar — StepCredentials shows inline Alert for validation errors
  });
};

export const useCreateConnection = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.integrations.connections.create, data),
    onSuccess: () => {
      enqueueSnackbar("Integration connected successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      invalidateCrossFeatureIntegrationCaches(queryClient);
    },
    // No onError snackbar — StepSyncSettings shows inline Alert for creation errors
  });
};

export const useUpdateConnection = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }) =>
      axios.patch(endpoints.integrations.connections.update(id), data),
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
    onError: (error) => {
      enqueueSnackbar(getErrorMessage(error), { variant: "error" });
    },
  });
};

export const useDeleteConnection = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id) =>
      axios.delete(endpoints.integrations.connections.delete(id)),
    onSuccess: () => {
      enqueueSnackbar("Integration deleted", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      invalidateCrossFeatureIntegrationCaches(queryClient);
    },
    onError: (error) => {
      enqueueSnackbar(getErrorMessage(error), { variant: "error" });
    },
  });
};

export const useSyncNow = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id) =>
      axios.post(endpoints.integrations.connections.syncNow(id)),
    onSuccess: (_data, id) => {
      enqueueSnackbar("Sync triggered", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connection(id),
      });
      queryClient.invalidateQueries({
        queryKey: integrationKeys.syncLogs(id),
      });
    },
    onError: (error) => {
      enqueueSnackbar(getErrorMessage(error), { variant: "error" });
    },
  });
};

export const usePauseConnection = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id) =>
      axios.post(endpoints.integrations.connections.pause(id)),
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
  });
};

export const useResumeConnection = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id) =>
      axios.post(endpoints.integrations.connections.resume(id)),
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
  });
};
