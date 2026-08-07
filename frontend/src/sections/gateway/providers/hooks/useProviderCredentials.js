import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Fetch the list of provider credentials for the current org.
 */
export function useProviderCredentials() {
  return useQuery({
    queryKey: ["agentcc-provider-credentials"],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.providerCredentials.list,
      );
      // Handle both paginated and non-paginated responses
      const result = data.result;
      return Array.isArray(result) ? result : result?.results || [];
    },
    staleTime: 30000,
  });
}

/**
 * Create a new provider credential (encrypted key + config).
 * The backend auto-pushes to the gateway after creation.
 */
export function useCreateProviderCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.post(
        endpoints.gateway.providerCredentials.create,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-credentials"],
      });
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-health"],
      });
    },
  });
}

/**
 * Update an existing provider credential's non-sensitive config.
 */
export function useUpdateProviderCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, payload }) => {
      const { data } = await axios.patch(
        endpoints.gateway.providerCredentials.update(id),
        payload,
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-credentials"],
      });
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-health"],
      });
    },
  });
}

/**
 * Soft-delete a provider credential.
 */
export function useDeleteProviderCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.delete(
        endpoints.gateway.providerCredentials.delete(id),
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-credentials"],
      });
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-health"],
      });
    },
  });
}

/**
 * Rotate (replace) the encrypted credentials for an existing provider.
 */
export function useRotateProviderCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, credentials }) => {
      const { data } = await axios.post(
        endpoints.gateway.providerCredentials.rotate(id),
        { credentials },
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agentcc-provider-credentials"],
      });
    },
  });
}
