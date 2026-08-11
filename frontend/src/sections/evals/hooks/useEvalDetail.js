import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import axios, { endpoints } from "src/utils/axios";

/**
 * Hook to fetch a single eval template's detail.
 */
export function useEvalDetail(templateId) {
  return useQuery({
    queryKey: ["evals", "detail", templateId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalDetail(templateId),
      );
      return data?.result;
    },
    enabled: !!templateId,
  });
}

/**
 * Hook to update an eval template.
 * Invalidates the detail + list caches on success.
 */
export function useUpdateEval(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.put(
        endpoints.develop.eval.updateEvalTemplate(templateId),
        payload,
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "detail", templateId],
      });
      queryClient.invalidateQueries({ queryKey: ["evals", "list"] });
    },
  });
}

const COPY_SUFFIX = /(_copy_\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2})+$/;

// Strip any prior _copy_<timestamp> so duplicating a copy stays flat.
function buildCopyName(sourceName) {
  const baseName = (sourceName || "eval").replace(COPY_SUFFIX, "");
  return `${baseName}_copy_${format(new Date(), "dd-MM-yyyy_HH-mm-ss")}`;
}

/** Hook to duplicate an eval template. */
export function useDuplicateEval(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (sourceName) => {
      const { data } = await axios.post(
        endpoints.develop.eval.duplicateEvalsTemplate,
        {
          eval_template_id: templateId,
          name: buildCopyName(sourceName),
        },
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evals", "list"] });
    },
  });
}
