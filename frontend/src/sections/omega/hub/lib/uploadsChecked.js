import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useCapabilities } from "src/hooks/useCapabilities";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
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
 * The one sentence a submission that cannot move shows while this server
 * runs no Hub checker, where an administrator of the server can start one:
 * on the Review page, My submission and My submissions of a self-hosted
 * server (noCheckerStatus).
 */
export const NO_CHECKER_STATUS =
  "The automated checks can't run on this server until an administrator starts the Hub checker.";

/**
 * The same submission's sentence on Cloud, and while the server's mode is
 * not known: why it waits, with no one to ask. The Review page imports these
 * words for its note beside a recheck that cannot run, on any server.
 */
export const NO_CHECKER_RUNNING =
  "The checks can't run on this server because no Hub checker is running.";

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

/** An organization owner or admin (accounts/omega_session.py maps them to Omega owner and admin). */
export const isAdminRole = (role) => role === "Owner" || role === "Admin";

/**
 * Who reads which words while uploads are off. On a server confirmed
 * self-hosted, an organization owner or admin reads the setup steps
 * (`admin`) and anyone else is asked to send their administrator
 * (`member`). On Cloud, uploads are off by design, and while the server's
 * mode is not known useDeploymentMode() stands in "oss" for it (while it
 * loads, or after its answer failed): there everyone reads that uploads are
 * off and nothing more (`everyone`), so no one is sent to an administrator,
 * or shown the steps, on a guess.
 */
export const UPLOADS_OFF_AUDIENCE = Object.freeze({
  admin: "admin",
  member: "member",
  everyone: "everyone",
});

/**
 * The audience for a person with organization `role` on a server whose mode
 * useDeploymentMode() answered as `deployment`. The one place that decides
 * it, so the Publish page, My submissions, a submission's page and the
 * Review page agree.
 * @param {{isConfirmed?: boolean, isCloud?: boolean} | undefined} deployment
 * @param {string} [role]
 */
export function uploadsOffAudience(deployment, role) {
  const selfHosted = Boolean(deployment?.isConfirmed) && !deployment.isCloud;
  if (!selfHosted) return UPLOADS_OFF_AUDIENCE.everyone;
  return isAdminRole(role)
    ? UPLOADS_OFF_AUDIENCE.admin
    : UPLOADS_OFF_AUDIENCE.member;
}

/** True where an administrator of this server starts the Hub checker. */
export const selfHostedAudience = (audience) =>
  audience === UPLOADS_OFF_AUDIENCE.admin ||
  audience === UPLOADS_OFF_AUDIENCE.member;

/** The sentence a submission that cannot move shows to `audience`. */
export const noCheckerStatus = (audience) =>
  selfHostedAudience(audience) ? NO_CHECKER_STATUS : NO_CHECKER_RUNNING;

/** The audience for the person signed in, on this server. */
export function useUploadsOffAudience() {
  const deployment = useDeploymentMode();
  const { role } = useSafeAuthContext();
  return uploadsOffAudience(deployment, role);
}

export default useUploadsChecked;
