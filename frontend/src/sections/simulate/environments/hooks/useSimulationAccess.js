import { useAuthContext } from "src/auth/hooks";
import {
  PERMISSIONS,
  ROLES,
  RolePermission,
} from "src/utils/rolePermissionMapping";

// Unlocking a version is stricter than locking it: owners and admins only.
const UNLOCK_ADMINS = [ROLES.OWNER, ROLES.ADMIN, ROLES.WORKSPACE_ADMIN];

export default function useSimulationAccess() {
  const { role } = useAuthContext();
  const permissions = RolePermission.SIMULATION_AGENT;
  return {
    canEdit: Boolean(permissions[PERMISSIONS.UPDATE][role]),
    canRun: Boolean(permissions[PERMISSIONS.RUN_SIMULATION_TEST][role]),
    canUnlockVersion: UNLOCK_ADMINS.includes(role),
  };
}
