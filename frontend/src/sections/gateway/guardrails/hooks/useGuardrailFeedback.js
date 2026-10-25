import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const GF_KEY = "agentcc-guardrail-feedback";

export function useGuardrailFeedback(params = {}, options = {}) {
  return useQuery({
    queryKey: [GF_KEY, params],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.guardrailFeedback.list,
        { params },
      );
      return data.result || [];
    },
    staleTime: 30000,
    placeholderData: keepPreviousData,
    ...options,
  });
}

// The signed-in reviewer's own answer on one check of one request: the API
// keeps one per (request, check, reviewer); of two rows for one answer, the
// latest is the answer.
export function useMyGuardrailFeedback(requestLogId, checkName) {
  const query = useGuardrailFeedback(
    { request_log_id: requestLogId, check_name: checkName, mine: "true" },
    {
      enabled: Boolean(requestLogId && checkName),
      placeholderData: undefined,
    },
  );
  const rows = Array.isArray(query.data) ? query.data : [];
  const latest = rows.reduce(
    (best, row) =>
      !best ||
      String(row.updated_at || row.created_at || "") >
        String(best.updated_at || best.created_at || "")
        ? row
        : best,
    null,
  );
  return { ...query, answer: latest };
}

export function useGuardrailFeedbackSummary(params = {}) {
  return useQuery({
    queryKey: [GF_KEY, "summary", params],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.guardrailFeedback.summary,
        { params },
      );
      return data.result || [];
    },
    staleTime: 60000,
  });
}

// Changes a saved answer (its feedback and comment) in place.
export function useUpdateGuardrailFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, feedback, comment }) => {
      const { data } = await axios.patch(
        endpoints.gateway.guardrailFeedback.detail(id),
        { feedback, comment },
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [GF_KEY] }),
  });
}

export function useCreateGuardrailFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.post(
        endpoints.gateway.guardrailFeedback.create,
        payload,
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [GF_KEY] }),
  });
}
