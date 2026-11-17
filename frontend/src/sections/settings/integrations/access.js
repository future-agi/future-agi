import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useIntegrationsScope } from "./scope";

// Integrations belong to the organization. Adding, reconnecting, editing,
// pausing, resuming, disconnecting and removing one is open to the roles Add
// Integration is enabled for (Owner, Admin, Workspace Admin); the API grants
// the same roles (futureagi/integrations/permissions.py). Every other role
// reads the same pages with those controls disabled.
export const INTEGRATION_ADMIN_ONLY = "Owner or Admin only";

export function canManageIntegrations(role) {
  return RolePermission.API_KEYS[PERMISSIONS.CREATE][role] === true;
}

/**
 * Whether the signed-in user may manage the integrations the page shows: on a
 * workspace's own page, as its scope decides (IntegrationsWorkspaceScope);
 * elsewhere, by the current role.
 */
export function useCanManageIntegrations() {
  const { role } = useAuthContext();
  const scope = useIntegrationsScope();
  return scope ? scope.canManage : canManageIntegrations(role);
}
