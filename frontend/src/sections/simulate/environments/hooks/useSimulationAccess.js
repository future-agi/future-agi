import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function useSimulationAccess() {
  const { role } = useAuthContext();
  const permissions = RolePermission.SIMULATION_AGENT;
  return {
    canEdit: Boolean(permissions[PERMISSIONS.UPDATE][role]),
    canRun: Boolean(permissions[PERMISSIONS.RUN_SIMULATION_TEST][role]),
  };
}
