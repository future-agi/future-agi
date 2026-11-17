import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { creditKeys } from "src/sections/settings/BillingV2/gateway-credits/api";

import {
  postConfigPatch,
  refreshAfterConfigSave,
} from "../../hooks/useConfigPatch";

import { SavedButNotAppliedError } from "../../providers/hooks/useGatewayConfig";
import { draftFromAuto } from "../autoRouting";

export { postConfigPatch, saveErrorMessage } from "../../hooks/useConfigPatch";

export const autoApplyReceiptKey = (gatewayId, scope = null) => ["gateway-auto-apply-receipt", gatewayId, scope];
export const autoApplyWarning = (result) => {
  if (result?.gateway_synced === true) return "";
  return result?.gateway_synced === false
    ? new SavedButNotAppliedError(result).message
    : "Saved, but gateway apply was not confirmed. Retry apply to confirm the live routing settings.";
};

const REQUEST_CONFIG = { timeout: 30000 };

export const MANAGED_MODELS_QUERY_KEY = "agentcc-managed-models";
export const MANAGED_CATALOG_QUERY_KEY = "agentcc-managed-models-catalog";

// A successful enablement change supersedes retained status even when no
// catalog reader is mounted. Tie the generation to the Query so it expires
// with that cache entry and cannot affect another QueryClient.
const managedReadVersions = new WeakMap();
export const managedModelsReadVersion = (query) =>
  managedReadVersions.get(query) ?? 0;

// Whether this deployment offers managed models, whether the org opted in,
// and its prepaid credit. The Models pages report a failed read themselves
// (one error with Retry), so the app-wide toast stays off. `enabled: false`
// skips the read (the rail reads it on AI Gateway pages only).
export function useManagedModelsStatus({ enabled = true } = {}) {
  return useQuery({
    enabled,
    queryKey: [MANAGED_MODELS_QUERY_KEY],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.managedModels,
        REQUEST_CONFIG,
      );
      return data.result;
    },
    staleTime: 30000,
    meta: { errorHandled: true },
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
    onSuccess: (_result, { enabled }) => {
      const query = queryClient.getQueryCache().find({
        queryKey: [MANAGED_MODELS_QUERY_KEY],
        exact: true,
      });
      if (query) {
        managedReadVersions.set(query, managedModelsReadVersion(query) + 1);
      }
      // A saved disable must close cached managed offers and wallets even
      // when inactive or when their refresh fails. Preserve other fields;
      // do not invent status or enabled credit data when opting in.
      if (enabled === false) {
        queryClient.setQueryData([MANAGED_MODELS_QUERY_KEY], (previous) =>
          previous ? { ...previous, enabled: false } : previous,
        );
        queryClient.setQueryData(creditKeys.summary, (previous) =>
          previous?.data?.result
            ? {
                ...previous,
                data: {
                  ...previous.data,
                  result: { ...previous.data.result, enabled: false },
                },
              }
            : previous,
        );
      }
      return refreshAfterConfigSave(
        queryClient,
        [MANAGED_MODELS_QUERY_KEY],
        creditKeys.all,
      );
    },
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
    onSuccess: (result, { gatewayId, auto, receiptScope }) => {
      const warning = autoApplyWarning(result);
      // Hook-level callbacks still run if the settings page unmounts while
      // saving; keep the outcome with the gateway whose patch was submitted.
      const key = autoApplyReceiptKey(gatewayId, receiptScope);
      queryClient.setQueryDefaults(key, { gcTime: Infinity });
      queryClient.setQueryData(key, warning
        ? { auto, draft: draftFromAuto(auto), warning }
        : null);
      return refreshAfterConfigSave(queryClient);
    },
    // The browser gives up at 30 s, the server does not: a save that timed
    // out or failed can still have been stored. Re-read the config so the
    // page shows what is stored (a stored change clears "Unsaved changes",
    // edits that were not stored stay). Not returned, so the caller's error
    // toast is not held back until the re-read answers.
    onError: () => {
      refreshAfterConfigSave(queryClient);
    },
    meta: { errorHandled: true },
  });
}
