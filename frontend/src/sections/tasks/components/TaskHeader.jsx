import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Breadcrumbs,
  Chip,
  IconButton,
  Link,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import _ from "lodash";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const STATUS_CONFIG = {
  pending: { color: "warning", icon: "solar:clock-circle-linear" },
  running: { color: "info", icon: "svg-spinners:ring-resize" },
  completed: { color: "success", icon: "solar:check-circle-linear" },
  failed: { color: "error", icon: "solar:close-circle-linear" },
  paused: { color: "default", icon: "solar:pause-circle-linear" },
};

const StatusChip = ({ status }) => {
  const normalized = (status || "").toLowerCase();
  const cfg = STATUS_CONFIG[normalized] || STATUS_CONFIG.pending;
  return (
    <Chip
      label={_.capitalize(normalized || "unknown")}
      size="small"
      color={cfg.color}
      variant="outlined"
      icon={<Iconify icon={cfg.icon} width={14} />}
      sx={{
        fontWeight: 500,
        fontSize: "12px",
        height: 24,
        "& .MuiChip-icon": { ml: 0.5 },
      }}
    />
  );
};

StatusChip.propTypes = {
  status: PropTypes.string,
};

/**
 * TaskHeader — breadcrumb + editable name + project chip + status + actions
 *
 * Props:
 *   mode: "create" | "edit"
 *   name: string (current name)
 *   onNameChange?: (name) => void   — called on blur in edit mode
 *   projectName?: string            — read-only project name (edit mode)
 *   status?: string                 — task status (edit mode)
 *   actions?: ReactNode             — extra action buttons (Rerun / Pause / Delete)
 *   nameEditable?: boolean          — allow editing the name (default: true)
 *   backTo?: string                 — path to navigate when the back arrow is clicked
 */
const TaskHeader = ({
  mode,
  name,
  onNameChange,
  projectName,
  status,
  actions,
  nameEditable = true,
  backTo,
}) => {
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEditTask =
    RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role];
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(name || "");

  useEffect(() => {
    setDraftName(name || "");
  }, [name]);

  const handleSaveName = () => {
    setEditing(false);
    const trimmed = draftName.trim();
    if (trimmed && trimmed !== name && onNameChange) {
      onNameChange(trimmed);
    } else {
      setDraftName(name || "");
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        px: 2,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        backgroundColor: "background.paper",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          minWidth: 0,
          flex: 1,
        }}
      >
        {backTo && (
          <Tooltip title="Back">
            <IconButton
              size="small"
              onClick={() => navigate(backTo)}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <Iconify icon="mdi:arrow-left" width={18} />
            </IconButton>
          </Tooltip>
        )}
        <Breadcrumbs separator="/" sx={{ fontSize: "13px" }}>
          <Link
            underline="hover"
            color="text.secondary"
            sx={{ cursor: "pointer", fontSize: "13px" }}
            onClick={() => navigate("/dashboard/tasks")}
          >
            Tasks
          </Link>
          {editing ? (
            <TextField
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={handleSaveName}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSaveName();
                if (e.key === "Escape") {
                  setDraftName(name || "");
                  setEditing(false);
                }
              }}
              size="small"
              autoFocus
              variant="standard"
              sx={{
                "& .MuiInputBase-input": {
                  fontSize: "14px",
                  fontWeight: 600,
                  py: 0.25,
                },
              }}
            />
          ) : (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Typography
                color="text.primary"
                fontWeight={600}
                sx={{ fontSize: "14px" }}
              >
                {mode === "create" ? "Create Task" : name || "Untitled Task"}
              </Typography>
              {mode === "edit" && nameEditable && (
                <Tooltip title="Rename">
                  <IconButton
                    size="small"
                    onClick={() => setEditing(true)}
                    disabled={!canEditTask}
                    sx={{ p: 0.25, color: "text.secondary" }}
                  >
                    <Iconify icon="solar:pen-2-linear" width={14} />
                  </IconButton>
                </Tooltip>
              )}
            </Box>
          )}
        </Breadcrumbs>

        {mode === "edit" && projectName && (
          <Chip
            label={projectName}
            size="small"
            variant="outlined"
            icon={<Iconify icon="mdi:folder-outline" width={14} />}
            sx={{
              fontSize: "11px",
              height: 22,
              borderColor: "divider",
              "& .MuiChip-icon": { ml: 0.5 },
            }}
          />
        )}

        {mode === "edit" && status && <StatusChip status={status} />}
      </Box>

      {actions && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {actions}
        </Box>
      )}
    </Box>
  );
};

TaskHeader.propTypes = {
  mode: PropTypes.oneOf(["create", "edit"]).isRequired,
  name: PropTypes.string,
  onNameChange: PropTypes.func,
  projectName: PropTypes.string,
  status: PropTypes.string,
  actions: PropTypes.node,
  nameEditable: PropTypes.bool,
  backTo: PropTypes.string,
};

export default TaskHeader;
