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
  JEV_MODELS: "jev_models",
  PROTECT: "protect",
  VOICE_SIM: "voice_sim",
  AGENTIC_EVAL: "agentic_eval",
  SYNTHETIC_DATA: "synthetic_data",
  OPTIMIZATION: "optimization",
  // Hosted Omega products under controlled rollout (READINESS 3.13). Each reads
  // PRODUCT_NOT_ADMITTED (hidden) or PRODUCT_PAUSED until the server admits it;
  // sections/omega/shell/rollout.js turns the answer into a product status.
  OMEGA_CONSOLE: "omega_console",
  OMEGA_SANDBOXES: "omega_sandboxes",
  OMEGA_DEPLOYMENTS: "omega_deployments",
  OMEGA_STUDIO_TAB: "omega_studio_tab",
  OMEGA_HUB: "omega_hub",
  // Parts of those products, shown only when they work today
  // (omega_rollout/readiness.py; sections/omega/shell/useOmegaPart.js).
  OMEGA_PART_HUB_CLI: "omega_part_hub_cli",
  OMEGA_PART_HUB_PUBLISH: "omega_part_hub_publish",
  OMEGA_PART_DEPLOY_CLI: "omega_part_deploy_cli",
  OMEGA_PART_DEPLOYMENT_STUDIO: "omega_part_deployment_studio",
  // The parity program's hide switches (sections/omega/shell/switches.js).
  OMEGA_PART_DEPLOY_NO_DOWNTIME: "omega_part_deploy_no_downtime",
  OMEGA_PART_DEPLOY_LANGGRAPH_JS: "omega_part_deploy_langgraph_js",
  OMEGA_PART_SDK_PYTHON: "omega_part_sdk_python",
  OMEGA_PART_SDK_TYPESCRIPT: "omega_part_sdk_typescript",
  OMEGA_PART_HOSTED_SANDBOXES: "omega_part_hosted_sandboxes",
  OMEGA_PART_HOSTED_DEPLOYMENTS: "omega_part_hosted_deployments",
  INSTANT_EVAL: "instant_eval",
  INSIGHTS: "insights",
  FIX_MY_AGENT: "fix_my_agent",
  // Service states reported directly by the capabilities view.
  CUSTOM_CODE_EVALS: "custom_code_evals",
  HUGGING_FACE_IMPORT: "huggingface_import",
  DATASET_METADATA: "dataset_metadata",
});

/**
 * `staleTime` defaults to Infinity (license and plan answers change rarely).
 * The Omega product gates pass a shorter one, so a rollout change reaches an
 * open page when it mounts a gate or regains focus, on the same cached query.
 * `enabled` false reads nothing (the router passes it before sign-in, since
 * the endpoint answers only a signed-in user).
 * Every reader shows its own neutral state for a failed read (and the New
 * experiment picker retries it quietly), so a failure never toasts.
 */
export function useCapabilities({ staleTime = Infinity, enabled = true } = {}) {
  return useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: () => axios.get(apiPath("/api/capabilities/")),
    select: (res) => res.data,
    staleTime,
    retry: 1,
    enabled,
    meta: { errorHandled: true },
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
 * `isKnown` preserves whether the server supplied a boolean answer; an absent
 * entry can show neutral copy and use `refetch` instead of implying a denial.
 * `reason` is the sentence an entry carries for a person when it is off
 * (most entries carry none).
 */
export function useFeatureAllowed(featureId, { enabled = true } = {}) {
  const { data, isLoading, isError, isFetching, refetch } = useCapabilities({
    enabled,
  });
  const feature = data?.features?.[featureId];
  return {
    allowed: feature?.allowed === true,
    isKnown: typeof feature?.allowed === "boolean",
    reasonCode: feature?.reason_code ?? null,
    reason: feature?.reason ?? null,
    isLoading,
    isError,
    isFetching,
    refetch,
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
  const { allowed, reasonCode, reason, isLoading, isError } =
    useFeatureAllowed(featureId);
  return {
    locked: isLoading || !allowed,
    reasonCode,
    reason,
    isLoading,
    isError,
  };
}

/**
 * A confirmed denial: capabilities loaded, the fetch didn't fail, and the
 * feature isn't allowed. For marking an entry point that still opens (onto
 * its upgrade gate), so it never shows while loading or after a failed fetch.
 *
 *   const insightsDenied = useFeatureDenied(CAPABILITY.INSIGHTS);
 */
export function useFeatureDenied(featureId) {
  const { allowed, isLoading, isError } = useFeatureAllowed(featureId);
  return !isLoading && !isError && !allowed;
}
