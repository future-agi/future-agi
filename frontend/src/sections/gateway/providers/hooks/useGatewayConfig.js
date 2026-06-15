import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export function useGatewayConfig(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-gateway-config", gatewayId],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.config(gatewayId));
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
      const { data } = await axios.get(endpoints.gateway.providers(gatewayId));
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
      const { data } = await axios.post(
        endpoints.gateway.updateProvider(gatewayId),
        {
          name,
          config,
        },
      );
      return data.result;
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
      const { data } = await axios.post(
        endpoints.gateway.providerCredentials.fetchModels,
        body,
      );
      return data.result;
    },
  });
}
