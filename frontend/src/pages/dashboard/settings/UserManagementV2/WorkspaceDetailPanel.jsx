import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  Select,
  MenuItem,
  Stack,
  CircularProgress,
  IconButton,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { wsRoleCellStyles, LEVELS } from "./constant";
import { useAuthContext } from "src/auth/hooks";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";

const RoleChip = ({ role }) => {
  const styles = wsRoleCellStyles[role] || {
    backgroundColor: "action.selected",
    color: "text.secondary",
  };

  return (
    <Stack
      sx={{
        px: "12px",
        py: "4px",
        height: "24px",
        borderRadius: 0.5,
        width: "fit-content",
        ...styles,
      }}
      direction="row"
      alignItems="center"
    >
      <Typography typography="s2" fontWeight="fontWeightMedium">
        {role}
      </Typography>
    </Stack>
  );
};

RoleChip.propTypes = {
  role: PropTypes.string,
};

const WS_ROLE_OPTIONS = [
  { label: "Workspace Admin", value: LEVELS.WORKSPACE_ADMIN },
  { label: "Workspace Member", value: LEVELS.WORKSPACE_MEMBER },
  { label: "Workspace Viewer", value: LEVELS.WORKSPACE_VIEWER },
];

const WorkspaceRoleSelect = ({
  userId,
  workspaceId,
  currentLevel,
  onUpdate,
}) => {
  const [level, setLevel] = useState(currentLevel);

  const { mutate, isPending } = useMutation({
    mutationFn: (newLevel) =>
      axios.post(endpoints.rbac.workspaceMemberRoleUpdate(workspaceId), {
        user_id: userId,
        ws_level: newLevel,
      }),
    onSuccess: (res, newLevel) => {
      setLevel(newLevel);
      enqueueSnackbar("Workspace role updated", { variant: "success" });
      onUpdate?.();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.message || error?.detail || "Failed to update role",
        { variant: "error" },
      );
    },
  });

  const handleChange = (e) => {
    const newLevel = e.target.value;
    if (newLevel !== level) {
      mutate(newLevel);
    }
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <Select
        value={level}
        onChange={handleChange}
        size="small"
        disabled={isPending}
        sx={{
          minWidth: 180,
          height: 32,
          typography: "s2",
          fontWeight: "fontWeightMedium",
          "& .MuiSelect-select": { py: "4px" },
        }}
      >
        {WS_ROLE_OPTIONS.map((opt) => (
          <MenuItem key={opt.value} value={opt.value}>
            <Typography typography="s2" fontWeight="fontWeightMedium">
              {opt.label}
            </Typography>
          </MenuItem>
        ))}
      </Select>
      {isPending && <CircularProgress size={16} />}
    </Box>
  );
};

WorkspaceRoleSelect.propTypes = {
  userId: PropTypes.string.isRequired,
  workspaceId: PropTypes.string.isRequired,
  currentLevel: PropTypes.number.isRequired,
  onUpdate: PropTypes.func,
};

const RemoveFromWorkspaceButton = ({
  userId,
  workspaceId,
  isLastWorkspace,
  onRemoved,
}) => {
  const [confirmOpen, setConfirmOpen] = useState(false);

  const { mutate: removeFromWorkspace, isPending: isRemovingFromWs } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: () =>
        axios.delete(endpoints.rbac.workspaceMemberRemove(workspaceId), {
          data: { user_id: userId },
        }),
      onSuccess: () => {
        enqueueSnackbar("Removed from workspace", { variant: "success" });
        onRemoved?.();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to remove from workspace", {
          variant: "error",
        });
      },
    });

  const { mutate: removeFromOrg, isPending: isRemovingFromOrg } = useMutation({
    meta: { errorHandled: true },
    mutationFn: () =>
      axios.delete(endpoints.rbac.memberRemove, {
        data: { user_id: userId },
      }),
    onSuccess: () => {
      setConfirmOpen(false);
      enqueueSnackbar("Member removed from organization", {
        variant: "success",
      });
      onRemoved?.();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.result || "Failed to remove member from organization",
        { variant: "error" },
      );
    },
  });

  const isPending = isRemovingFromWs || isRemovingFromOrg;

  const handleClick = () => {
    if (isLastWorkspace) {
      setConfirmOpen(true);
    } else {
      removeFromWorkspace();
    }
  };

  return (
    <>
      <CustomTooltip title="Remove from workspace">
        <IconButton
          size="small"
          onClick={handleClick}
          disabled={isPending}
          sx={{ color: "error.main" }}
        >
          {isPending ? (
            <CircularProgress size={16} />
          ) : (
            <Iconify icon="solar:trash-bin-minimalistic-bold" width={18} />
          )}
        </IconButton>
      </CustomTooltip>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Remove from organization?"
        content="This is the user's only workspace. Removing them will also remove them from the organization entirely."
        action={
          <LoadingButton
            variant="contained"
            color="error"
            size="small"
            loading={isRemovingFromOrg}
            onClick={() => removeFromOrg()}
            sx={{ paddingX: "24px" }}
          >
            Remove
          </LoadingButton>
        }
      />
    </>
  );
};

RemoveFromWorkspaceButton.propTypes = {
  userId: PropTypes.string.isRequired,
  workspaceId: PropTypes.string.isRequired,
  isLastWorkspace: PropTypes.bool,
  onRemoved: PropTypes.func,
};

const WorkspaceDetailPanel = (props) => {
  const { data, api } = props;
  const { orgLevel, user } = useAuthContext();
  const workspaces = data?.workspaces || [];
  const targetOrgLevel = data?.org_level;
  const isTargetOrgAdmin = targetOrgLevel >= LEVELS.ADMIN;
  const isSameUser = user?.email === data?.email;

  const actorOrgLevel = orgLevel ?? user?.org_level;
  const canEditRoles =
    !isSameUser &&
    actorOrgLevel != null &&
    (actorOrgLevel >= LEVELS.OWNER || actorOrgLevel > targetOrgLevel);

  const handleUpdate = () => {
    // Refresh the row to pick up new data
    api?.refreshServerSide?.({});
  };

  if (workspaces.length === 0) {
    return (
      <Box sx={{ px: 4, py: 2 }}>
        <Typography typography="s2" color="text.disabled">
          No workspace memberships
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ px: 4, py: 1.5, bgcolor: "background.default" }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: canEditRoles ? "1fr 1fr auto" : "1fr 1fr",
          gap: 0,
          maxWidth: canEditRoles ? 580 : 500,
        }}
      >
        {/* Header */}
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          color="text.secondary"
          sx={{ pb: 1, borderBottom: "1px solid", borderColor: "divider" }}
        >
          Workspace
        </Typography>
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          color="text.secondary"
          sx={{ pb: 1, borderBottom: "1px solid", borderColor: "divider" }}
        >
          Role
        </Typography>
        {canEditRoles && (
          <Typography
            typography="s2"
            fontWeight="fontWeightSemiBold"
            color="text.secondary"
            sx={{
              pb: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              pl: 1,
            }}
          >
            Action
          </Typography>
        )}

        {/* Rows */}
        {workspaces.map((ws) => {
          const wsId = ws.workspace_id;
          const wsName = ws.workspace_name;
          const wsRoleValue = ws.ws_role;
          const wsLevelValue = ws.ws_level;
          const autoAccess = ws.auto_access;
          return (
            <React.Fragment key={wsId}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  py: 1,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  color="text.primary"
                >
                  {wsName}
                </Typography>
              </Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  py: 1,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                {isTargetOrgAdmin || autoAccess || !canEditRoles ? (
                  <RoleChip role={wsRoleValue} />
                ) : (
                  <WorkspaceRoleSelect
                    userId={data.id}
                    workspaceId={wsId}
                    currentLevel={wsLevelValue}
                    onUpdate={handleUpdate}
                  />
                )}
              </Box>
              {canEditRoles && (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    py: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    pl: 1,
                  }}
                >
                  {!autoAccess && !isTargetOrgAdmin ? (
                    <RemoveFromWorkspaceButton
                      userId={data.id}
                      workspaceId={wsId}
                      isLastWorkspace={workspaces.length === 1}
                      onRemoved={handleUpdate}
                    />
                  ) : null}
                </Box>
              )}
            </React.Fragment>
          );
        })}
      </Box>
    </Box>
  );
};

WorkspaceDetailPanel.propTypes = {
  data: PropTypes.object,
  api: PropTypes.object,
};

export default WorkspaceDetailPanel;
