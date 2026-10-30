import { useApiKeysStatus } from "src/api/model/api-keys";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { AI_PROVIDERS_SETTINGS_PATH, JEV_KEY_NEEDED } from "./useInstantEval";

export const JEV_PROVIDER = "typesafe";

// Jev runs on the organization's own TypeSafe key, so it needs a saved key
// under Settings -> AI Providers and, on Cloud, the plan capability. Jev is
// in every self-hosted edition (Community included), so there only the key
// counts and the plan message never shows. Saving a key invalidates the
// key-status query, so the gate lifts without a reload.
export function useJevAvailability() {
  const deployment = useDeploymentMode();
  const capability = useFeatureAllowed(CAPABILITY.JEV_MODELS);
  const selfHosted = deployment.isSuccess && !deployment.isCloud;
  const allowed = selfHosted || capability.allowed;
  const keys = useApiKeysStatus({ enabled: allowed });
  const hasKey = Boolean(
    (keys.data || []).find((provider) => provider.provider === JEV_PROVIDER)
      ?.hasKey,
  );
  const isLoading =
    deployment.isLoading ||
    (!selfHosted && capability.isLoading) ||
    (allowed && keys.isLoading);

  if (!allowed)
    return {
      available: false,
      isLoading,
      reason:
        "Jev isn't available in this workspace yet. Ask your Future AGI admin to set it up.",
    };
  if (!hasKey)
    return {
      available: false,
      isLoading,
      reason: JEV_KEY_NEEDED,
      settingsPath: AI_PROVIDERS_SETTINGS_PATH,
    };
  return { available: true, isLoading, reason: null };
}
