import { useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

import { withApiPathPrefix } from "../utils";

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

export const GATEWAY_CONFIG_QUERY_KEY = "agentcc-gateway-config";

// Returned from every config mutation's onSuccess, so the mutation (and the
// dialog that closes on it) settles only once the config has been re-read:
// otherwise Edit, clicked straight after Save, opens on the cached config from
// before the save. The other keys are refreshed without waiting.
export function refreshGatewayConfig(queryClient, ...alsoStaleKeys) {
  alsoStaleKeys.forEach((queryKey) =>
    queryClient.invalidateQueries({ queryKey }),
  );
  return queryClient.invalidateQueries({
    queryKey: [GATEWAY_CONFIG_QUERY_KEY],
  });
}

// Whether the re-read above ended in an error. `invalidateQueries` resolves
// whether or not the refetch succeeded, so a save that landed and a re-read
// that did not would otherwise be reported as an unqualified success next to an
// unattributed error toast. Callers use it to name the half that failed.
export function gatewayConfigRefreshFailed(queryClient) {
  return queryClient
    .getQueryCache()
    .findAll({ queryKey: [GATEWAY_CONFIG_QUERY_KEY] })
    .some((query) => query.state.status === "error");
}

// A config write Django stored but could not push to the gateway: it answers
// 200 with gateway_synced:false and gateway_warning. The change is saved, so
// it is not a failure, and the gateway does not run it, so it is not a
// success either.
export class SavedButNotAppliedError extends Error {
  constructor(result) {
    const warning =
      result?.gateway_warning || "the gateway did not confirm the change.";
    super(`Saved, but the gateway did not apply it: ${warning}`);
    this.name = "SavedButNotAppliedError";
    this.savedButNotApplied = true;
    // Deliberately no `result` field: the app-wide MutationCache handler
    // (utils/queryErrorHandler) toasts errors that carry one, and this
    // warning is already shown once, by the hook.
    this.response = result;
  }
}

export function isSavedButNotApplied(error) {
  return Boolean(error?.savedButNotApplied);
}

function requireGatewayApplied(result) {
  if (result?.gateway_synced === false) {
    throw new SavedButNotAppliedError(result);
  }
  return result;
}

// The caller's onError says "Failed to ..." for a request that failed; for a
// change that was saved that would be untrue, so it is skipped for this one
// error. onSuccess (and its success toast) never runs, because the mutation
// rejects. onSettled runs as usual.
function skipCallerErrorWhenSaved(options) {
  if (!options?.onError) return options;
  return {
    ...options,
    onError: (error, ...rest) =>
      isSavedButNotApplied(error) ? undefined : options.onError(error, ...rest),
  };
}

// A gateway config write. `request` returns Django's `result`; `refresh`
// re-reads what the write changed. On gateway_synced:false the hook shows
// "Saved, but the gateway did not apply it: <gateway_warning>" as a warning,
// re-reads the config (the save did land), and rejects with
// SavedButNotAppliedError, whose message the callers' dialogs show in their
// error Alert.
export function useGatewayConfigWrite(request, refresh) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (variables) =>
      requireGatewayApplied(await request(variables)),
    onSuccess: () => refresh(queryClient),
    onError: (error) => {
      if (!isSavedButNotApplied(error)) return undefined;
      enqueueSnackbar(error.message, { variant: "warning" });
      return refresh(queryClient);
    },
  });
  const { mutate: mutateOnce } = mutation;
  const mutate = useCallback(
    (variables, options) =>
      mutateOnce(variables, skipCallerErrorWhenSaved(options)),
    [mutateOnce],
  );
  return { ...mutation, mutate };
}

export function useGatewayConfig(gatewayId) {
  return useQuery({
    queryKey: [GATEWAY_CONFIG_QUERY_KEY, gatewayId],
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

const refreshWithProviderHealth = (queryClient) =>
  refreshGatewayConfig(queryClient, ["agentcc-provider-health"]);
const refreshWithOrgConfig = (queryClient) =>
  refreshGatewayConfig(queryClient, ["agentcc-org-config"]);

async function postForResult(url, body) {
  const { data } = await axios.post(url, body, REQUEST_CONFIG);
  return data.result;
}

export function useUpdateProvider() {
  return useGatewayConfigWrite(async ({ gatewayId, name, config }) => {
    try {
      return await postForResult(endpoints.gateway.updateProvider(gatewayId), {
        name,
        config,
      });
    } catch (err) {
      throw asRequestError(err, "Saving the provider");
    }
  }, refreshWithProviderHealth);
}

export function useRemoveProvider() {
  return useGatewayConfigWrite(
    ({ gatewayId, name }) =>
      postForResult(endpoints.gateway.removeProvider(gatewayId), { name }),
    refreshWithProviderHealth,
  );
}

export function useToggleGuardrail() {
  return useGatewayConfigWrite(
    ({ gatewayId, name, enabled }) =>
      postForResult(endpoints.gateway.toggleGuardrail(gatewayId), {
        name,
        enabled,
      }),
    refreshWithOrgConfig,
  );
}

export function useUpdateGuardrail() {
  return useGatewayConfigWrite(
    ({ gatewayId, name, config }) =>
      postForResult(endpoints.gateway.updateGuardrail(gatewayId), {
        name,
        config,
      }),
    refreshWithOrgConfig,
  );
}

export function useSetBudget() {
  return useGatewayConfigWrite(
    ({ gatewayId, level, config }) =>
      postForResult(endpoints.gateway.setBudget(gatewayId), { level, config }),
    (queryClient) => refreshGatewayConfig(queryClient),
  );
}

export function useRemoveBudget() {
  return useGatewayConfigWrite(
    ({ gatewayId, level }) =>
      postForResult(endpoints.gateway.removeBudget(gatewayId), { level }),
    refreshWithOrgConfig,
  );
}

export function useUpdateConfig() {
  return useGatewayConfigWrite(
    ({ gatewayId, config }) =>
      postForResult(endpoints.gateway.updateConfig(gatewayId), config),
    refreshWithProviderHealth,
  );
}

export function useReloadConfig() {
  return useGatewayConfigWrite(
    (gatewayId) => postForResult(endpoints.gateway.reload(gatewayId), {}),
    refreshWithProviderHealth,
  );
}

export function useFetchProviderModels() {
  return useMutation({
    mutationFn: async ({
      providerName,
      baseUrl,
      apiKey,
      apiFormat,
      apiPathPrefix,
    }) => {
      // Discovery has to probe the same versioned path the proxy will use, and
      // the prefix is an openai-format concept, so gate it the way saving does.
      const body = withApiPathPrefix(
        providerName
          ? { provider_name: providerName }
          : { base_url: baseUrl, api_key: apiKey, api_format: apiFormat },
        apiFormat,
        apiPathPrefix,
      );
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
