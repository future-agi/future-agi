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

/**
 * The one sentence a submission that cannot move shows while this server runs
 * no Hub checker: on the Review page, My submission and My submissions alike.
 */
export const NO_CHECKER_STATUS =
  "The automated checks can't run on this server until an administrator starts the Hub checker.";

// While no checker runs nothing claims a check job (omega_hub/services/
// checks.py): a received or checking submission never moves, and one whose
// checks could not run cannot be run again, so the server's "in a moment" or
// "will run them again" would not come true.
const STALLED_STATUSES = new Set(["received", "checking", "check_error"]);

/**
 * True when a submission in `status` cannot move because this server runs no
 * Hub checker (`uploads` is what useUploadsChecked() answered). Never while
 * the answer is loading.
 * @param {string} uploads
 * @param {string} status
 */
export const stalledWithoutChecker = (uploads, status) =>
  uploads === "unchecked" && STALLED_STATUSES.has(status);

export default useUploadsChecked;
