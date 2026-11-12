import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useOrganization } from "src/contexts/OrganizationContext";
import { useGatewayContext } from "../../context/useGatewayContext";
import { useProviderHealth } from "../../providers/hooks/useGatewayConfig";
import { buildCatalogRows } from "../catalogRows";
import {
  MANAGED_CATALOG_QUERY_KEY,
  MANAGED_MODELS_QUERY_KEY,
  managedModelsReadVersion,
  useManagedCatalog,
  useManagedModelsStatus,
} from "./useManagedModels";
import { useVendorLogos } from "./useVendorLogos";

const validStatus = (data) => typeof data?.available === "boolean";
const validCatalog = (data) => Array.isArray(data?.models);
const validProviders = (data) => Array.isArray(data?.providers);
const INCOMPLETE_READ = new Error(
  "The gateway returned an incomplete model list or status. Try again.",
);

// Legacy query keys are not all gateway/org scoped. Remember only facts this
// hook validated for a scope, not arbitrary query.data. Keying by the actual
// Query keeps provenance across remounts but drops it on cache removal/GC and
// isolates QueryClients. Each scope retains its own last good read.
const retainedReads = new WeakMap();

function useRetainedRead(query, queryKey, scope, valid, enabled = true) {
  const client = useQueryClient();
  const cache = client.getQueryCache().find({ queryKey, exact: true });
  const version = managedModelsReadVersion(cache);
  const retained = retainedReads.get(cache)?.get(scope);
  const retainedData = retained?.version === version ? retained.data : undefined;
  // Revalidate legacy data on remount even when we can display proven facts.
  // A failed read keeps those facts, unless a successful mutation superseded
  // them. Never replace them with unvalidated data from another legacy scope.
  const [saved, setSaved] = useState(() => ({
    scope,
    version,
    confirmed: query.data === undefined,
    data: retainedData,
  }));
  let current = saved;
  if (saved.scope !== scope || saved.version !== version) {
    current = { scope, version, confirmed: false, data: retainedData };
    setSaved(current);
  } else if (
    saved.confirmed &&
    valid(query.data) &&
    saved.data !== query.data
  ) {
    current = { ...saved, data: query.data };
    setSaved(current);
  }

  const { refetch: read } = query;
  const refetch = useCallback(
    async (...args) => {
      setSaved((previous) =>
        previous.scope === scope && previous.version === version
          ? { ...previous, checking: true, error: null }
          : previous,
      );
      const result = await read(...args);
      const error =
        result.error || (!valid(result.data) ? INCOMPLETE_READ : null);
      setSaved((previous) => {
        if (previous.scope !== scope || previous.version !== version) {
          return previous;
        }
        return error
          ? { ...previous, checking: false, error }
          : {
              scope,
              version,
              confirmed: true,
              data: result.data,
              checking: false,
            };
      });
      return result;
    },
    [read, scope, valid, version],
  );

  useEffect(() => {
    if (current.confirmed && valid(current.data)) {
      const scopes = retainedReads.get(cache) ?? new Map();
      scopes.set(scope, { version, data: current.data });
      retainedReads.set(cache, scopes);
    }
  }, [cache, scope, version, current.confirmed, current.data, valid]);

  useEffect(() => {
    if (enabled && !current.confirmed) void refetch();
  }, [enabled, current.confirmed, refetch]);

  const data = enabled ? current.data : undefined;
  const error = !enabled
    ? null
    : !current.confirmed
      ? current.error
      : query.error ||
        (query.isSuccess && !valid(query.data) ? INCOMPLETE_READ : null);
  return {
    ...query,
    data,
    error,
    refetch,
    isError: Boolean(error),
    isSuccess: Boolean(data) && !error,
    isPending: !data && !error,
    isLoading: !data && !error,
    isFetching: current.checking || query.isFetching,
  };
}

export function useModelReadScope(gatewayId) {
  const { currentOrganizationId } = useOrganization();
  return JSON.stringify([currentOrganizationId ?? null, gatewayId ?? null]);
}

// Shared by the catalog and Your models, without making the own-provider index
// fetch a managed catalog, credits or logos.
export function useModelProviders(gatewayId, scope) {
  return useRetainedRead(
    useProviderHealth(gatewayId),
    ["agentcc-provider-health", gatewayId],
    scope,
    validProviders,
    Boolean(gatewayId),
  );
}

export const providerReadState = (providers) =>
  providers.error
    ? "error"
    : validProviders(providers.data)
      ? "ready"
      : "pending";

// Everything the Models pages read: managed status, the managed catalog (only
// where the deployment offers one), the org's own providers, and the catalog
// as rows with this org's access and prices.
export function useCatalogRows() {
  const {
    gateway,
    gatewayId,
    hosted,
    deploymentModeConfirmed,
    retryDeploymentMode,
  } = useGatewayContext();
  const scope = useModelReadScope(gatewayId);
  const status = useRetainedRead(
    useManagedModelsStatus(),
    [MANAGED_MODELS_QUERY_KEY],
    scope,
    validStatus,
    Boolean(gatewayId),
  );
  const offered = status.data?.available !== false;
  const catalog = useRetainedRead(
    useManagedCatalog({
      enabled: Boolean(status.data) && offered,
    }),
    [MANAGED_CATALOG_QUERY_KEY],
    scope,
    validCatalog,
    Boolean(gatewayId) && Boolean(status.data) && offered,
  );
  const providers = useModelProviders(gatewayId, scope);
  const providerAccessState = providerReadState(providers);
  const logos = useVendorLogos();

  const providerList = providers.data?.providers;
  const rows = useMemo(
    () =>
      catalog.data
        ? buildCatalogRows(catalog.data.models, {
            providers: providerList,
            providerAccessState,
            managedStatus: status.data,
            logos,
          })
        : undefined,
    [catalog.data, providerList, providerAccessState, status.data, logos],
  );

  return {
    gateway,
    gatewayId,
    baseUrl: gateway?.baseUrl,
    hosted,
    deploymentModeConfirmed,
    retryDeploymentMode,
    status,
    catalog,
    providers,
    providerAccessState,
    rows,
    offered,
    markupPercent: catalog.data?.markup_percent ?? status.data?.markup_percent,
    isLoading: status.isLoading || (offered && catalog.isLoading),
  };
}
