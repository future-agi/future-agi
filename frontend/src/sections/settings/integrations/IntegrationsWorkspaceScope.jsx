import { useMemo } from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspaceFromList } from "src/api/workspaces/list";
import { ROLES } from "src/utils/rolePermissionMapping";
import { IntegrationsScopeContext } from "./scope";

// Workspace levels (futureagi/tfc/constants/levels.py): Admin 8.
const WORKSPACE_ADMIN_LEVEL = 8;

// Scopes the integrations pages under it to the workspace in the URL
// (scope.js). Its integrations are managed, as the API decides for the
// workspace the requests name (futureagi/integrations/permissions.py), by an
// organization Owner or Admin and by an Admin of that workspace.
export default function IntegrationsWorkspaceScope({ children }) {
  const { workspaceId } = useParams();
  const { user } = useAuthContext();
  const orgRole = user?.organization_role;
  const orgAdmin = orgRole === ROLES.OWNER || orgRole === ROLES.ADMIN;
  const { workspace } = useWorkspaceFromList(workspaceId, {
    enabled: !orgAdmin,
  });
  const canManage =
    orgAdmin || (workspace?.user_ws_level ?? 0) >= WORKSPACE_ADMIN_LEVEL;
  const scope = useMemo(
    () => ({ workspaceId, canManage }),
    [workspaceId, canManage],
  );
  return (
    <IntegrationsScopeContext.Provider value={scope}>
      {children}
    </IntegrationsScopeContext.Provider>
  );
}

IntegrationsWorkspaceScope.propTypes = {
  children: PropTypes.node.isRequired,
};
