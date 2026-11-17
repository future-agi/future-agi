import { useMemo } from "react";
import { matchRoutes } from "react-router-dom";

import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import RoleProtection from "src/routes/components/role-protection";
import { paths } from "src/routes/paths";
import { dashboardRoutes } from "src/routes/sections/dashboard";

// The organization's settings pages: Org Settings first, then the others in
// the order the Settings menu lists them. Billing is not one of them here: it
// is offered exactly where Usage Summary is (cloud, or gateway credits), and
// this list answers where Usage Summary is not. Updates checks the host
// inside its page, so the route table cannot answer for it. License is left
// out too: its route admits a workspace admin, but the page shows licence
// details only to an organization Owner or Admin and tells anyone else
// "No license", which is not true for them.
const ORGANIZATION_PAGES = [
  paths.dashboard.settings.orgSettings,
  paths.dashboard.settings.manageteam,
  paths.dashboard.settings.mcpServer,
  "/dashboard/settings/workspace",
];

// The rule RoleProtection applies on the route: the organization role or the
// current workspace role is one of the route's allowed roles.
function admits(element, orgRole, workspaceRole) {
  if (element?.type !== RoleProtection) return false;
  const allowed = element.props.allowedRoles || [];
  return [orgRole, workspaceRole].some(
    (role) => role && allowed.includes(role),
  );
}

/**
 * The first organization settings page this person may open, read from the
 * route table the router builds (`routes`, from dashboardRoutes) and the
 * guard on each route, or null when none admits them.
 */
export function firstOrganizationPage(routes, orgRole, workspaceRole) {
  return (
    ORGANIZATION_PAGES.find((path) => {
      const leaf = matchRoutes(routes, path)?.at(-1);
      return (
        leaf?.route.path !== "*" &&
        admits(leaf?.route.element, orgRole, workspaceRole)
      );
    }) ?? null
  );
}

/**
 * Where "Organization settings" can take the signed-in person: the first
 * organization settings page their role opens, with the same inputs the
 * router uses (routes/sections/index.jsx and RoleProtection), or null.
 */
export function useOrganizationSettingsLanding() {
  const { user, role } = useAuthContext();
  const { currentWorkspaceRole } = useWorkspace();
  const { isCloud } = useDeploymentMode();
  return useMemo(() => {
    if (!user) return null;
    const routes = dashboardRoutes(user, currentWorkspaceRole, { isCloud });
    return firstOrganizationPage(routes, user.organization_role, role);
  }, [user, role, currentWorkspaceRole, isCloud]);
}
