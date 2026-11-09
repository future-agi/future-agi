import { useCapabilities } from "src/hooks/useCapabilities";
import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import { OMEGA_PART, ROLLOUT_STALE_MS } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

/**
 * Whether this server can check uploads (OMEGA_PART HUB_PUBLISH: a Hub checker
 * is configured, omega_rollout/readiness.py): "checked", "unchecked" once the
 * answer says it cannot, and "loading" before the answer, so the Publish page
 * never flashes "not available" at a server that can check uploads.
 */
export function useUploadsChecked() {
  const checked = useOmegaPart(OMEGA_PART.HUB_PUBLISH);
  const { isLoading } = useCapabilities({
    staleTime: isOmegaConsoleEnabled() ? ROLLOUT_STALE_MS : Infinity,
  });
  if (checked) return "checked";
  return isLoading ? "loading" : "unchecked";
}

export default useUploadsChecked;
