// Settings › Audit log (W4-7 org page; W4 §9 integration, M6): the workspace's
// audit across datasets, experiments, access, sharing and reviews, read from
// GET /model-hub/audit/?scope=workspace:<id>. Workspace admins only (the
// route's WorkspaceRoleProtection; the server answers 403 for others).
import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "react-router";
import { Box, Typography } from "@mui/material";
import { useAuthContext } from "src/auth/hooks";
import { AuditLogPanel } from "src/sections/common/Audit/AuditDrawer";
import { workspaceAuditScope } from "src/sections/common/Audit/auditModel";
import { useWorkspaceFromList } from "src/api/workspaces/list";

/**
 * L7c g1 (nav:audit-log): workspace admins only, by the same rule as the
 * settings nav (org Owner / Admin, or workspace level 8+). Anyone else who
 * opens the URL reads a denial, not an empty filter.
 */
const canSeeWorkspaceAudit = (orgRole, wsLevel) =>
  orgRole === "Owner" || orgRole === "Admin" || Number(wsLevel ?? 0) >= 8;

export default function WorkspaceAuditLog() {
  const { workspaceId } = useParams();
  const { workspace } = useWorkspaceFromList(workspaceId) ?? {};
  const { user } = useAuthContext();
  if (!canSeeWorkspaceAudit(user?.organization_role, workspace?.user_ws_level))
    return (
      <>
        <Helmet>
          <title>Audit log</title>
        </Helmet>
        <Box sx={{ maxWidth: 640 }} data-testid="workspace-audit-denied">
          <Typography variant="subtitle1">Audit log</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Only workspace admins can see this workspace&apos;s audit log.
          </Typography>
        </Box>
      </>
    );
  return (
    <>
      <Helmet>
        <title>Audit log</title>
      </Helmet>
      <Box
        sx={{ height: "100%", minHeight: 480, maxWidth: 960 }}
        data-testid="workspace-audit-log"
      >
        <AuditLogPanel
          scope={workspaceAuditScope(workspaceId)}
          title={workspace?.display_name || workspace?.name || undefined}
        />
      </Box>
    </>
  );
}
