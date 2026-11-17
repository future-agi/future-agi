import React, { createContext, useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Outlet } from "react-router-dom";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

import { useManagedModelsStatus } from "../models/hooks/useManagedModels";

export const GatewayContext = createContext(null);

export function normalizeGateway(gateway) {
  if (!gateway) return null;
  return {
    ...gateway,
    baseUrl: gateway.baseUrl ?? gateway.base_url ?? "",
    providerCount: gateway.providerCount ?? gateway.provider_count ?? 0,
    modelCount: gateway.modelCount ?? gateway.model_count ?? 0,
    lastHealthCheck:
      gateway.lastHealthCheck ?? gateway.last_health_check ?? null,
    // Whether Try it and Guardrail test can run in this release. Undefined
    // when the server does not say; only false turns them off.
    delegatedPlaygroundAvailable:
      gateway.delegatedPlaygroundAvailable ??
      gateway.delegated_playground_available,
    // Whether Submit Batch Job can run in this release; the same rule.
    delegatedBatchAvailable:
      gateway.delegatedBatchAvailable ?? gateway.delegated_batch_available,
  };
}

const shouldAutoRefreshGateways = ({ state }) =>
  state.status !== "error" ||
  (Array.isArray(state.data?.result) && Boolean(state.data.result[0]?.id));

export const GatewayProvider = ({ children }) => {
  const queryClient = useQueryClient();

  const {
    data: gatewaysResponse,
    isLoading,
    isFetching: isRefreshing,
    error,
  } = useQuery({
    queryKey: ["agentcc-gateways"],
    queryFn: async ({ signal }) => {
      const res = await axiosInstance.get(endpoints.gateway.list, {
        timeout: 30000,
        signal,
      });
      return res.data;
    },
    retry: false,
    // A failed boot waits for explicit Retry, including after a tab focus,
    // reconnect or remount. A usable cached gateway keeps normal refresh
    // behavior even when its latest background read failed.
    retryOnMount: false,
    refetchOnWindowFocus: shouldAutoRefreshGateways,
    refetchOnReconnect: shouldAutoRefreshGateways,
  });

  const gateways = Array.isArray(gatewaysResponse?.result)
    ? gatewaysResponse.result.map(normalizeGateway)
    : [];

  // Single gateway — always use the first (and only) one
  const gateway = gateways[0] || null;

  // Whether this deployment offers managed models: false until the managed
  // models status says it does, so a page leaves its managed sentences out
  // while the status loads, cannot be read, or says none are offered.
  const managedStatus = useManagedModelsStatus();
  const managedOffered = managedStatus.data?.available === true;

  // The hook's fallback "oss" is not confirmation of a self-hosted deployment.
  // Keep confirmation separate so pending/failed/unknown metadata cannot turn
  // a known public endpoint into a copyable placeholder.
  const {
    isCloud,
    isConfirmed: deploymentModeConfirmed,
    refetch: retryDeploymentMode,
  } = useDeploymentMode();

  const refreshGateways = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["agentcc-gateways"] }),
    [queryClient],
  );

  const value = useMemo(
    () => ({
      // Primary — single gateway
      gateway,
      gatewayId: gateway?.id || null,
      // Backward compat
      gateways,
      // Utilities
      refreshGateways,
      isLoading,
      isRefreshing,
      error,
      managedOffered,
      hosted: isCloud,
      deploymentModeConfirmed,
      retryDeploymentMode,
    }),
    [
      gateway,
      gateways,
      refreshGateways,
      isLoading,
      isRefreshing,
      error,
      managedOffered,
      isCloud,
      deploymentModeConfirmed,
      retryDeploymentMode,
    ],
  );

  return (
    <GatewayContext.Provider value={value}>
      {children || <Outlet />}
    </GatewayContext.Provider>
  );
};

GatewayProvider.propTypes = {
  children: PropTypes.node,
};
