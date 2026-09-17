import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// The shared axios instance is created without a `timeout`, and axios defaults
// to 0 — wait forever. A stalled gateway request therefore never rejects, so
// React Query's isPending sticks on and the caller's UI is left disabled with
// no error (the Save button stuck on "Saving..."). Scope a timeout to the
// gateway admin calls rather than changing the global instance.
const GATEWAY_TIMEOUT = 30000;
const REQUEST_CONFIG = { timeout: GATEWAY_TIMEOUT };

// Listing a provider's models is a round trip through the gateway to a third
// party, so it gets a longer budget than a config write.
const FETCH_MODELS_CONFIG = { timeout: 60000 };

// axios surfaces a timeout as ECONNABORTED with "timeout of 30000ms exceeded",
// which is not something a user can act on.
export function asRequestError(err, action) {
  const timedOut =
    err?.code === "ECONNABORTED" ||
    /^timeout of \d+ms/.test(err?.message || "");
  if (!timedOut) return err;
  const friendly = new Error(
    `${action} timed out — the gateway did not respond. Check that it is reachable, then try again.`,
  );
  friendly.cause = err;
  return friendly;
}

export function useGatewayConfig(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-gateway-config", gatewayId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.config(gatewayId),
        REQUEST_CONFIG,
      );
      return data.result;
    },
    enabled: Boolean(gatewayId),
    staleTime: 60000,
  });
}

export function useProviderHealth(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-provider-health", gatewayId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.providers(gatewayId),
        REQUEST_CONFIG,
      );
      return data.result;
    },
    enabled: Boolean(gatewayId),
    staleTime: 30000,
    refetchInterval: 60000,
  });
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, name, config }) => {
      try {
        const { data } = await axios.post(
          endpoints.gateway.updateProvider(gatewayId),
          {
            name,
            config,
          },
          REQUEST_CONFIG,
        );
        return data.result;
      } catch (err) {
        throw asRequestError(err, "Saving the provider");
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-provider-health"] });
    },
  });
}

export function useRemoveProvider() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, name }) => {
      const { data } = await axios.post(
        endpoints.gateway.removeProvider(gatewayId),
        { name },
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-provider-health"] });
    },
  });
}

export function useToggleGuardrail() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, name, enabled }) => {
      const { data } = await axios.post(
        endpoints.gateway.toggleGuardrail(gatewayId),
        {
          name,
          enabled,
        },
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
    },
  });
}

export function useUpdateGuardrail() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, name, config }) => {
      const { data } = await axios.post(
        endpoints.gateway.updateGuardrail(gatewayId),
        {
          name,
          config,
        },
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
    },
  });
}

export function useSetBudget() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, level, config }) => {
      const { data } = await axios.post(
        endpoints.gateway.setBudget(gatewayId),
        {
          level,
          config,
        },
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
    },
  });
}

export function useRemoveBudget() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, level }) => {
      const { data } = await axios.post(
        endpoints.gateway.removeBudget(gatewayId),
        { level },
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
    },
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, config }) => {
      const { data } = await axios.post(
        endpoints.gateway.updateConfig(gatewayId),
        config,
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-provider-health"] });
    },
  });
}

export function useReloadConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (gatewayId) => {
      const { data } = await axios.post(
        endpoints.gateway.reload(gatewayId),
        {},
        REQUEST_CONFIG,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({ queryKey: ["agentcc-provider-health"] });
    },
  });
}

export function useFetchProviderModels() {
  return useMutation({
    mutationFn: async ({ providerName, baseUrl, apiKey, apiFormat }) => {
      const body = providerName
        ? { provider_name: providerName }
        : { base_url: baseUrl, api_key: apiKey, api_format: apiFormat };
      try {
        const { data } = await axios.post(
          endpoints.gateway.providerCredentials.fetchModels,
          body,
          FETCH_MODELS_CONFIG,
        );
        return data.result;
      } catch (err) {
        throw asRequestError(err, "Loading this provider's models");
      }
    },
  });
}
