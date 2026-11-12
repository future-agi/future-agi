import React, { useRef, useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ShowActionMenus from "./ShowActionMenus";
import AllActionForm from "./AllActionForm";
import {
  cellStyles,
  orgRoleCellStyles,
  wsRoleCellStyles,
  wsActionMenusByStatus,
  LEVELS,
} from "./constant";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";

const ORG_ADMIN_MANAGED_REASON =
  "Organization Admins and Owners are managed from Organization Members.";

export const useCanEditRole = (
  actorOrgLevel,
  targetOrgLevel,
  isSameUser,
  { workspaceScope = false, actorWsLevel, targetWsLevel } = {},
) => {
  return useMemo(() => {
    if (isSameUser) {
      return { allowed: false, reason: "You can't edit your own role." };
    }

    if (actorOrgLevel == null || targetOrgLevel == null) {
      return { allowed: false, reason: "Unable to determine permissions." };
    }

    if (workspaceScope) {
      // Org Admin+ have automatic workspace access, even when the actor is Owner.
      if (targetOrgLevel >= LEVELS.ADMIN) {
        return {
          allowed: false,
          reason: "Organization admins cannot be managed from a workspace.",
        };
      }

      // Org Admin+ bypass the scoped API's workspace-rank checks.
      if (actorOrgLevel >= LEVELS.ADMIN) {
        return { allowed: true, reason: "" };
      }

      if (actorWsLevel == null || targetWsLevel == null) {
        return {
          allowed: false,
          reason: "Unable to determine workspace permissions.",
        };
      }

      // WorkspaceMemberRoleUpdateAPIView requires WS Admin and a lower target role.
      if (
        actorWsLevel < LEVELS.WORKSPACE_ADMIN ||
        actorWsLevel <= targetWsLevel
      ) {
        return {
          allowed: false,
          reason:
            "You must be a workspace admin above this user's workspace level.",
        };
      }

      return { allowed: true, reason: "" };
    }

    // Owner can manage everyone in organization scope.
    if (actorOrgLevel >= LEVELS.OWNER) {
      return { allowed: true, reason: "" };
    }

    // Must be strictly above target level
    if (actorOrgLevel > targetOrgLevel) {
      return { allowed: true, reason: "" };
    }

    return {
      allowed: false,
      reason: "You cannot manage a user at or above your own level.",
    };
  }, [
    actorOrgLevel,
    targetOrgLevel,
    isSameUser,
    workspaceScope,
    actorWsLevel,
    targetWsLevel,
  ]);
};

export const useCanResendInvite = (
  actorOrgLevel,
  targetOrgLevel,
  isSameUser,
) => {
  return useMemo(() => {
    if (isSameUser) {
      return { allowed: false, reason: "You can't resend your own invite." };
    }

    if (actorOrgLevel == null) {
      return { allowed: false, reason: "Unable to determine permissions." };
    }

    // Owner can resend any invite
    if (actorOrgLevel >= LEVELS.OWNER) {
      return { allowed: true, reason: "" };
    }

    // Admin can resend invites for users below Admin level
    if (actorOrgLevel >= LEVELS.ADMIN) {
      if (targetOrgLevel != null && targetOrgLevel >= LEVELS.ADMIN) {
        return {
          allowed: false,
          reason: "Admin cannot resend invites for Admin or above.",
        };
      }
      return { allowed: true, reason: "" };
    }

    return {
      allowed: false,
      reason: "You are not authorized to resend invites.",
    };
  }, [actorOrgLevel, targetOrgLevel, isSameUser]);
};

export const useCanSendInvite = (orgLevel, effectiveLevel) => {
  return useMemo(() => {
    if (
      (orgLevel != null && orgLevel >= LEVELS.ADMIN) ||
      (effectiveLevel != null && effectiveLevel >= LEVELS.ADMIN)
    ) {
      return { allowed: true, reason: "" };
    }
    return {
      allowed: false,
      reason: "You are not authorized to send invites.",
    };
  }, [orgLevel, effectiveLevel]);
};

// Use integer levels when available, fall back to string role check
export const canManageUsersFor = ({ orgLevel, effectiveLevel, role }) =>
  (orgLevel != null && orgLevel >= LEVELS.ADMIN) ||
  (effectiveLevel != null && effectiveLevel >= LEVELS.ADMIN) ||
  role === "Owner" ||
  role === "Admin" ||
  role === "workspace_admin";

const ChipCell = ({ value, styleMap }) => {
  if (!value) return null;
  const styles = styleMap?.[value] || {
    backgroundColor: "action.selected",
    color: "text.secondary",
  };

  return (
    <Box
      sx={{
        height: "100%",
        width: "fit-content",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      <Stack
        sx={{
          px: "12px",
          py: "4px",
          gap: "8px",
          height: "24px",
          borderRadius: "4px",
          ...styles,
        }}
        direction={"row"}
        alignItems={"center"}
      >
        <Typography typography="s2" fontWeight={"fontWeightMedium"}>
          {value}
        </Typography>
      </Stack>
    </Box>
  );
};

ChipCell.propTypes = {
  value: PropTypes.string,
  styleMap: PropTypes.object,
};

export const ProcessingStatusCell = ({ value }) => (
  <ChipCell value={value} styleMap={cellStyles} />
);
ProcessingStatusCell.propTypes = { value: PropTypes.string };

export const OrgRoleCell = ({ value }) => (
  <ChipCell value={value} styleMap={orgRoleCellStyles} />
);
OrgRoleCell.propTypes = { value: PropTypes.string };

export const WorkspaceRoleCell = ({ value }) => (
  <ChipCell value={value} styleMap={wsRoleCellStyles} />
);
WorkspaceRoleCell.propTypes = { value: PropTypes.string };

export const WorkspaceChipsCell = ({ value }) => {
  if (!value || value.length === 0) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
        }}
      >
        <Typography typography="s2" color="text.disabled">
          —
        </Typography>
      </Box>
    );
  }

  const first = value[0];
  const remaining = value.length - 1;

  const tooltipContent = (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, py: 0.5 }}>
      {value.map((ws) => (
        <Box
          key={ws.workspace_id}
          sx={{ display: "flex", alignItems: "center", gap: 1 }}
        >
          <Typography variant="caption" fontWeight="fontWeightMedium">
            {ws.workspace_name}
          </Typography>
          <Typography variant="caption" color="inherit" sx={{ opacity: 0.7 }}>
            {ws.ws_role}
          </Typography>
        </Box>
      ))}
    </Box>
  );

  return (
    <Tooltip
      title={tooltipContent}
      arrow
      placement="bottom-start"
      slotProps={{
        tooltip: {
          sx: {
            maxWidth: 300,
            "& .MuiTooltip-arrow": {
              "&::before": {},
            },
          },
        },
      }}
    >
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          gap: 0.5,
        }}
      >
        <Chip
          label={first.workspace_name}
          size="small"
          sx={{
            maxWidth: 160,
            typography: "s2",
            bgcolor: (theme) => theme.palette.action.hover,
            color: "text.primary",
            "&:hover": {
              bgcolor: (theme) => theme.palette.action.hover,
            },
          }}
        />
        {remaining > 0 && (
          <Chip
            label={`+${remaining}`}
            size="small"
            variant="outlined"
            sx={{
              typography: "s2",
              borderColor: "divider",
              color: "text.secondary",
            }}
          />
        )}
      </Box>
    </Tooltip>
  );
};
WorkspaceChipsCell.propTypes = { value: PropTypes.array };

// The row's menu. On the Members page the form it opens lives in the page
// (onOpenActionForm, from cellRendererParams), not in this cell: the table
// builds its cells again when it reloads, which would close a form held here.
// Without onOpenActionForm the cell holds the form itself.
export const ActionRender = (props) => {
  const { data, api, workspaceScope, workspaceId, onOpenActionForm } = props;
  const status = data.status;
  const { user, orgLevel, wsLevel } = useAuthContext();

  const targetOrgLevel = data?.org_level;
  const actorOrgLevel = orgLevel ?? user?.org_level;
  const actorWsLevel = wsLevel ?? user?.ws_level;
  const loggedInUserEmail = user?.email;
  const selectedUserEmail = data?.email;

  const isSameUser = loggedInUserEmail === selectedUserEmail;

  const { allowed: canEdit, reason: editReason } = useCanEditRole(
    actorOrgLevel,
    targetOrgLevel,
    isSameUser,
    { workspaceScope, actorWsLevel, targetWsLevel: data?.ws_level },
  );

  const { allowed: canResend, reason: resendReason } = useCanResendInvite(
    actorOrgLevel,
    targetOrgLevel,
    isSameUser,
  );

  // Workspace removal requires admin authority, not a higher target role.
  // The endpoint remains responsible for protecting the user's last workspace.
  const canRemove = workspaceScope
    ? !isSameUser &&
      actorOrgLevel != null &&
      targetOrgLevel != null &&
      targetOrgLevel < LEVELS.ADMIN &&
      (actorOrgLevel >= LEVELS.ADMIN ||
        (actorWsLevel != null && actorWsLevel >= LEVELS.WORKSPACE_ADMIN))
    : canEdit;

  // Org Admins and Owners hold Workspace Admin in every workspace, and the
  // workspace role and removal endpoints refuse them. Whoever the organization
  // rule lets manage them sees those entries disabled with the reason; the
  // invite cancel endpoint accepts them from an org Admin+ actor.
  const { allowed: canManageInOrganization } = useCanEditRole(
    actorOrgLevel,
    targetOrgLevel,
    isSameUser,
  );
  const managedFromOrganization =
    workspaceScope &&
    targetOrgLevel != null &&
    targetOrgLevel >= LEVELS.ADMIN &&
    canManageInOrganization;
  const canCancel = managedFromOrganization || canEdit;
  const disabledReasons = managedFromOrganization
    ? {
        "edit-ws-role": ORG_ADMIN_MANAGED_REASON,
        "remove-ws-member": ORG_ADMIN_MANAGED_REASON,
      }
    : undefined;

  const hasAnyPermission = canEdit || canRemove || canResend || canCancel;
  const isDisabled = !hasAnyPermission;
  const tooltipReason = isDisabled ? editReason || resendReason : "";

  const actionRef = useRef(null);
  const [showOptions, setShowOptions] = useState(false);
  const id = showOptions ? "action-popper" : undefined;

  const [openActionForm, setOpenActionForm] = useState(null);
  const openForm = (item) => {
    if (onOpenActionForm) onOpenActionForm(item, data);
    else setOpenActionForm(item);
  };

  return (
    <Box
      sx={{
        height: "100%",
        width: "fit-content",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      {["Active", "Pending", "Expired", "Deactivated"].includes(status) && (
        <Stack
          ref={actionRef}
          sx={{
            py: "4px",
            gap: "8px",
            height: "24px",
            borderRadius: "4px",
            cursor: isDisabled ? "not-allowed" : "pointer",
          }}
          direction={"row"}
          alignItems={"center"}
          onClick={() => {
            if (isDisabled) return;
            setShowOptions(true);
          }}
        >
          <CustomTooltip show={isDisabled} title={tooltipReason}>
            <Iconify
              icon="solar:menu-dots-linear"
              sx={{
                color: isDisabled ? "divider" : "text.primary",
              }}
            />
          </CustomTooltip>
        </Stack>
      )}

      <ShowActionMenus
        id={id}
        actionRef={actionRef}
        open={showOptions}
        onClose={() => setShowOptions(false)}
        data={data}
        setOpenActionForm={openForm}
        canEdit={canEdit}
        canRemove={canRemove}
        canResend={canResend}
        canCancel={canCancel}
        disabledReasons={disabledReasons}
        menusByStatus={workspaceScope ? wsActionMenusByStatus : undefined}
      />

      {!onOpenActionForm && openActionForm && (
        <AllActionForm
          openActionForm={openActionForm}
          onClose={() => setOpenActionForm(null)}
          userData={data}
          gridApi={api}
          workspaceId={workspaceId}
        />
      )}
    </Box>
  );
};

ActionRender.propTypes = {
  data: PropTypes.object,
  api: PropTypes.object,
  workspaceScope: PropTypes.bool,
  workspaceId: PropTypes.string,
  onOpenActionForm: PropTypes.func,
};
