import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import {
  postConfigPatch,
  refreshAfterConfigSave,
} from "../../hooks/useConfigPatch";

export { postConfigPatch, saveErrorMessage } from "../../hooks/useConfigPatch";

const REQUEST_CONFIG = { timeout: 30000 };

export const MANAGED_MODELS_QUERY_KEY = "agentcc-managed-models";
export const MANAGED_CATALOG_QUERY_KEY = "agentcc-managed-models-catalog";

// Whether this deployment offers managed models, whether the org opted in,
// and its prepaid credit.
export function useManagedModelsStatus() {
  return useQuery({
    queryKey: [MANAGED_MODELS_QUERY_KEY],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.managedModels,
        REQUEST_CONFIG,
      );
      return data.result;
    },
    staleTime: 30000,
  });
}

// The managed providers' model catalog with upstream and billed prices. The
// gateway re-fetches it every few hours, so a visit-long cache is enough. The
// catalog panel reports its own failure, so the app-wide toast stays off.
export function useManagedCatalog({ enabled = true } = {}) {
  return useQuery({
    queryKey: [MANAGED_CATALOG_QUERY_KEY],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.managedModelsCatalog,
        REQUEST_CONFIG,
      );
      return data.result;
    },
    enabled,
    staleTime: 5 * 60 * 1000,
    meta: { errorHandled: true },
  });
}

// Opts the org in to (or out of) managed models.
export function useSetManagedModelsEnabled() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ gatewayId, enabled }) =>
      postConfigPatch(
        gatewayId,
        { managed_models: { enabled } },
        "Saving managed models",
      ),
    onSuccess: () =>
      refreshAfterConfigSave(queryClient, [MANAGED_MODELS_QUERY_KEY]),
    meta: { errorHandled: true },
  });
}

// Saves routing.auto. `auto` is a deep-merge patch: a null value deletes that
// key, so a cleared field goes back to the gateway default.
export function useSaveAutoRouting() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ gatewayId, auto }) =>
      postConfigPatch(gatewayId, { routing: { auto } }, "Saving auto routing"),
    onSuccess: () => refreshAfterConfigSave(queryClient),
    meta: { errorHandled: true },
  });
}
