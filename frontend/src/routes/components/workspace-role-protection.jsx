import React from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { LoadingScreen } from "src/components/loading-screen";
import NoAccessState from "./no-access-state";
import { useWorkspaceFromList } from "src/api/workspaces/list";
import { useAuthContext } from "src/auth/hooks";

/**
 * WorkspaceRoleProtection - Role-based protection for workspace settings routes
 *
 * Unlike RoleProtection which checks organization-level roles, this component
 * checks the user's role for the SPECIFIC workspace being viewed in settings.
 *
 * Usage:
 *   <WorkspaceRoleProtection allowedRoles={["workspace_admin", "workspace_member"]}>
 *     <WorkspaceIntegrations />
 *   </WorkspaceRoleProtection>
 *
 * Role mapping from workspace level (user_ws_level) — mirrors backend Level constants:
 * - >= 8 = workspace_admin (WORKSPACE_ADMIN)
 * - >= 3 = workspace_member (WORKSPACE_MEMBER)
 * - >= 1 = workspace_viewer (WORKSPACE_VIEWER)
 * - 0 = no access
 *
 * Organization roles (Owner, Admin) bypass workspace-level checks and always have full access.
 */
const WorkspaceRoleProtection = ({ allowedRoles, children }) => {
  const { workspaceId } = useParams();
  const { user: _user, role: orgRole } = useAuthContext();

  // Organization Owners and Admins have full access to all workspaces
  const isOrgAdminPlus = orgRole === "Owner" || orgRole === "Admin";

  // Fetch workspace list to get user's role for this specific workspace
  const { workspace, isLoading, isError, isOrgUnavailable, refetch } =
    useWorkspaceFromList(workspaceId, {
      enabled: !isOrgAdminPlus, // Skip query if org admin
    });

  // Loading state
  if (isLoading && !isOrgAdminPlus) {
    return <LoadingScreen variant="orbit" sx={{ minHeight: "60vh" }} />;
  }

  // No organization could be set for this session, so the workspace list is
  // never requested and no retry of it can help: say so, without a Retry.
  if (isOrgUnavailable && !isOrgAdminPlus) {
    return (
      <Stack
        data-testid="workspace-access-no-organization"
        alignItems="center"
        justifyContent="center"
        spacing={2}
        sx={{ minHeight: "60vh", px: 3, textAlign: "center" }}
      >
        <Typography variant="h5" component="h1">
          No organization for this session
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ maxWidth: 480 }}
        >
          Workspace access is checked within an organization, and none could be
          set for your account. Ask an organization Owner or Admin to add you,
          then sign in again.
        </Typography>
      </Stack>
    );
  }

  // The workspace list failed to load: that is not a denial, so offer a retry.
  if (isError && !isOrgAdminPlus) {
    return (
      <Stack
        data-testid="workspace-access-load-failed"
        alignItems="center"
        justifyContent="center"
        spacing={2}
        sx={{ minHeight: "60vh", px: 3, textAlign: "center" }}
      >
        <Typography variant="h5" component="h1">
          Your access to this workspace couldn&apos;t be checked
        </Typography>
        <Typography variant="body2" color="text.secondary">
          The workspace list failed to load.
        </Typography>
        <Button variant="outlined" color="inherit" onClick={() => refetch()}>
          Retry
        </Button>
      </Stack>
    );
  }

  // Organization admins bypass workspace role checks
  if (isOrgAdminPlus) {
    return <>{children}</>;
  }

  if (!workspace) {
    // Workspace not found or user has no access
    return <NoAccessState allowedRoles={allowedRoles} />;
  }

  // Map workspace level to role name
  // Backend level constants: WORKSPACE_ADMIN=8, WORKSPACE_MEMBER=3, WORKSPACE_VIEWER=1
  const wsLevel = workspace.user_ws_level || 0;
  const workspaceRole = (() => {
    if (wsLevel >= 8) return "workspace_admin";
    if (wsLevel >= 3) return "workspace_member";
    if (wsLevel >= 1) return "workspace_viewer";
    return null;
  })();

  // Check if user's workspace role is in allowed roles
  if (!workspaceRole || !allowedRoles.includes(workspaceRole)) {
    return (
      <NoAccessState
        allowedRoles={allowedRoles}
        workspaceRole={workspaceRole}
      />
    );
  }

  return <>{children}</>;
};

WorkspaceRoleProtection.propTypes = {
  allowedRoles: PropTypes.array.isRequired,
  children: PropTypes.any,
};

export default WorkspaceRoleProtection;
