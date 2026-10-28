import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

// Said wherever a Viewer would start or change a run: the server refuses
// them every Instant eval start and control and every Insights run, draft,
// preview, question and edit, and lets them read every result.
export const VIEWER_CANT_RUN = "Viewers can read results but can't start runs.";

/**
 * Whether this person's workspace role lets them start Instant eval runs and
 * Insights reports, decided as Tasks decides who may add an eval task. A
 * role not known yet holds nothing back: the server has the last word.
 */
export function useCanStartRuns() {
  const { role } = useSafeAuthContext();
  if (!role) return true;
  return Boolean(
    RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role],
  );
}
