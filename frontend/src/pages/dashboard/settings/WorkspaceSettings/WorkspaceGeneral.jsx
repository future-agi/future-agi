import React, { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useWorkspaceFromList } from "src/api/workspaces/list";
import {
  Box,
  Typography,
  TextField,
  Button,
} from "@mui/material";
import { useSnackbar } from "notistack";
import { LoadingScreen } from "src/components/loading-screen";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";

export default function WorkspaceGeneral() {
  const { workspaceId } = useParams();
  const { role } = useAuthContext();
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();
  const { currentWorkspaceId, updateWorkspaceName } = useWorkspace();

  const [displayName, setDisplayName] = useState("");

  const isOrgAdminPlus = role === "Owner" || role === "Admin";

  // Fetch workspace list and find this workspace
  const { workspace, isLoading } = useWorkspaceFromList(workspaceId);

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
      queryClient.invalidateQueries({
        queryKey: ["workspaces-list"],
      });
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

  if (isLoading) {
    return (
      <LoadingScreen variant="orbit" sx={{ height: "100%", minHeight: "60vh" }} />
    );
  }

  return (
    <>
      <Helmet>
        <title>Workspace Settings</title>
      </Helmet>

      <Box sx={{ px: "2px", maxWidth: 600 }}>
        <Typography
          sx={{
            typography: "m2",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
          }}
        >
          General
        </Typography>
        <Typography
          sx={{
            typography: "s1",
            fontWeight: "fontWeightRegular",
            color: "text.secondary",
            mt: 0.5,
            mb: 3,
          }}
        >
          Manage workspace settings
        </Typography>

        <TextField
          label="Workspace Name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          disabled={!canEdit}
          fullWidth
          size="small"
        />

        {canEdit && (
          <Box sx={{ mt: 3 }}>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={!hasChanges || updateMutation.isPending}
            >
              {updateMutation.isPending ? "Saving..." : "Save Changes"}
            </Button>
          </Box>
        )}
      </Box>
    </>
  );
}
