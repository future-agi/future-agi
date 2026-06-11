import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

// Centralized toast wrapper for the GT mutation hooks. Lives here (not
// in the component callsites) so success/error feedback fires
// regardless of the parent's render timing — particularly important on
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
// Polls every 3s whenever the embed job is still in flight — both the
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

// ── Get ground truth config for template ──
export function useGroundTruthConfig(templateId) {
  return useQuery({
    queryKey: ["evals", "ground-truth-config", templateId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getGroundTruthConfig(templateId),
      );
      return data?.result?.ground_truth;
    },
    enabled: !!templateId,
  });
}

// ── Update ground truth config ──
export function useUpdateGroundTruthConfig(templateId) {
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  return useMutation({
    mutationFn: async (config) => {
      const { data } = await axios.put(
        endpoints.develop.eval.updateGroundTruthConfig(templateId),
        config,
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "ground-truth-config", templateId],
      });
      enqueueSnackbar("Config saved", { variant: "success" });
    },
    onError: (err) =>
      enqueueSnackbar(toastFromError(err, "Failed to save config"), {
        variant: "error",
      }),
  });
}

// ── Update role mapping ──
export function useUpdateRoleMapping() {
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  return useMutation({
    mutationFn: async ({ gtId, roleMapping }) => {
      const { data } = await axios.put(
        endpoints.develop.eval.groundTruthRoleMapping(gtId),
        {
          role_mapping: roleMapping,
        },
      );
      return data?.result;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
      enqueueSnackbar(
        result?.embeddings_stale
          ? "Role mapping saved. Embeddings are stale — re-embed when ready."
          : "Role mapping saved",
        { variant: result?.embeddings_stale ? "warning" : "success" },
      );
    },
    onError: (err) =>
      enqueueSnackbar(toastFromError(err, "Failed to save mapping"), {
        variant: "error",
      }),
  });
}

// ── Update variable mapping ──
export function useUpdateVariableMapping() {
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  return useMutation({
    mutationFn: async ({ gtId, variableMapping }) => {
      const { data } = await axios.put(
        endpoints.develop.eval.groundTruthMapping(gtId),
        {
          variable_mapping: variableMapping,
        },
      );
      return data?.result;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
      enqueueSnackbar(
        result?.embeddings_stale
          ? "Variable mapping saved. Embeddings are stale — re-embed when ready."
          : "Variable mapping saved",
        { variant: result?.embeddings_stale ? "warning" : "success" },
      );
    },
    onError: (err) =>
      enqueueSnackbar(toastFromError(err, "Failed to save mapping"), {
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

// ── Search ground truth (test retrieval) ──
// Accepts either `inputs` (multi-variable dict — preferred) or a legacy
// single `query` string. Mirrors the runtime path so the test reflects
// what the eval pipeline will see.
export function useSearchGroundTruth() {
  return useMutation({
    mutationFn: async ({
      gtId,
      inputs,
      query,
      maxResults = 3,
      similarityThreshold = 0,
    }) => {
      const payload = {
        max_results: maxResults,
        similarity_threshold: similarityThreshold,
      };
      if (inputs && typeof inputs === "object" && Object.keys(inputs).length) {
        payload.inputs = inputs;
      }
      if (typeof query === "string" && query.trim()) {
        payload.query = query;
      }
      const { data } = await axios.post(
        endpoints.develop.eval.groundTruthSearch(gtId),
        payload,
      );
      return data?.result;
    },
  });
}

// ── Validate a candidate eval-output value against the template's output type ──
export function useValidateGroundTruthOutput(templateId) {
  return useMutation({
    mutationFn: async ({ value }) => {
      const { data } = await axios.post(
        endpoints.develop.eval.groundTruthValidateOutput(templateId),
        { value },
      );
      return data?.result;
    },
  });
}
