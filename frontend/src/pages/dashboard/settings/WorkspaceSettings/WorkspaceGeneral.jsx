import React, { useEffect, useId, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  useWorkspaceFromList,
  workspacesListKey,
} from "src/api/workspaces/list";
import { Box, Button, Link, TextField, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import PageHeader from "src/components/page-header";
import { RouterLink } from "src/routes/components";
import { dsTypography } from "src/theme/ds-tokens";
import axiosInstance, { endpoints } from "src/utils/axios";
import { isPrototypeMode } from "src/utils/prototype";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import WorkspaceSharingCard from "src/sections/common/Access/WorkspaceSharingCard";
import { workspaceDirectoryKey } from "../UserManagementV2/getWorkspaceQueryOptions";
import { wsLevelToString } from "../UserManagementV2/constant";

// A prototype build has no Members page (the Settings menu leaves it out).
const PROTOTYPE_BUILD = isPrototypeMode();
const ORG_ROLES = ["Owner", "Admin", "Member", "Viewer"];
const ORG_ADMIN_ROLES = ["Owner", "Admin"];

// The card and rows of the sibling Settings pages (Organization settings,
// Profile): below md the card fills the screen and each label sits above its
// value.
const CARD_SX = {
  width: "100%",
  maxWidth: "670px",
  p: 2,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: (theme) => theme.spacing(1),
  bgcolor: "background.paper",
};
const ROW_SX = {
  display: "flex",
  flexWrap: { xs: "wrap", md: "nowrap" },
  alignItems: "flex-start",
  rowGap: 1,
};
const ROW_DIVIDER_SX = {
  borderBottom: "1px solid",
  borderColor: "divider",
  pb: 2.5,
  mb: 2.5,
};
const LABEL_SX = {
  width: { xs: "100%", md: "200px" },
  flexShrink: 0,
  pt: { md: "7px" },
  color: "text.secondary",
  fontWeight: "fontWeightRegular",
};
const VALUE_SX = {
  width: { xs: "100%", md: "70%" },
  minWidth: 0,
  overflowWrap: "anywhere",
};
const HELPER_SX = { display: "block", mt: 0.5, color: "text.secondary" };

// The reader's access in words: the workspace role their level gives (an
// organization Owner or Admin administers every workspace), and their
// organization role. With no workspace level recorded, only an organization
// Owner or Admin reaches the workspace, through that role, so only they are
// told it applies here; the server gives any other reader no access to it.
function describeAccess({ orgRole, wsLevel, isOrgAdminPlus }) {
  const level = Number(wsLevel) || 0;
  const noLevel = wsLevel === null || wsLevel === undefined;
  const orgRoleApplies =
    noLevel && isOrgAdminPlus && ORG_ADMIN_ROLES.includes(orgRole);
  let workspaceRole = null;
  if (isOrgAdminPlus || level >= 8) workspaceRole = wsLevelToString[8];
  else if (level >= 3) workspaceRole = wsLevelToString[3];
  else if (level >= 1) workspaceRole = wsLevelToString[1];
  if (workspaceRole) {
    return {
      role: workspaceRole,
      detail: orgRole ? `Organization role: ${orgRole}` : null,
      note: orgRoleApplies
        ? "Your organization role applies in this workspace."
        : null,
    };
  }
  return {
    role: orgRole ? `Organization ${orgRole}` : "Organization access",
    detail: null,
    note: null,
  };
}

export default function WorkspaceGeneral() {
  const { workspaceId } = useParams();
  const { role, user } = useAuthContext();
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();
  const { currentWorkspaceId, updateWorkspaceName } = useWorkspace();
  const nameInputId = useId();
  const nameLabelId = useId();
  const idLabelId = useId();
  const accessLabelId = useId();
  const idRef = useRef(null);

  const [displayName, setDisplayName] = useState("");

  const isOrgAdminPlus = role === "Owner" || role === "Admin";

  // Fetch workspace list and find this workspace
  const { workspace, isLoading, isError } = useWorkspaceFromList(workspaceId);

  useEffect(() => {
    if (workspace) {
      setDisplayName(workspace.display_name || workspace.name || "");
    }
  }, [workspace]);

  // Check if user is WS Admin for this workspace
  const wsLevel = workspace?.user_ws_level ?? 0;
  const isWsAdmin = isOrgAdminPlus || wsLevel >= 8;
  const canEdit = isWsAdmin;

  const updateMutation = useMutation({
    mutationFn: (payload) =>
      axiosInstance.put(
        endpoints.workspace.workspaceUpdate(workspaceId),
        payload,
      ),
    onSuccess: () => {
      enqueueSnackbar("Workspace updated", { variant: "success" });
      // Update context + sessionStorage if renaming the current workspace
      if (workspaceId === currentWorkspaceId) {
        updateWorkspaceName(displayName);
      }
      queryClient.invalidateQueries({
        queryKey: ["user-workspaces-for-settings"],
      });
      // Refresh the workspace switcher dropdown
      queryClient.invalidateQueries({ queryKey: workspacesListKey });
      // Directory pages stay fresh indefinitely unless explicitly invalidated.
      queryClient.invalidateQueries({ queryKey: workspaceDirectoryKey });
    },
    onError: (err) => {
      enqueueSnackbar(
        err?.response?.data?.message || "Failed to update workspace",
        { variant: "error" },
      );
    },
  });

  const handleSave = () => {
    updateMutation.mutate({ display_name: displayName });
  };

  const hasChanges =
    workspace &&
    displayName !== (workspace.display_name || workspace.name || "");

  // Where the clipboard is missing (a page on plain http), throws or refuses,
  // the ID is selected instead, ready to copy by hand.
  const selectId = () => {
    const node = idRef.current;
    const selection = node && window.getSelection?.();
    if (!selection) return;
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  };

  const handleCopy = () => {
    let copying;
    try {
      copying = navigator.clipboard.writeText(workspaceId);
    } catch {
      selectId();
      return;
    }
    Promise.resolve(copying).then(
      () => enqueueSnackbar("Workspace ID copied", { variant: "success" }),
      selectId,
    );
  };

  if (isLoading) {
    return (
      <LoadingScreen
        variant="orbit"
        sx={{ height: "100%", minHeight: "60vh" }}
      />
    );
  }

  // Without the workspace there is nothing to edit, and `canEdit` comes from
  // the org role — an Owner would otherwise get a blank name field with a live
  // Save that renames the workspace to "".
  if (isError || !workspace) {
    return (
      <Box sx={{ py: 6, textAlign: "center" }}>
        <Typography typography="s1" sx={{ color: "text.secondary" }}>
          This workspace could not be loaded. Reload the page, or pick another
          workspace from the switcher.
        </Typography>
      </Box>
    );
  }

  const orgRole = [user?.organization_role, role].find((r) =>
    ORG_ROLES.includes(r),
  );
  const access = describeAccess({
    orgRole,
    wsLevel: workspace.user_ws_level,
    isOrgAdminPlus,
  });
  // The Members page admits workspace admins only, as the Settings menu shows.
  const canManageMembers = isWsAdmin && !PROTOTYPE_BUILD;

  return (
    <>
      <Helmet>
        <title>Workspace settings</title>
      </Helmet>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2, px: "2px" }}>
        <PageHeader
          title="General"
          description="Name, ID and access for this workspace."
          sx={{ mb: 0 }}
        />

        <Box sx={CARD_SX}>
          <Box
            role="group"
            aria-labelledby={nameLabelId}
            data-testid="workspace-name-section"
            sx={{ ...ROW_SX, ...ROW_DIVIDER_SX }}
          >
            <Typography
              id={nameLabelId}
              component="label"
              htmlFor={nameInputId}
              variant="s1"
              sx={LABEL_SX}
            >
              Workspace name
            </Typography>
            <Box
              sx={{
                ...VALUE_SX,
                display: "flex",
                alignItems: "flex-start",
                gap: 1,
              }}
            >
              <TextField
                id={nameInputId}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={!canEdit}
                helperText={
                  canEdit
                    ? undefined
                    : "Only workspace admins can rename this workspace."
                }
                // The reason stays readable under the disabled field.
                FormHelperTextProps={{
                  sx: { "&.Mui-disabled": { color: "text.secondary" } },
                }}
                fullWidth
                size="small"
                sx={{ flex: 1, minWidth: 0 }}
              />
              {canEdit && (
                <LoadingButton
                  variant="contained"
                  color="primary"
                  size="small"
                  loading={updateMutation.isPending}
                  onClick={handleSave}
                  disabled={!hasChanges}
                  sx={{ flexShrink: 0, mt: "2px" }}
                >
                  Save
                </LoadingButton>
              )}
            </Box>
          </Box>

          <Box
            role="group"
            aria-labelledby={idLabelId}
            data-testid="workspace-id-section"
            sx={{ ...ROW_SX, ...ROW_DIVIDER_SX }}
          >
            <Typography id={idLabelId} variant="s1" sx={LABEL_SX}>
              Workspace ID
            </Typography>
            <Box sx={VALUE_SX}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Box
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    minHeight: 32,
                    display: "flex",
                    alignItems: "center",
                    px: 1.5,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "6px",
                    bgcolor: "background.neutral",
                  }}
                >
                  <Box
                    component="code"
                    ref={idRef}
                    sx={{
                      ...dsTypography.code_md,
                      color: "text.primary",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {workspaceId}
                  </Box>
                </Box>
                <Button
                  variant="outlined"
                  color="primary"
                  size="small"
                  onClick={handleCopy}
                  aria-label="Copy workspace ID"
                  startIcon={<Iconify icon="solar:copy-linear" width={16} />}
                  sx={{ flexShrink: 0 }}
                >
                  Copy
                </Button>
              </Box>
              <Typography variant="s1" sx={HELPER_SX}>
                Use this ID with the API and the SDK.
              </Typography>
            </Box>
          </Box>

          <Box
            role="group"
            aria-labelledby={accessLabelId}
            data-testid="workspace-access-section"
            sx={ROW_SX}
          >
            <Typography
              id={accessLabelId}
              variant="s1"
              sx={{ ...LABEL_SX, pt: 0 }}
            >
              Your access
            </Typography>
            <Box
              sx={{
                ...VALUE_SX,
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "space-between",
                alignItems: "flex-start",
                gap: 1,
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  variant="s1"
                  sx={{
                    display: "block",
                    color: "text.primary",
                    fontWeight: "fontWeightMedium",
                  }}
                >
                  {access.role}
                </Typography>
                {access.detail && (
                  <Typography variant="s1" sx={HELPER_SX}>
                    {access.detail}
                  </Typography>
                )}
                {access.note && (
                  <Typography variant="s1" sx={HELPER_SX}>
                    {access.note}
                  </Typography>
                )}
              </Box>
              {canManageMembers && (
                <Link
                  component={RouterLink}
                  href={`/dashboard/settings/workspace/${workspaceId}/members`}
                  variant="s1"
                  underline="hover"
                  sx={{ flexShrink: 0, fontWeight: "fontWeightMedium" }}
                >
                  Manage members
                </Link>
              )}
            </Box>
          </Box>
        </Box>

        <WorkspaceSharingCard workspaceId={workspaceId} />
      </Box>
    </>
  );
}
