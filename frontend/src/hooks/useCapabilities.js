import { useQuery } from "@tanstack/react-query";
import { apiPath } from "src/api/contracts/api-surface";
import axios from "src/utils/axios";

export const CAPABILITIES_QUERY_KEY = ["capabilities"];

/**
 * Canonical feature ids, mirrored from the backend capability registry
 * (`tfc/capabilities/registry.py`). Use these instead of bare string literals
 * so a typo is a build/lint error, not a feature that silently locks forever.
 */
export const CAPABILITY = Object.freeze({
  ERROR_FEED: "error_feed",
  FALCON_AI: "falcon_ai",
  TURING_MODELS: "turing_models",
  PROTECT: "protect",
  VOICE_SIM: "voice_sim",
  AGENTIC_EVAL: "agentic_eval",
  SYNTHETIC_DATA: "synthetic_data",
  OPTIMIZATION: "optimization",
});

export function useCapabilities() {
  return useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: () => axios.get(apiPath("/api/capabilities/")),
    select: (res) => res.data,
    staleTime: Infinity,
    retry: 1,
  });
}

/**
 * Is a single capability allowed for this deployment/org?
 * Backed by the same cached /api/capabilities/ query.
 *
 *   const { allowed, isLoading, isError } = useFeatureAllowed(CAPABILITY.TURING_MODELS);
 *
 * `isError` distinguishes a transient /api/capabilities/ failure from a real
 * denial so surfaces can show a neutral/retry state instead of an upsell.
 */
export function useFeatureAllowed(featureId) {
  const { data, isLoading, isError } = useCapabilities();
  return {
    allowed: data?.features?.[featureId]?.allowed === true,
    reasonCode: data?.features?.[featureId]?.reason_code ?? null,
    isLoading,
    isError,
  };
}

/**
 * Fail-closed "locked" view of a capability for gating UI.
 *
 *   const { locked, isLoading, isError } = useFeatureLocked(CAPABILITY.TURING_MODELS);
 *
 * `locked` is true while capabilities are still loading (fail closed — never
 * flash an entitled control during the fetch) and true on denial. Read
 * `isLoading`/`isError` to render a skeleton or neutral/retry state instead of
 * upsell copy when the answer isn't a real denial.
 */
export function useFeatureLocked(featureId) {
  const { allowed, reasonCode, isLoading, isError } = useFeatureAllowed(featureId);
  return {
    locked: isLoading || !allowed,
    reasonCode,
    isLoading,
    isError,
  };
}
