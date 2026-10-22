import { useMemo } from "react";
import { pick } from "lodash";
import { useOrgConfig } from "../../providers/hooks/useOrgConfig";
import { useGatewayContext } from "../../context/useGatewayContext";
import { buildConfigPatch, useConfigPatch } from "../../hooks/useConfigPatch";

// The routing keys the Fallbacks page edits. Other routing keys (strategy,
// auto, policies) belong to other pages; leaving them out of the patch keeps a
// draft opened before another page saved from writing back the old values.
const FALLBACK_ROUTING_KEYS = [
  "fallback_enabled",
  "default_model",
  "model_fallbacks",
  "failover",
  "retry",
  "circuit_breaker",
  "model_timeouts",
];

/**
 * Wraps useOrgConfig to expose just the routing section and a save function
 * that writes only the fallback and reliability keys the draft changed.
 */
export function useFallbackConfig() {
  const { data: orgConfig, isLoading, error, refetch } = useOrgConfig();
  const { gatewayId } = useGatewayContext();
  const patchMutation = useConfigPatch({
    action: "Saving fallback config",
    successMessage: "Fallback config saved",
  });

  const routing = useMemo(() => orgConfig?.routing || {}, [orgConfig]);

  const saveRouting = (newRouting) =>
    patchMutation.mutateAsync({
      gatewayId,
      patch: buildConfigPatch(
        { routing: pick(routing, FALLBACK_ROUTING_KEYS) },
        { routing: pick(newRouting, FALLBACK_ROUTING_KEYS) },
        ["routing"],
      ),
    });

  return {
    routing,
    orgConfig,
    isLoading,
    error,
    refetch,
    saveRouting,
    isSaving: patchMutation.isPending,
  };
}
