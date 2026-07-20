import { useAuthContext } from "src/auth/hooks";
import { RolePermission, PERMISSIONS } from "src/utils/rolePermissionMapping";

/**
 * Single source of truth for agent-playground write access. Mirrors the
 * backend workspace write-check (viewers get "Write access denied"), so every
 * create/update/delete/run affordance can gate on the same booleans.
 *
 * `canEditAgent` / `isReadOnly` are UPDATE-based and cover the builder's
 * run/save/edit/delete surfaces. `canCreate` / `canDelete` are exposed for the
 * list view, which distinguishes creating an agent from deleting one.
 */
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
