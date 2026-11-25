import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const QUERY_KEY = ["agentcc-guardrail-policies"];

export function useGuardrailPolicies() {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.guardrailPolicies);
      return Array.isArray(data.result)
        ? data.result
        : data.result?.results || [];
    },
    staleTime: 30000,
  });
}

function usePolicyMutation(mutationFn) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEY }),
  });
}

export function useCreateGuardrailPolicy() {
  return usePolicyMutation(async (payload) => {
    const { data } = await axios.post(
      endpoints.gateway.guardrailPolicies,
      payload,
    );
    return data.result;
  });
}

export function useUpdateGuardrailPolicy() {
  return usePolicyMutation(async ({ id, ...payload }) => {
    const { data } = await axios.patch(
      endpoints.gateway.guardrailPolicyDetail(id),
      payload,
    );
    return data.result;
  });
}

export function useDeleteGuardrailPolicy() {
  return usePolicyMutation(async (id) => {
    const { data } = await axios.delete(
      endpoints.gateway.guardrailPolicyDetail(id),
    );
    return data.result;
  });
}

export function policiesForKey(policies, keyId) {
  return (policies || []).filter(
    (policy) =>
      policy.is_active &&
      policy.scope === "key" &&
      !policy.name?.startsWith("__") &&
      policy.applied_keys?.includes(keyId),
  );
}
