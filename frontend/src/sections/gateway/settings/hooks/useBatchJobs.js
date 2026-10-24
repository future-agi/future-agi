import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

// Every status a console batch ends in: the gateway's completed and
// cancelled, and failed for a batch the gateway does not hold.
export const BATCH_TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
]);

// The shared axios instance rejects with the response body spread flat
// (message, statusCode). Submitting and cancelling a batch are an Owner's or
// Admin's: the batch runs as the org and spends its credits.
function batchErrorMessage(err, verb, fallback) {
  if (err?.statusCode === 403) {
    return `Only an organization Owner or Admin can ${verb} batch jobs.`;
  }
  return err?.message || err?.response?.data?.message || fallback;
}

export function useSubmitBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, requests, maxConcurrency = 5 }) => {
      const { data } = await axios.post(
        endpoints.gateway.batch.submit(gatewayId),
        { requests, max_concurrency: maxConcurrency },
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-batch-jobs"] });
      enqueueSnackbar("Batch job submitted", { variant: "success" });
    },
    onError: (err) => {
      enqueueSnackbar(
        batchErrorMessage(err, "submit", "Failed to submit batch"),
        { variant: "error" },
      );
    },
  });
}

export function useBatchStatus(gatewayId, batchId) {
  return useQuery({
    queryKey: ["agentcc-batch-status", gatewayId, batchId],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.batch.get(gatewayId), {
        params: { batch_id: batchId },
      });
      return data.result;
    },
    enabled: Boolean(gatewayId) && Boolean(batchId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (BATCH_TERMINAL_STATUSES.has(status)) return false;
      return 3000;
    },
    staleTime: 2000,
  });
}

export function useCancelBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, batchId }) => {
      const { data } = await axios.post(
        endpoints.gateway.batch.cancel(gatewayId),
        { batch_id: batchId },
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-batch-status"] });
      enqueueSnackbar("Batch job cancelled", { variant: "info" });
    },
    onError: (err) => {
      enqueueSnackbar(
        batchErrorMessage(err, "cancel", "Failed to cancel batch"),
        { variant: "error" },
      );
    },
  });
}
