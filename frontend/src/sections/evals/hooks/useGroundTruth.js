import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

// Centralized toast wrapper for the GT mutation hooks. Lives here (not
// in the component callsites) so success/error feedback fires
// regardless of the parent's render timing - particularly important on
// the first save when local state and persisted state were equal a
// tick earlier and the component is mid-resync.
const toastFromError = (err, fallback) =>
  err?.response?.data?.message ||
  err?.response?.data?.detail ||
  err?.message ||
  fallback;

// ── List ground truth datasets for a template ──
export function useGroundTruthList(templateId) {
  return useQuery({
    queryKey: ["evals", "ground-truth", templateId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getGroundTruthList(templateId),
      );
      return data?.result;
    },
    enabled: !!templateId,
  });
}

// ── Upload ground truth (file via FormData, or JSON body for dataset import) ──
export function useUploadGroundTruth(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const isFormData = payload instanceof FormData;
      const { data } = await axios.post(
        endpoints.develop.eval.uploadGroundTruth(templateId),
        payload,
        isFormData
          ? { headers: { "Content-Type": "multipart/form-data" } }
          : {},
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "ground-truth", templateId],
      });
    },
  });
}

// ── Get paginated data preview ──
export function useGroundTruthData(gtId, { page = 1, pageSize = 50 } = {}) {
  return useQuery({
    queryKey: ["evals", "ground-truth-data", gtId, page, pageSize],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.groundTruthData(gtId),
        {
          params: { page, page_size: pageSize },
        },
      );
      return data?.result;
    },
    enabled: !!gtId,
    keepPreviousData: true,
  });
}

// ── Get embedding status ──
//
// Polls every 3s whenever the embed job is still in flight - both the
// `pending` (queued, workflow not yet picked up) and `processing`
// (activity running) interim states. When the status flips to a
// terminal value (`completed` / `failed`), polling stops AND the list
// query is invalidated so the parent UI re-reads the row count and
// flips the Embed button + stale-banner off.
export function useGroundTruthStatus(gtId, { enabled = true } = {}) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["evals", "ground-truth-status", gtId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.groundTruthStatus(gtId),
      );
      const result = data?.result;
      if (
        result?.embedding_status === "completed" ||
        result?.embedding_status === "failed"
      ) {
        queryClient.invalidateQueries({
          queryKey: ["evals", "ground-truth"],
        });
      }
      return result;
    },
    enabled: !!gtId && enabled,
    refetchInterval: (query) => {
      const status = query?.state?.data?.embedding_status;
      if (status === "pending" || status === "processing") return 3000;
      return false;
    },
  });
}


// ── Atomic save of the whole GT tab (variable mapping + role mapping +
// injection config). Backs the single Save button on the FE GT tab.
// One PUT, one notification. The BE service refuses without a real
// `output` column in `role_mapping` and rejects unknown columns.
export function useSaveGroundTruthSetup(templateId) {
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  return useMutation({
    mutationFn: async ({
      gtId,
      variableMapping,
      roleMapping,
      maxExamples,
      enabled,
    }) => {
      const { data } = await axios.put(
        endpoints.develop.eval.groundTruthSetup(gtId),
        {
          variable_mapping: variableMapping,
          role_mapping: roleMapping,
          max_examples: maxExamples,
          enabled,
        },
      );
      return data?.result;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
      if (templateId) {
        // Setup writes the runtime knobs onto the gt row; invalidate the
        // template detail too so any downstream consumer refetches.
        queryClient.invalidateQueries({
          queryKey: ["evals", "detail", templateId],
        });
      }
      enqueueSnackbar("Saved", { variant: "success" });
    },
    onError: (err) =>
      enqueueSnackbar(toastFromError(err, "Failed to save"), {
        variant: "error",
      }),
  });
}

// ── Delete ground truth ──
export function useDeleteGroundTruth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (gtId) => {
      const { data } = await axios.delete(
        endpoints.develop.eval.deleteGroundTruth(gtId),
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
    },
  });
}

// ── Trigger embedding generation ──
export function useTriggerEmbedding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (gtId) => {
      const { data } = await axios.post(
        endpoints.develop.eval.groundTruthEmbed(gtId),
        {},
      );
      return data?.result;
    },
    onSuccess: (_, gtId) => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
      queryClient.invalidateQueries({
        queryKey: ["evals", "ground-truth-status", gtId],
      });
    },
  });
}

