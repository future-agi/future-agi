import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { rotateApiKey } from "../rotate";

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

// `options` as for useQuery; the keys page passes { meta: { errorHandled:
// true } } because it shows its own error state.
export function useApiKeys(gatewayId, options = {}) {
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
    ...options,
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
    // The create reply is show-once; discard it when the dialog unmounts.
    gcTime: 0,
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

// Explicit replacement command: local create, expiry and old-key revoke share
// one server transaction. The ownership marker alone never revokes a source.
// Gateway enforcement is separate and can remain pending after local commit.
export function useRotateApiKey() {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    // Unmounting must not retain the show-once reply for the default cache TTL.
    gcTime: 0,
    mutationFn: async (key) => {
      if (typeof key?.id !== "string" || !key.id) {
        throw new Error("Key rotation is not permitted");
      }
      return rotateApiKey(key, {
        replaceAtomically: async (payload) => {
          const { data } = await axios.post(
            endpoints.gateway.createApiKey,
            { ...payload, rotates_key_id: key.id, revoke_rotated_key: true },
          );
          return normalizeApiKey(data.result);
        },
      });
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });

  return {
    ...mutation,
    reset: () => {
      // Reset detaches the observer; explicitly remove this reply as well so
      // neither secret nor created.key waits in the cache for a GC timer.
      // Match the result object, never a global mutation key or another reveal.
      if (mutation.data) {
        const cache = queryClient.getMutationCache();
        cache.getAll().forEach((entry) => {
          if (entry.state.data === mutation.data) cache.remove(entry);
        });
      }
      mutation.reset();
    },
  };
}
