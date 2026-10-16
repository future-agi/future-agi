import { useApiKeysStatus } from "src/api/model/api-keys";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import { AI_PROVIDERS_SETTINGS_PATH } from "./useInstantEval";

export const JEV_PROVIDER = "typesafe";

// Jev runs on the organization's own TypeSafe key, so it needs both the
// plan capability and a saved key under Settings -> AI Providers. Saving a
// key invalidates the key-status query, so the gate lifts without a reload.
export function useJevAvailability() {
  const capability = useFeatureAllowed(CAPABILITY.JEV_MODELS);
  const keys = useApiKeysStatus({ enabled: capability.allowed });
  const hasKey = Boolean(
    (keys.data || []).find((provider) => provider.provider === JEV_PROVIDER)
      ?.hasKey,
  );
  const isLoading =
    capability.isLoading || (capability.allowed && keys.isLoading);

  if (!capability.allowed)
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
      reason:
        "Jev runs on your own TypeSafe key. Add it in Settings → AI Providers to start.",
      settingsPath: AI_PROVIDERS_SETTINGS_PATH,
    };
  return { available: true, isLoading, reason: null };
}
