import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

// Every status a console batch ends in: the gateway's completed and
// cancelled, and failed for a batch the gateway no longer holds.
export const BATCH_TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
]);

// The shared axios instance rejects with the response body spread flat
// (message, statusCode). Submitting and cancelling a batch are an Owner's or
// Admin's: the batch runs as the org and spends its credits.
function batchErrorMessage(err, verb, fallback) {
  const remedy = getGatewayAuthenticationErrorMessage(err);
  if (remedy) return remedy;
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
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-batch-jobs"] });
      // Name the job: its ID is what reopens it later (Open job by ID).
      const batchId =
        typeof result?.batch_id === "string" ? result.batch_id : "";
      enqueueSnackbar(
        batchId ? `Batch job ${batchId} submitted` : "Batch job submitted",
        { variant: "success" },
      );
    },
    onError: (err) => {
      enqueueSnackbar(
        batchErrorMessage(err, "submit", "Failed to submit batch"),
        { variant: "error" },
      );
    },
  });
}

export function useBatchStatus(gatewayId, batchId, { poll = true } = {}) {
  return useQuery({
    queryKey: ["agentcc-batch-status", gatewayId, batchId],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.batch.get(gatewayId), {
        params: { batch_id: batchId },
      });
      return data.result;
    },
    enabled: Boolean(gatewayId) && Boolean(batchId),
    // A failed lookup waits for an explicit Retry, including after focus,
    // reconnect or reopening the detail. Never replay submit/cancel here.
    retry: false,
    retryOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: (query) => query.state.status !== "error",
    // Do not inherit previous-job placeholder data from query defaults.
    placeholderData: () => undefined,
    refetchInterval: (query) => {
      const batch = query.state.data;
      if (
        !poll ||
        query.state.status === "error" ||
        batch?.outcome === "unknown" ||
        BATCH_TERMINAL_STATUSES.has(batch?.status) ||
        !["pending", "processing"].includes(batch?.status)
      ) {
        return false;
      }
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
