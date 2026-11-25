import { useAuthContext } from "src/auth/hooks";
import {
  PERMISSIONS,
  ROLES,
  RolePermission,
} from "src/utils/rolePermissionMapping";

// Unlocking a release is stricter than locking it: owners and admins only.
const RELEASE_ADMINS = [ROLES.OWNER, ROLES.ADMIN, ROLES.WORKSPACE_ADMIN];

export default function useSimulationAccess() {
  const { role } = useAuthContext();
  const permissions = RolePermission.SIMULATION_AGENT;
  return {
    canEdit: Boolean(permissions[PERMISSIONS.UPDATE][role]),
    canRun: Boolean(permissions[PERMISSIONS.RUN_SIMULATION_TEST][role]),
    canUnlockRelease: RELEASE_ADMINS.includes(role),
  };
}
