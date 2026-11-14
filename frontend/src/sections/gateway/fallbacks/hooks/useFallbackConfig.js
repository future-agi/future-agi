import { useMemo } from "react";
import {
  useOrgConfig,
  useCreateOrgConfig,
} from "../../providers/hooks/useOrgConfig";
import { writableOrgConfig } from "../../utils/orgConfig";

/**
 * Wraps useOrgConfig to expose just the routing section
 * and a save function that merges routing changes back into the full config.
 */
export function useFallbackConfig() {
  const { data: orgConfig, isLoading, error, refetch } = useOrgConfig();
  const createMutation = useCreateOrgConfig();

  const routing = useMemo(() => orgConfig?.routing || {}, [orgConfig]);

  /**
   * Save routing changes by creating a new org config version.
   * Merges the provided routing object into the existing org config.
   */
  const saveRouting = (newRouting, description) => {
    const payload = {
      ...writableOrgConfig(orgConfig),
      routing: newRouting,
      change_description:
        description || "Updated fallback & reliability config",
    };
    return createMutation.mutateAsync(payload);
  };

  return {
    routing,
    orgConfig,
    isLoading,
    error,
    refetch,
    saveRouting,
    isSaving: createMutation.isPending,
  };
}
