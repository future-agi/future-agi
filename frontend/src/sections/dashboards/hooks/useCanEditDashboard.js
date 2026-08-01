import { useAuthContext } from "src/auth/hooks";
import { RolePermission, PERMISSIONS } from "src/utils/rolePermissionMapping";

export default function useCanEditDashboard() {
  const { role } = useAuthContext();
  const canCreate = Boolean(
    RolePermission.DASHBOARDS[PERMISSIONS.CREATE][role],
  );
  const canUpdate = Boolean(
    RolePermission.DASHBOARDS[PERMISSIONS.UPDATE][role],
  );
  const canDelete = Boolean(
    RolePermission.DASHBOARDS[PERMISSIONS.DELETE][role],
  );
  return {
    canCreate,
    canUpdate,
    canDelete,
    isReadOnly: !canUpdate,
  };
}
