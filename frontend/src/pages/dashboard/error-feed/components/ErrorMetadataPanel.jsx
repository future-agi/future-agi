import React, { useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Collapse,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
  useTheme,
  alpha,
} from "@mui/material";
import { formatDistanceToNowStrict } from "date-fns";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { paths } from "src/routes/paths";
import { useSnackbar } from "src/components/snackbar";
import {
  useCreateLinearIssue,
  useErrorFeedSidebar,
  useLinearTeams,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/error-feed";
import { useErrorFeedAlerts } from "src/api/errorFeed/alerts";
import { useIntegrationConnections } from "src/api/integrations";
import { useOrgMembers } from "src/api/annotation-queues/annotation-queues";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useSharedIssueSidebar } from "src/api/shared-links";
import openExternal, { safeExternalUrl } from "../openExternal";
import { useErrorFeedStore } from "../store";
import { useSharedIssue } from "../sharedIssueContext";
import ErrorStatusChip from "./ErrorStatusChip";
import ErrorSeverityBadge from "./ErrorSeverityBadge";
import PropTypes from "prop-types";
import { statusColors } from "src/utils/runStatus";

const humanizeTime = (iso) => {
  if (!iso) return "—";
  try {
    return `${formatDistanceToNowStrict(new Date(iso))} ago`;
  } catch {
    return "—";
  }
};

// ── Shared label row ──────────────────────────────────────────────────────────
function FieldRow({ label, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      gap={1}
    >
      <Typography
        sx={{ fontSize: "12px", color: "text.secondary", flexShrink: 0 }}
      >
        {label}
      </Typography>
      {children}
    </Stack>
  );
}
FieldRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

// ── Meta row ────────────────────────────────────────────────────────────────
function MetaRow({ label, value, icon, linkHref }) {
  if (value == null || value === "") return null;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      justifyContent="space-between"
      gap={1}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ minWidth: 0, flexShrink: 0 }}
      >
        {icon && (
          <Iconify
            icon={icon}
            width={12}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            fontSize: "12px",
            color: "text.secondary",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </Typography>
      </Stack>
      {linkHref ? (
        <Typography
          component="a"
          href={linkHref}
          target="_blank"
          rel="noopener noreferrer"
          sx={{
            fontSize: "12px",
            fontWeight: 500,
            color: "primary.main",
            fontFamily: "inherit",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            textDecoration: "none",
            textAlign: "right",
            "&:hover": { textDecoration: "underline" },
          }}
        >
          {value}
        </Typography>
      ) : (
        <Tooltip title={String(value)} placement="top-start">
          <Typography
            sx={{
              fontSize: "12px",
              fontWeight: 500,
              color: "text.primary",
              fontFamily: "inherit",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              textAlign: "right",
            }}
          >
            {value}
          </Typography>
        </Tooltip>
      )}
    </Stack>
  );
}
MetaRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.any,
  icon: PropTypes.string,
  monospace: PropTypes.bool,
  linkHref: PropTypes.string,
};

// ── Section ──────────────────────────────────────────────────────────────────
function Section({ title, children }) {
  return (
    <Stack gap={0.85}>
      <Typography
        sx={{
          fontSize: "11px",
          fontWeight: 600,
          color: "text.tertiary",
          textTransform: "none",
          letterSpacing: "0.01em",
        }}
      >
        {title}
      </Typography>
      {children}
    </Stack>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

// ── Status dropdown ──────────────────────────────────────────────────────────
const STATUS_OPTIONS = [
  {
    value: "escalating",
    label: "Escalating",
    icon: "solar:graph-up-linear",
    role: "danger",
  },
  {
    value: "acknowledged",
    label: "Acknowledged",
    icon: "solar:check-circle-linear",
    role: "neutral",
  },
  {
    value: "for_review",
    label: "For review",
    icon: "solar:eye-linear",
    role: "warning",
  },
  {
    value: "resolved",
    label: "Resolved",
    icon: "solar:check-circle-bold",
    role: "success",
  },
];

function StatusDropdown({ clusterId, current }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const updateIssue = useUpdateErrorFeedIssue();
  const theme = useTheme();
  const cur =
    STATUS_OPTIONS.find((s) => s.value === current) || STATUS_OPTIONS[0];
  const curColors = statusColors(theme, cur.role);

  return (
    <>
      <Box
        onClick={(e) => {
          e.stopPropagation();
          setAnchorEl(e.currentTarget);
        }}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 0.9,
          py: 0.35,
          borderRadius: "4px",
          cursor: "pointer",
          bgcolor: curColors.bg,
          color: curColors.fg,
          "&:hover": { opacity: 0.85 },
          transition: "all 0.15s",
        }}
      >
        <Iconify icon={cur.icon} width={12} sx={{ color: "inherit" }} />
        <Typography fontSize="12px" fontWeight={500} sx={{ color: "inherit" }}>
          {cur.label}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={12}
          sx={{ color: "inherit", opacity: 0.7 }}
        />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          sx: {
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            minWidth: 150,
            mt: 0.5,
          },
        }}
      >
        <Box sx={{ px: 1.5, py: 0.75 }}>
          <Typography
            sx={{
              fontSize: "12px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "none",
              letterSpacing: 0,
            }}
          >
            Change status
          </Typography>
        </Box>
        <Divider sx={{ borderColor: "divider" }} />
        {STATUS_OPTIONS.filter((option) => option.value !== current).map(
          (option) => {
            const statusColor = statusColors(theme, option.role).fg;
            return (
              <MenuItem
                key={option.value}
                onClick={() => {
                  updateIssue.mutate({ clusterId, status: option.value });
                  setAnchorEl(null);
                }}
                sx={{ gap: 1, py: 0.75 }}
              >
                <Iconify
                  icon={option.icon}
                  width={16}
                  sx={{ color: statusColor }}
                />
                <Typography fontSize="12px">{option.label}</Typography>
              </MenuItem>
            );
          },
        )}
      </Menu>
    </>
  );
}
StatusDropdown.propTypes = {
  clusterId: PropTypes.string,
  current: PropTypes.string,
};

// ── Severity dropdown ─────────────────────────────────────────────────────────
const SEVERITY_OPTIONS = [
  {
    value: "critical",
    label: "Critical",
    role: "danger",
    icon: "solar:danger-circle-linear",
  },
  {
    value: "high",
    label: "High",
    role: "warning",
    icon: "solar:danger-circle-linear",
  },
  {
    value: "medium",
    label: "Medium",
    role: "info",
    icon: "solar:danger-triangle-linear",
  },
  {
    value: "low",
    label: "Low",
    role: "neutral",
    icon: "solar:info-circle-linear",
  },
];

function SeverityDropdown({ current, onChange }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const cur =
    SEVERITY_OPTIONS.find((s) => s.value === current) || SEVERITY_OPTIONS[1];

  return (
    <>
      <Box
        onClick={(e) => {
          e.stopPropagation();
          setAnchorEl(e.currentTarget);
        }}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 0.9,
          py: 0.35,
          borderRadius: "4px",
          cursor: "pointer",
          border: "1px solid",
          borderColor: `status.${cur.role}.border`,
          bgcolor: `status.${cur.role}.bg`,
          "&:hover": { borderColor: `status.${cur.role}.solid` },
          transition: "all 0.15s",
        }}
      >
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: `status.${cur.role}.solid`,
            flexShrink: 0,
          }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          sx={{ color: `status.${cur.role}.fg` }}
        >
          {cur.label}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={12}
          sx={{ color: `status.${cur.role}.fg`, opacity: 0.7 }}
        />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          sx: {
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            minWidth: 200,
            mt: 0.5,
          },
        }}
      >
        <Box sx={{ px: 1.5, py: 0.75 }}>
          <Typography
            sx={{
              fontSize: "12px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "none",
              letterSpacing: 0,
            }}
          >
            Change severity
          </Typography>
        </Box>
        <Divider sx={{ borderColor: "divider" }} />
        {SEVERITY_OPTIONS.filter((s) => s.value !== current).map((s) => {
          return (
            <MenuItem
              key={s.value}
              onClick={() => {
                onChange?.(s.value);
                setAnchorEl(null);
              }}
              sx={{ gap: 1, py: 0.75 }}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: `status.${s.role}.solid`,
                  flexShrink: 0,
                }}
              />
              <Typography fontSize="12px" sx={{ color: `status.${s.role}.fg` }}>
                {s.label}
              </Typography>
            </MenuItem>
          );
        })}
      </Menu>
    </>
  );
}
SeverityDropdown.propTypes = {
  current: PropTypes.string,
  onChange: PropTypes.func,
};

// ── Assignee dropdown ─────────────────────────────────────────────────────────
function getInitials(name, email) {
  if (name) {
    const parts = name.trim().split(/\s+/);
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : name.slice(0, 2).toUpperCase();
  }
  return (email || "??").slice(0, 2).toUpperCase();
}

function AssigneeDropdown({ current, onChange, members = [] }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const member = members.find((m) => m.email === current);

  return (
    <>
      <Box
        onClick={(e) => {
          e.stopPropagation();
          setAnchorEl(e.currentTarget);
        }}
        aria-haspopup="menu"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.6,
          px: 0.75,
          py: 0.3,
          borderRadius: "4px",
          cursor: "pointer",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "action.hover",
          "&:hover": {
            borderColor: "status.discovery.solid",
            bgcolor: "status.discovery.bg",
          },
          transition: "all 0.15s",
        }}
      >
        {member ? (
          <>
            <Box
              sx={{
                width: 16,
                height: 16,
                borderRadius: "50%",
                bgcolor: "status.discovery.bg",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <Typography
                fontSize="12px"
                fontWeight={600}
                sx={{ color: "status.discovery.fg" }}
              >
                {getInitials(member.name, member.email)}
              </Typography>
            </Box>
            <Typography
              fontSize="12px"
              color="text.primary"
              noWrap
              sx={{ maxWidth: 90 }}
            >
              {member.name || member.email.split("@")[0]}
            </Typography>
          </>
        ) : (
          <>
            <Iconify
              icon="solar:user-plus-linear"
              width={12}
              sx={{ color: "text.secondary" }}
            />
            <Typography fontSize="12px" color="text.secondary">
              Assign
            </Typography>
          </>
        )}
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={12}
          sx={{ color: "text.secondary", opacity: 0.75 }}
        />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          sx: {
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            minWidth: 200,
            mt: 0.5,
          },
        }}
      >
        <Box sx={{ px: 1.5, py: 0.75 }}>
          <Typography
            sx={{
              fontSize: "12px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "none",
              letterSpacing: 0,
            }}
          >
            Assign to
          </Typography>
        </Box>
        <Divider sx={{ borderColor: "divider" }} />
        {current && (
          <MenuItem
            onClick={() => {
              onChange?.(null);
              setAnchorEl(null);
            }}
            sx={{ gap: 1, py: 0.75 }}
          >
            <Iconify
              icon="solar:user-minus-linear"
              width={16}
              sx={{ color: "text.disabled" }}
            />
            <Typography fontSize="12px" color="text.secondary">
              Unassign
            </Typography>
          </MenuItem>
        )}
        {members
          .filter((m) => m.email !== current)
          .map((m) => (
            <MenuItem
              key={m.email}
              onClick={() => {
                onChange?.(m.email);
                setAnchorEl(null);
              }}
              sx={{ gap: 1, py: 0.75 }}
            >
              <Box
                sx={{
                  width: 20,
                  height: 20,
                  borderRadius: "50%",
                  bgcolor: "status.discovery.bg",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <Typography
                  fontSize="12px"
                  fontWeight={600}
                  sx={{ color: "status.discovery.fg" }}
                >
                  {getInitials(m.name, m.email)}
                </Typography>
              </Box>
              <Typography fontSize="12px" color="text.primary">
                {m.name || m.email.split("@")[0]}
              </Typography>
            </MenuItem>
          ))}
      </Menu>
    </>
  );
}
AssigneeDropdown.propTypes = {
  current: PropTypes.string,
  onChange: PropTypes.func,
  members: PropTypes.array,
};

// ── Co-occurring issues ───────────────────────────────────────────────────────
function CoOccurringList({ issues, readOnly = false }) {
  const navigate = useNavigate();
  return (
    <Stack gap={0.75}>
      {issues.map((issue) => (
        <Stack
          key={issue.id}
          gap={0.2}
          onClick={
            readOnly
              ? undefined
              : () => navigate(paths.dashboard.errorFeed.detail(issue.id))
          }
          sx={{
            py: 0.5,
            px: 0.75,
            mx: -0.75,
            borderRadius: "6px",
            cursor: readOnly ? "default" : "pointer",
            "&:hover": readOnly ? undefined : { bgcolor: "action.hover" },
          }}
        >
          <Typography typography="s1" color="text.primary" noWrap>
            {issue.title}
          </Typography>
          <Typography typography="s3" color="text.tertiary">
            {issue.count?.toLocaleString()} shared{" "}
            {issue.count === 1 ? "trace" : "traces"}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
CoOccurringList.propTypes = {
  issues: PropTypes.array.isRequired,
  readOnly: PropTypes.bool,
};

// ── Integrations ──────────────────────────────────────────────────────────────

export function LinearTeamPicker({ open, onClose, clusterId, traceId }) {
  const { enqueueSnackbar } = useSnackbar();
  // Use the live active org (matches the X-Organization-Id header) instead of
  // the cached user.organization.id, which can drift and 404 the request
  // (TH-6156).
  const { currentOrganizationId } = useOrganization();
  const {
    data: linearData,
    isLoading: teamsLoading,
    isError: teamsError,
  } = useLinearTeams(currentOrganizationId, { enabled: open });
  const createIssue = useCreateLinearIssue();
  const teams = linearData?.teams ?? [];

  const handleCreate = (teamId) => {
    createIssue.mutate(
      { clusterId, teamId, traceId },
      {
        onSuccess: (res) => {
          // Wire form is snake_case; tolerate the axios camelCase aliases.
          const result = res?.data?.result;
          const issueId = result?.issue_id ?? result?.issueId;
          const issueUrl = result?.issue_url ?? result?.issueUrl;
          const alreadyLinked = result?.already_linked ?? result?.alreadyLinked;
          if (alreadyLinked) {
            openExternal(issueUrl);
          } else {
            enqueueSnackbar(
              issueId ? `Created ${issueId}` : "Linear issue created",
              {
                variant: "success",
              },
            );
            openExternal(issueUrl);
          }
          onClose();
        },
        onError: (err) => {
          const message =
            err?.response?.data?.result || "Failed to create Linear issue";
          enqueueSnackbar(message, { variant: "error" });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: {
          maxWidth: 560,
          borderRadius: "12px",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <DialogTitle sx={{ pb: 1, pt: 2, px: 2.5 }}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          <Stack gap={0.2}>
            <Typography fontSize="13px" fontWeight={600} color="text.primary">
              Create Linear Issue
            </Typography>
            <Typography fontSize="12px" color="text.secondary">
              Select a team to create the issue in.
            </Typography>
          </Stack>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ px: 2.5, pb: 2.5, pt: 0 }}>
        {teams.length === 0 ? (
          <Typography fontSize="12px" color="text.disabled" sx={{ py: 2 }}>
            {teamsLoading
              ? "Loading teams…"
              : teamsError
                ? "Couldn't reach Linear. Check the integration in Settings and try again."
                : linearData?.connected === false
                  ? "Linear isn't connected for this workspace. Connect it in Settings > Integrations."
                  : "No teams found in your Linear workspace."}
          </Typography>
        ) : (
          <Stack gap={0.75} sx={{ mt: 1 }}>
            {teams.map((t) => (
              <Stack
                key={t.id}
                direction="row"
                alignItems="center"
                gap={1.25}
                onClick={() => !createIssue.isPending && handleCreate(t.id)}
                sx={{
                  px: 1.25,
                  py: 0.85,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  cursor: createIssue.isPending ? "wait" : "pointer",
                  "&:hover": {
                    // eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear)
                    borderColor: "#5E6AD2",
                    // eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear)
                    bgcolor: alpha("#5E6AD2", 0.04),
                  },
                  transition: "all 0.15s",
                }}
              >
                <Box
                  sx={{
                    width: 28,
                    height: 28,
                    borderRadius: "6px",
                    // eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear)
                    bgcolor: alpha("#5E6AD2", 0.1),
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear) */}
                  <Typography fontSize="12px" fontWeight={600} color="#5E6AD2">
                    {t.key}
                  </Typography>
                </Box>
                <Typography
                  fontSize="12px"
                  fontWeight={600}
                  color="text.primary"
                  flex={1}
                >
                  {t.name}
                </Typography>
                {createIssue.isPending && (
                  // eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear)
                  <CircularProgress size={14} sx={{ color: "#5E6AD2" }} />
                )}
              </Stack>
            ))}
          </Stack>
        )}
      </DialogContent>
    </Dialog>
  );
}
LinearTeamPicker.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  clusterId: PropTypes.string.isRequired,
  traceId: PropTypes.string,
};

function ConnectorRow({ icon, color, name, subtitle, action, onAction }) {
  const theme = useTheme();

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        px: 1.25,
        py: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      <Box
        sx={{
          width: 24,
          height: 24,
          borderRadius: "6px",
          bgcolor: alpha(color, 0.12),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={14} sx={{ color }} />
      </Box>

      <Stack gap={0} flex={1} minWidth={0}>
        <Typography typography="s1" fontWeight={600} color="text.primary" noWrap>
          {name}
        </Typography>
        <Typography
          typography="s3"
          noWrap
          color={subtitle === "Connected" ? undefined : "text.secondary"}
          sx={
            subtitle === "Connected"
              ? { color: statusColors(theme, "success").fg }
              : undefined
          }
        >
          {subtitle}
        </Typography>
      </Stack>

      {action && (
        <Button
          size="small"
          variant="outlined"
          onClick={onAction}
          sx={{
            whiteSpace: "nowrap",
            flexShrink: 0,
            borderColor: "divider",
            color: "text.primary",
          }}
        >
          {action}
        </Button>
      )}
    </Stack>
  );
}
ConnectorRow.propTypes = {
  icon: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  subtitle: PropTypes.string.isRequired,
  action: PropTypes.string,
  onAction: PropTypes.func,
};

function SharedLinearLink({ url, issueId }) {
  const href = safeExternalUrl(url);
  if (url && !href) {
    return (
      <Typography fontSize="12px" color="text.secondary">
        {issueId || "Linear issue"}
      </Typography>
    );
  }
  if (!url) {
    return (
      <Typography fontSize="12px" color="text.disabled">
        No linked issue
      </Typography>
    );
  }
  return (
    <Stack direction="row" alignItems="center" gap={0.75}>
      <Iconify icon="simple-icons:linear" width={12} />
      <Link
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        underline="hover"
        fontSize="12px"
      >
        {issueId || "Linear issue"}
      </Link>
    </Stack>
  );
}
SharedLinearLink.propTypes = {
  url: PropTypes.string,
  issueId: PropTypes.string,
};

function Integrations({
  clusterId,
  traceId,
  projectId,
  externalIssueUrl,
  externalIssueId,
}) {
  const theme = useTheme();
  const navigate = useNavigate();
  // Live active org (matches the X-Organization-Id header) rather than the
  // cached user.organization.id, which can drift and 404 the request (TH-6156).
  const { currentOrganizationId } = useOrganization();
  const {
    data: linearData,
    isLoading: linearLoading,
    isError: linearError,
  } = useLinearTeams(currentOrganizationId);
  const { data: alertRules = [] } = useErrorFeedAlerts();
  const { data: connections = [] } = useIntegrationConnections();
  const linearConnected = linearData?.connected === true;
  const slackConnected = connections.some((c) => c.platform === "slack");
  const matchingAlertRules = alertRules.filter(
    (r) =>
      r.enabled !== false &&
      (!r.project_id || String(r.project_id) === String(projectId)),
  );
  const [teamPickerOpen, setTeamPickerOpen] = useState(false);

  const linked = !!externalIssueUrl;

  const handleLinearAction = () => {
    if (linked) {
      openExternal(externalIssueUrl);
      return;
    }
    if (!linearConnected) {
      navigate(paths.dashboard.settings.integrations);
      return;
    }
    setTeamPickerOpen(true);
  };

  const handleSlackAction = () => {
    const q = new URLSearchParams();
    if (matchingAlertRules.length === 0) {
      q.set("create", "1");
      if (projectId) q.set("project_id", projectId);
    }
    const qs = q.toString();
    navigate(`${paths.dashboard.errorFeed.alerts}${qs ? `?${qs}` : ""}`);
  };

  const firstAlertRule = matchingAlertRules[0] || null;
  const alertChannelsSummary = (() => {
    if (!firstAlertRule) {
      return slackConnected ? "Off" : "Not set up";
    }
    const parts = [];
    if (firstAlertRule.slack_enabled && firstAlertRule.slack_channel_name) {
      parts.push(`#${firstAlertRule.slack_channel_name}`);
    }
    const emails = firstAlertRule.email_recipients || [];
    if (firstAlertRule.email_enabled && emails.length > 0) {
      parts.push(emails.length === 1 ? "Email" : `Email (${emails.length})`);
    }
    return parts.length > 0 ? parts.join(" · ") : "Active";
  })();
  const alertAction = matchingAlertRules.length > 0 ? "Manage" : "Set up";

  const linearSubtitle = linked
    ? externalIssueId
    : linearLoading
      ? "Checking…"
      : linearError
        ? "Connection unreachable"
        : linearConnected
          ? "Connected"
          : "Not connected";
  const linearAction = linked
    ? `View ${externalIssueId ?? "issue"}`
    : linearLoading
      ? null
      : linearConnected
        ? "Create issue"
        : "Connect";

  return (
    <>
      <Stack gap={0.75}>
        <ConnectorRow
          icon="solar:bell-bing-linear"
          color={
            matchingAlertRules.length > 0
              ? theme.palette.status.success.solid
              : theme.palette.status.warning.solid
          }
          name="Alerts"
          subtitle={alertChannelsSummary}
          action={alertAction}
          onAction={handleSlackAction}
        />
        <ConnectorRow
          icon="simple-icons:linear"
          // eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear)
          color="#5E6AD2"
          name="Linear"
          subtitle={linearSubtitle}
          action={linearAction}
          onAction={handleLinearAction}
        />
      </Stack>

      {teamPickerOpen && (
        <LinearTeamPicker
          open={teamPickerOpen}
          onClose={() => setTeamPickerOpen(false)}
          clusterId={clusterId}
          traceId={traceId}
        />
      )}
    </>
  );
}
Integrations.propTypes = {
  clusterId: PropTypes.string,
  traceId: PropTypes.string,
  projectId: PropTypes.string,
  externalIssueUrl: PropTypes.string,
  externalIssueId: PropTypes.string,
};

// Backend returns `"llm_judge" | "deterministic"` — EvalRow expects
// `"llm" | "pass" | "deterministic"`. Translate on the way in.
const EVAL_TYPE_MAP = {
  llm_judge: "llm",
  deterministic: "deterministic",
};

export default function ErrorMetadataPanel({ error }) {
  const [severity, setSeverityLocal] = useState(error?.severity ?? "high");
  const [assignee, setAssigneeLocal] = useState(error?.assignees?.[0] ?? null);
  // Use the live active org (matches the X-Organization-Id header) instead of
  // the cached user.organization.id, which can drift and 404 the members
  // request (TH-6156).
  // A shared issue is read-only and its viewer may be signed out: nothing
  // here fetches, and triage shows as text.
  const shared = useSharedIssue();
  const { currentOrganizationId } = useOrganization();
  const { data: orgMembers = [] } = useOrgMembers(
    shared ? null : currentOrganizationId,
  );
  const updateIssue = useUpdateErrorFeedIssue();

  const setSeverity = (val) => {
    setSeverityLocal(val);
    updateIssue.mutate({ clusterId: error?.cluster_id, severity: val });
  };
  const setAssignee = (val) => {
    setAssigneeLocal(val);
    updateIssue.mutate({ clusterId: error?.cluster_id, assignee: val });
  };

  const selectedTraceId = useErrorFeedStore(
    (s) => s.selectedTraceIdByCluster[error?.cluster_id] ?? null,
  );
  const fetchedSidebar = useErrorFeedSidebar(
    shared ? null : error?.cluster_id,
    selectedTraceId,
  );
  // Like the issue page, AI metadata and evaluations follow the picked trace.
  const sharedTraceSidebar = useSharedIssueSidebar(
    shared?.token,
    shared ? selectedTraceId : null,
  );
  // The share's own sidebar is the default trace's; once a trace is picked,
  // show only that trace's sidebar, never the default's in its place.
  const sidebar = shared
    ? selectedTraceId
      ? sharedTraceSidebar.data
      : shared.sidebar
    : fetchedSidebar.data;
  const isSidebarLoading = shared
    ? !!selectedTraceId && sharedTraceSidebar.isLoading
    : fetchedSidebar.isLoading;
  const sidebarPending = isSidebarLoading && !sidebar;
  // Must match OverviewTab's trace source or the two read different cache entries.
  const effectiveTraceId =
    selectedTraceId ?? sidebar?.ai_metadata?.trace_id ?? null;

  const [relatedOpen, setRelatedOpen] = useState(false);
  const [idCopied, setIdCopied] = useState(false);

  if (!error) return null;

  const qualityScores = (sidebar?.evaluations ?? []).map((ev) => ({
    label: ev.label,
    type: EVAL_TYPE_MAP[ev.type] ?? ev.type,
    result: ev.result,
    score: ev.score ?? undefined,
    value: ev.value ?? undefined,
  }));
  const coOccurringIssues = sidebar?.co_occurring_issues ?? [];

  const fixLayerLabel = error.fix_layer
    ? String(error.fix_layer).replace(/^\w/, (c) => c.toUpperCase())
    : null;

  const handleCopyId = async () => {
    if (!error.cluster_id) return;
    await copyToClipboard(error.cluster_id);
    setIdCopied(true);
    setTimeout(() => setIdCopied(false), 1500);
  };

  return (
    <Box
      sx={{
        width: 268,
        flexShrink: 0,
        borderLeft: "1px solid",
        borderColor: "divider",
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Stack gap={2.25} sx={{ p: 2, pb: 9 }}>
        {/* ── Triage (single source of truth for Status & Severity) ── */}
        <Section title="Triage">
          <Stack gap={0.9}>
            <FieldRow label="Status">
              {shared ? (
                <ErrorStatusChip status={error.status} />
              ) : (
                <StatusDropdown
                  clusterId={error.cluster_id}
                  current={error.status}
                />
              )}
            </FieldRow>
            <FieldRow label="Severity">
              {shared ? (
                <ErrorSeverityBadge
                  severity={error.severity}
                  assessmentStatus={error.severity_assessment_status}
                  source={error.severity_source}
                  reason={error.severity_reason}
                />
              ) : (
                <SeverityDropdown current={severity} onChange={setSeverity} />
              )}
            </FieldRow>
            <FieldRow label="Owner">
              {shared ? (
                <Typography typography="s1" color="text.primary" noWrap>
                  {assignee || "Unassigned"}
                </Typography>
              ) : (
                <AssigneeDropdown
                  current={assignee}
                  onChange={setAssignee}
                  members={orgMembers}
                />
              )}
            </FieldRow>
          </Stack>
        </Section>

        {/* ── Issue Details (non-duplicated rail facts; users/traces/project live in header) ── */}
        <Section title="Details">
          <Stack gap={0.7}>
            {fixLayerLabel && <MetaRow label="Fix layer" value={fixLayerLabel} />}
            {sidebar?.ai_metadata?.trace_id && (
              <MetaRow label="Trace ID" value={sidebar.ai_metadata.trace_id} />
            )}
            <MetaRow
              label="First seen"
              value={error.firstSeenHuman ?? humanizeTime(error.first_seen)}
            />
            <MetaRow
              label="Last seen"
              value={error.lastSeenHuman ?? humanizeTime(error.last_seen)}
            />
            <FieldRow label="Issue ID">
              <Stack direction="row" alignItems="center" gap={0.5}>
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ fontFamily: "ui-monospace, SFMono-Regular, monospace" }}
                >
                  {String(error.cluster_id || "").slice(0, 8)}
                </Typography>
                <Tooltip title={idCopied ? "Copied" : "Copy issue ID"} arrow>
                  <IconButton
                    size="small"
                    aria-label="Copy issue ID"
                    onClick={handleCopyId}
                    sx={{ p: 0.25, color: "text.secondary" }}
                  >
                    <Iconify
                      icon={idCopied ? "solar:check-linear" : "solar:copy-linear"}
                      width={14}
                    />
                  </IconButton>
                </Tooltip>
              </Stack>
            </FieldRow>
          </Stack>
        </Section>

        {/* ── Verification Checks (calm semantic colours, never red-washed) ── */}
        {sidebarPending ? (
          <Section title="Checks">
            <Stack gap={0.75}>
              {[0, 1].map((i) => (
                <Skeleton key={i} height={18} sx={{ borderRadius: "4px" }} />
              ))}
            </Stack>
          </Section>
        ) : (
          qualityScores.length > 0 && (
            <Section title="Checks">
              <Stack gap={0.65}>
                {qualityScores.map((qs) => {
                  const isConfirmed = qs.result === "CONFIRMED" || qs.value === "Confirmed";
                  const isPath = qs.result === "INFO" || qs.type === "info";
                  return (
                    <FieldRow key={qs.label} label={qs.label}>
                      <Typography
                        typography="s3"
                        fontWeight={500}
                        color={
                          isPath
                            ? "text.primary"
                            : isConfirmed
                              ? "status.warning.fg"
                              : qs.result === "FAIL"
                                ? "status.danger.fg"
                                : "status.success.fg"
                        }
                        sx={
                          isPath
                            ? { fontFamily: "ui-monospace, SFMono-Regular, monospace" }
                            : undefined
                        }
                        noWrap
                      >
                        {qs.value || qs.result}
                      </Typography>
                    </FieldRow>
                  );
                })}
              </Stack>
            </Section>
          )
        )}

        {coOccurringIssues.length > 0 && (
          <Stack gap={0.75}>
            <Tooltip
              title="Other issues detected on the same user traces/sessions"
              arrow
              placement="left"
            >
              <Box
                component="button"
                type="button"
                aria-expanded={relatedOpen}
                onClick={() => setRelatedOpen((v) => !v)}
                sx={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  border: 0,
                  p: 0,
                  bgcolor: "transparent",
                  fontFamily: "inherit",
                  textAlign: "left",
                  cursor: "pointer",
                }}
              >
                <Stack gap={0}>
                  <Typography typography="s3" fontWeight={600} color="text.secondary">
                    Co-occurring issues ({coOccurringIssues.length})
                  </Typography>
                  <Typography typography="s3" color="text.tertiary">
                    Seen on same traces
                  </Typography>
                </Stack>
                <Iconify
                  icon={
                    relatedOpen
                      ? "solar:alt-arrow-up-linear"
                      : "solar:alt-arrow-down-linear"
                  }
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
              </Box>
            </Tooltip>
            <Collapse in={relatedOpen}>
              <CoOccurringList issues={coOccurringIssues} readOnly={!!shared} />
            </Collapse>
          </Stack>
        )}

        <Stack gap={1}>
          <Typography
            typography="s3"
            fontWeight={600}
            color="text.tertiary"
            sx={{ letterSpacing: "0.01em" }}
          >
            Alerts & integrations
          </Typography>
          {shared ? (
            <SharedLinearLink
              url={error?.external_issue_url}
              issueId={error?.external_issue_id}
            />
          ) : (
            <Integrations
              clusterId={error?.cluster_id}
              traceId={effectiveTraceId}
              projectId={error?.project_id}
              externalIssueUrl={error?.external_issue_url}
              externalIssueId={error?.external_issue_id}
            />
          )}
        </Stack>
      </Stack>
    </Box>
  );
}

ErrorMetadataPanel.propTypes = {
  error: PropTypes.object,
};
