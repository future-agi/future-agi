import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const QUERY_KEY = "agentcc-api-keys";

export function normalizeApiKey(key) {
  if (!key) return key;

  return {
    ...key,
    gatewayKeyId: key.gatewayKeyId ?? key.gateway_key_id ?? "",
    keyPrefix: key.keyPrefix ?? key.key_prefix ?? "",
    allowedModels: key.allowedModels ?? key.allowed_models ?? [],
    allowedProviders: key.allowedProviders ?? key.allowed_providers ?? [],
    createdAt: key.createdAt ?? key.created_at ?? null,
    updatedAt: key.updatedAt ?? key.updated_at ?? null,
    lastUsedAt: key.lastUsedAt ?? key.last_used_at ?? null,
    expiresAt: key.expiresAt ?? key.expires_at ?? null,
  };
}

export function useApiKeys(gatewayId) {
  const params = {};
  if (gatewayId) params.gateway_id = gatewayId;

  return useQuery({
    queryKey: [QUERY_KEY, gatewayId],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.apiKeys, { params });
      return (data.result || []).map(normalizeApiKey);
    },
    enabled: Boolean(gatewayId),
    placeholderData: keepPreviousData,
    staleTime: 30000,
  });
}

export function useApiKeyDetail(keyId) {
  return useQuery({
    queryKey: [QUERY_KEY, "detail", keyId],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.apiKeyDetail(keyId));
      return normalizeApiKey(data.result);
    },
    enabled: Boolean(keyId),
    staleTime: 15000,
  });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.post(
        endpoints.gateway.createApiKey,
        payload,
      );
      return normalizeApiKey(data.result);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}

export function useUpdateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ keyId, ...payload }) => {
      const { data } = await axios.patch(
        endpoints.gateway.updateApiKey(keyId),
        payload,
      );
      return normalizeApiKey(data.result);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (keyId) => {
      const { data } = await axios.post(endpoints.gateway.revokeApiKey(keyId));
      return normalizeApiKey(data.result);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}

export function useSyncApiKeys() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (gatewayId) => {
      const { data } = await axios.post(endpoints.gateway.syncApiKeys, {
        gateway_id: gatewayId,
      });
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}
