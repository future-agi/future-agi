import React from "react";
import PropTypes from "prop-types";
import { useAuthContext } from "src/auth/hooks";
import { Navigate } from "react-router";

/**
 * RoleProtection - Role-based route protection
 *
 * Checks BOTH organization role AND workspace role to determine access.
 * This allows the component to work for both org-level and workspace-level routes.
 *
 * Organization roles: "Owner", "Admin", "Member", "Viewer"
 * Workspace roles: "workspace_admin", "workspace_member", "workspace_viewer"
 *
 * Access is granted if EITHER the org role OR workspace role is in allowedRoles.
 *
 * TODO: Consider migrating to integer permission levels for consistent, hierarchical
 * comparisons (e.g. level >= ADMIN_LEVEL) instead of list-inclusion checks.
 */
const RoleProtection = ({ allowedRoles, children }) => {
  const { user, role: workspaceRole } = useAuthContext();

  // Get both organization role and workspace role
  const orgRole = user?.organization_role;

  // Check if either org role or workspace role is allowed
  const hasOrgAccess = orgRole && allowedRoles.includes(orgRole);
  const hasWorkspaceAccess =
    workspaceRole && allowedRoles.includes(workspaceRole);

  if (!hasOrgAccess && !hasWorkspaceAccess) {
    return <Navigate to="/dashboard/develop" />;
  }

  return <>{children}</>;
};

RoleProtection.propTypes = {
  allowedRoles: PropTypes.array,
  children: PropTypes.any,
};

export default RoleProtection;
