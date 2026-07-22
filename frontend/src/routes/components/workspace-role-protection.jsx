import React from "react";
import PropTypes from "prop-types";
import { useParams, Navigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { CircularProgress, Box } from "@mui/material";
import axiosInstance, { endpoints } from "src/utils/axios";
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
  const { data, isLoading, isError } = useQuery({
    queryKey: ["workspace-detail-protection", workspaceId],
    queryFn: () => axiosInstance.get(endpoints.workspace.workspaceList),
    enabled: !!workspaceId && !isOrgAdminPlus, // Skip query if org admin
    staleTime: 30000, // Cache for 30 seconds to avoid excessive requests
  });

  // Loading state
  if (isLoading && !isOrgAdminPlus) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 300,
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  // Error state - deny access
  if (isError && !isOrgAdminPlus) {
    return <Navigate to="/dashboard/develop" replace />;
  }

  // Organization admins bypass workspace role checks
  if (isOrgAdminPlus) {
    return <>{children}</>;
  }

  // Find the specific workspace and get user's role for it
  const workspace = (data?.data?.results || []).find(
    (ws) => ws.id === workspaceId,
  );

  if (!workspace) {
    // Workspace not found or user has no access
    return <Navigate to="/dashboard/develop" replace />;
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
    return <Navigate to="/dashboard/develop" replace />;
  }

  return <>{children}</>;
};

WorkspaceRoleProtection.propTypes = {
  allowedRoles: PropTypes.array.isRequired,
  children: PropTypes.any,
};

export default WorkspaceRoleProtection;
