import { useAuthContext } from "src/auth/hooks";
import { RolePermission, PERMISSIONS } from "src/utils/rolePermissionMapping";

export default function useCanEditAgent() {
  const { role } = useAuthContext();
  const canCreate = Boolean(RolePermission.AGENTS[PERMISSIONS.CREATE][role]);
  const canUpdate = Boolean(RolePermission.AGENTS[PERMISSIONS.UPDATE][role]);
  const canDelete = Boolean(RolePermission.AGENTS[PERMISSIONS.DELETE][role]);
  return {
    canCreate,
    canUpdate,
    canDelete,
    canEditAgent: canUpdate,
    isReadOnly: !canUpdate,
  };
}
