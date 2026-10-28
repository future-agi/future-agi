import PropTypes from "prop-types";
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useDeleteAgent } from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { CONNECTED_AGENTS_PATH } from "../../agentsTabs";

export const deleteBlockedReason = (agent) => {
  if (!agent.environmentCount) return null;
  const n = agent.environmentCount;
  return `Tested by ${n} environment${n === 1 ? "" : "s"}. Delete those environments first.`;
};

/**
 * The header's More menu: Delete. A blocked delete says why in the item
 * itself, so the item stays reachable from the keyboard.
 */
export default function AgentMoreMenu({ agent }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canDelete = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.DELETE][role];
  const [anchor, setAnchor] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const remove = useDeleteAgent(agent.id);
  const blocked = deleteBlockedReason(agent);

  const onDelete = () =>
    remove.mutate(undefined, {
      onSuccess: () => {
        enqueueSnackbar(`${agent.name} deleted`, { variant: "success" });
        navigate(CONNECTED_AGENTS_PATH);
      },
      onError: (error) =>
        enqueueSnackbar(errorMessage(error) || "Couldn't delete this agent", {
          variant: "error",
        }),
    });

  if (!canDelete) return null;

  return (
    <>
      <IconButton
        size="small"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={anchor ? "true" : undefined}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ color: "text.secondary" }}
      >
        <Iconify icon="solar:menu-dots-bold" width={20} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220, maxWidth: 300, mt: 0.5 } } }}
      >
        <MenuItem
          disabled={!!blocked}
          onClick={() => {
            setAnchor(null);
            setConfirm(true);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:trash-bin-minimalistic-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Delete"
            secondary={blocked}
            primaryTypographyProps={{ typography: "s2" }}
            secondaryTypographyProps={{
              typography: "s2",
              sx: { whiteSpace: "normal" },
            }}
          />
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={`Delete ${agent.name}?`}
        content={
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: 500 }}>
              {agent.name}
            </Box>{" "}
            and its versions are removed from Connected agents. Runs that
            already happened keep their results.
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={remove.isPending}
            onClick={onDelete}
          >
            Delete
          </Button>
        }
      />
    </>
  );
}

AgentMoreMenu.propTypes = { agent: PropTypes.object.isRequired };
