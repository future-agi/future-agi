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
import StatusBadge from "src/components/status-badge";
import _ from "lodash";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

// The same roles and icons as the Tasks list Status column, so a task reads
// the same in the list and on its own page.
const STATUS_CONFIG = {
  pending: { role: "neutral", icon: "solar:clock-circle-linear" },
  running: { role: "info", icon: "svg-spinners:ring-resize" },
  completed: { role: "success", icon: "solar:check-circle-linear" },
  failed: { role: "danger", icon: "solar:close-circle-linear" },
  paused: { role: "warning", icon: "solar:pause-circle-linear" },
};

const StatusChip = ({ status }) => {
  const normalized = (status || "").toLowerCase();
  const cfg = STATUS_CONFIG[normalized] || STATUS_CONFIG.pending;
  return (
    <StatusBadge
      status={normalized}
      label={_.capitalize(normalized || "unknown")}
      role={cfg.role}
      icon={<Iconify icon={cfg.icon} width={14} />}
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
  selectionSummary,
  outcome,
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
        flexWrap: "wrap",
        gap: 1,
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
              <Iconify icon="solar:arrow-left-linear" width={20} />
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
                  fontSize: "13px",
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
                component="h1"
                sx={{
                  fontSize: "14px",
                  overflowWrap: "anywhere",
                }}
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
            icon={<Iconify icon="solar:folder-linear" width={14} />}
            sx={{
              borderColor: "divider",
              "& .MuiChip-icon": { ml: 0.5 },
            }}
          />
        )}

        {mode === "edit" && status && <StatusChip status={status} />}
        {mode === "edit" && outcome}
      </Box>

      {actions && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          {actions}
        </Box>
      )}
      {selectionSummary && (
        <Box sx={{ flexBasis: "100%", minWidth: 0 }}>{selectionSummary}</Box>
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
  selectionSummary: PropTypes.node,
  // What the last run produced, beside its status (edit mode).
  outcome: PropTypes.node,
};

export default TaskHeader;
