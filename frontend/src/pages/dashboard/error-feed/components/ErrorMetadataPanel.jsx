import React, { useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
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
import { paths } from "src/routes/paths";
import { useSnackbar } from "src/components/snackbar";
import {
  useCreateLinearIssue,
  useErrorFeedSidebar,
  useLinearTeams,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/mockAdapter";
import { useOrgMembers } from "src/api/annotation-queues/annotation-queues";
import { useOrganization } from "src/contexts/OrganizationContext";
import openExternal from "../openExternal";
import { useErrorFeedStore } from "../store";
import PropTypes from "prop-types";
import { useIssueFix } from "src/sections/simulate-v2/errorFeed/issueFix";
import { triageFixEffect } from "src/sections/simulate-v2/errorFeed/issueFixModel";
import { useAgentImprovementFlag } from "src/sections/simulate-v2/_mock/featureFlags";
import { statusColors } from "src/utils/runStatus";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";
import IssueStatusChip from "./IssueStatusChip";

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
function MetaRow({ label, value, icon, monospace, linkHref }) {
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
    <Stack gap={1}>
      <Typography
        sx={{
          fontSize: "12px",
          fontWeight: 600,
          color: "text.secondary",
          textTransform: "none",
          letterSpacing: 0,
        }}
      >
        {title}
      </Typography>
      {children}
      <Divider sx={{ borderColor: "divider", mt: 0.25 }} />
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

function StatusDropdown({ clusterId, current, fix, onReopen }) {
  const [anchorEl, setAnchorEl] = useState(null);
  /* The issue's own triage status — a fix shows beside it, never instead.
     Escalating a watched or resolved fix reopens it (triageFixEffect). */
  const ruleOf = (value) => ({
    disabled: false,
    note: triageFixEffect(fix, value)?.note || "",
  });
  const updateIssue = useUpdateErrorFeedIssue();
  const theme = useTheme();
  const cur =
    STATUS_OPTIONS.find((s) => s.value === current) || STATUS_OPTIONS[0];
  const curColors = statusColors(theme, cur.role);

  return (
    <>
      <Box
        {...clickable(
          (e) => {
            e.stopPropagation();
            setAnchorEl(e.currentTarget);
          },
          { label: `Status: ${cur.label}`, expanded: Boolean(anchorEl) },
        )}
        aria-haspopup="menu"
        sx={{
          ...focusRing,
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
            minWidth: 200,
            maxWidth: 320,
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
            const rule = ruleOf(option.value);
            return (
              <MenuItem
                key={option.value}
                disabled={rule.disabled}
                onClick={() => {
                  updateIssue.mutate({ clusterId, status: option.value });
                  const effect = triageFixEffect(fix, option.value);
                  if (effect) onReopen?.(effect.reopen);
                  setAnchorEl(null);
                }}
                sx={{
                  gap: 1,
                  py: 0.75,
                  ...(rule.note && { alignItems: "flex-start" }),
                }}
              >
                <Iconify
                  icon={option.icon}
                  width={16}
                  sx={{ color: statusColor, ...(rule.note && { mt: "1px" }) }}
                />
                <Box minWidth={0}>
                  <Typography fontSize="12px">{option.label}</Typography>
                  {rule.note && (
                    <Typography
                      sx={{
                        fontSize: "12px",
                        color: "text.secondary",
                        whiteSpace: "normal",
                      }}
                    >
                      {rule.note}
                    </Typography>
                  )}
                </Box>
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
  fix: PropTypes.object,
  onReopen: PropTypes.func,
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
        {...clickable(
          (e) => {
            e.stopPropagation();
            setAnchorEl(e.currentTarget);
          },
          { label: `Severity: ${cur.label}`, expanded: Boolean(anchorEl) },
        )}
        aria-haspopup="menu"
        sx={{
          ...focusRing,
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
        {...clickable(
          (e) => {
            e.stopPropagation();
            setAnchorEl(e.currentTarget);
          },
          {
            label: `Assignee: ${member ? member.name || member.email : "unassigned"}`,
            expanded: Boolean(anchorEl),
          },
        )}
        aria-haspopup="menu"
        sx={{
          ...focusRing,
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
              sx={{ color: "text.disabled" }}
            />
            <Typography fontSize="12px" color="text.disabled">
              Assign
            </Typography>
          </>
        )}
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={12}
          sx={{ color: "text.disabled", opacity: 0.7 }}
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

// ── Eval row ──────────────────────────────────────────────────────────────────
function EvalRow({ label, type, result, score, value }) {
  const theme = useTheme();

  const passed = result === "passed";
  const resultColors = statusColors(theme, passed ? "success" : "danger");
  const resultColor = resultColors.solid;
  const resultIcon = passed
    ? "solar:check-circle-linear"
    : "solar:close-circle-linear";

  // Deterministic: use the returned value as the display label (coloured by pass/fail)
  // Pass/Fail: "Passed" / "Failed"
  const verdictLabel =
    type === "deterministic" ? value ?? "—" : passed ? "Passed" : "Failed";

  return (
    <Stack
      gap={0.55}
      sx={{
        px: 1,
        py: 0.75,
        borderRadius: "6px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
      }}
    >
      {/* Header: name only */}
      <Typography
        fontSize="12px"
        fontWeight={500}
        color="text.primary"
        noWrap
        sx={{ flex: 1, minWidth: 0 }}
      >
        {label}
      </Typography>

      {/* LLM Judge — score bar + percentage only (no pass/fail chip) */}
      {type === "llm" && score != null && (
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Box
            sx={{
              flex: 1,
              height: 3,
              borderRadius: 2,
              overflow: "hidden",
              bgcolor: "action.selected",
            }}
          >
            <Box
              sx={{
                width: `${score * 100}%`,
                height: "100%",
                bgcolor: resultColor,
                borderRadius: 2,
              }}
            />
          </Box>
          <Typography
            fontSize="12px"
            fontWeight={600}
            sx={{
              color: "text.secondary",
              fontFeatureSettings: "'tnum'",
              flexShrink: 0,
            }}
          >
            {Math.round(score * 100)}%
          </Typography>
        </Stack>
      )}

      {/* Pass/Fail + Deterministic — verdict label with result colour */}
      {(type === "pass" || type === "deterministic") && (
        <Stack direction="row" alignItems="center" gap={0.35}>
          <Iconify
            icon={resultIcon}
            width={12}
            sx={{ color: resultColors.fg }}
          />
          <Typography
            fontSize="12px"
            fontWeight={500}
            sx={{ color: resultColors.fg }}
          >
            {verdictLabel}
          </Typography>
        </Stack>
      )}
    </Stack>
  );
}
EvalRow.propTypes = {
  label: PropTypes.string,
  type: PropTypes.string,
  result: PropTypes.string,
  score: PropTypes.number,
  value: PropTypes.string,
};

// ── Activity timeline item ────────────────────────────────────────────────────
function ActivityItem({ text, meta, isLast }) {
  return (
    <Stack direction="row" gap={1} alignItems="flex-start">
      {/* Timeline spine */}
      <Stack alignItems="center" sx={{ flexShrink: 0, width: 10 }}>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            flexShrink: 0,
            border: "1.5px solid",
            borderColor: "text.disabled",
            bgcolor: "action.selected",
            mt: "3px",
          }}
        />
        {!isLast && (
          <Box
            sx={{
              width: 1.5,
              flex: 1,
              minHeight: 14,
              bgcolor: "divider",
              mt: "2px",
            }}
          />
        )}
      </Stack>
      <Stack gap={0} pb={isLast ? 0 : 1}>
        <Typography
          fontSize="12px"
          color="text.primary"
          sx={{ lineHeight: 1.5 }}
        >
          {text}
        </Typography>
        <Typography fontSize="12px" color="text.secondary">
          {meta}
        </Typography>
      </Stack>
    </Stack>
  );
}
ActivityItem.propTypes = {
  text: PropTypes.string,
  meta: PropTypes.string,
  isLast: PropTypes.bool,
};

// ── Co-occurring issues ───────────────────────────────────────────────────────

function CoOccurringList({ issues }) {
  const navigate = useNavigate();
  return (
    <Stack gap={0.5}>
      {issues.map((issue) => {
        return (
          <Stack
            key={issue.id}
            direction="row"
            alignItems="center"
            gap={0.75}
            {...clickable(
              () => navigate(paths.dashboard.errorFeed.detail(issue.id)),
              { role: "link" },
            )}
            sx={{
              ...focusRing,
              px: 1,
              py: 0.7,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor: "transparent",
              cursor: "pointer",
              "&:hover": {
                borderColor: "status.discovery.border",
                bgcolor: "status.discovery.bg",
              },
              transition: "all 0.15s",
            }}
          >
            <Stack gap={0.1} flex={1} minWidth={0}>
              <Typography fontSize="12px" color="text.primary" noWrap>
                {issue.title}
              </Typography>
              <Typography fontSize="12px" color="text.secondary">
                {issue.count?.toLocaleString()} shared
                {issue.co_occurrence != null &&
                  ` · ${Math.round(issue.co_occurrence * 100)}%`}
              </Typography>
            </Stack>
          </Stack>
        );
      })}
    </Stack>
  );
}
CoOccurringList.propTypes = { issues: PropTypes.array.isRequired };

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
                {...clickable(
                  () => !createIssue.isPending && handleCreate(t.id),
                )}
                sx={{
                  ...focusRing,
                  px: 1.25,
                  py: 0.85,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  cursor: createIssue.isPending ? "wait" : "pointer",
                  "&:hover": {
                    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                    borderColor: "#5E6AD2",
                    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
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
                    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                    bgcolor: alpha("#5E6AD2", 0.1),
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {/* eslint-disable-next-line no-restricted-syntax -- ds: data colour */}
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
                  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
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
        px: 1,
        py: 0.7,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        bgcolor: "transparent",
      }}
    >
      <Box
        sx={{
          width: 22,
          height: 22,
          borderRadius: "4px",
          bgcolor: alpha(color, 0.1),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={14} sx={{ color }} />
      </Box>

      <Stack gap={0} flex={1} minWidth={0}>
        <Typography fontSize="12px" fontWeight={600} color="text.primary">
          {name}
        </Typography>
        <Typography
          fontSize="12px"
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
          sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
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

function Integrations({
  clusterId,
  traceId,
  externalIssueUrl,
  externalIssueId,
}) {
  const navigate = useNavigate();
  // Live active org (matches the X-Organization-Id header) rather than the
  // cached user.organization.id, which can drift and 404 the request (TH-6156).
  const { currentOrganizationId } = useOrganization();
  const {
    data: linearData,
    isLoading: linearLoading,
    isError: linearError,
  } = useLinearTeams(currentOrganizationId);
  const linearConnected = linearData?.connected === true;
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
          icon="simple-icons:linear"
          // eslint-disable-next-line no-restricted-syntax -- ds: data colour
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
  const improving = useAgentImprovementFlag();
  const { status: fix, setReopened } = useIssueFix(error);
  const [severity, setSeverityLocal] = useState(error?.severity ?? "high");
  const [assignee, setAssigneeLocal] = useState(error?.assignees?.[0] ?? null);
  // Use the live active org (matches the X-Organization-Id header) instead of
  // the cached user.organization.id, which can drift and 404 the members
  // request (TH-6156).
  const { currentOrganizationId } = useOrganization();
  const { data: orgMembers = [] } = useOrgMembers(currentOrganizationId);
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
  const { data: sidebar, isLoading: isSidebarLoading } = useErrorFeedSidebar(
    error?.cluster_id,
    selectedTraceId,
  );
  const sidebarPending = isSidebarLoading && !sidebar;
  // Must match OverviewTab's trace source or the two read different cache entries.
  const effectiveTraceId =
    selectedTraceId ?? sidebar?.ai_metadata?.trace_id ?? null;

  if (!error) return null;

  // Prefer backend-computed age_days; fall back to client calc if sidebar
  // hasn't loaded yet so the UI still renders something.
  const fallbackAgeMs = error.first_seen
    ? Date.now() - new Date(error.first_seen).getTime()
    : null;
  const ageDays =
    sidebar?.timeline?.age_days ??
    (fallbackAgeMs ? Math.floor(fallbackAgeMs / (1000 * 60 * 60 * 24)) : null);

  const qualityScores = (sidebar?.evaluations ?? []).map((ev) => ({
    label: ev.label,
    type: EVAL_TYPE_MAP[ev.type] ?? ev.type,
    result: ev.result,
    score: ev.score ?? undefined,
    value: ev.value ?? undefined,
  }));
  const coOccurringIssues = sidebar?.co_occurring_issues ?? [];

  const activityLog = error.first_seen
    ? [
        {
          color: "status.warning.solid",
          text: "First detected",
          meta: `System · ${error.firstSeenHuman ?? humanizeTime(error.first_seen)}${
            ageDays != null ? ` — ${ageDays} days open` : ""
          }`,
        },
      ]
    : [];

  return (
    <Box
      sx={{
        width: 280,
        flexShrink: 0,
        borderLeft: "1px solid",
        borderColor: "divider",
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Stack gap={1.75} sx={{ p: 1.5 }}>
        {/* ── Status / Severity / Assignee ── */}
        <Section title="Triage">
          <Stack gap={0.9}>
            <FieldRow label="Status">
              <StatusDropdown
                clusterId={error.cluster_id}
                current={error.status}
                fix={improving ? fix : null}
                onReopen={setReopened}
              />
            </FieldRow>
            {/* The fix's own status (RSI PRD: a fix-status chip), beside the issue's. */}
            {improving && fix && (
              <FieldRow label="Fix">
                <IssueStatusChip issue={error} />
              </FieldRow>
            )}
            <FieldRow label="Severity">
              <SeverityDropdown current={severity} onChange={setSeverity} />
            </FieldRow>
            <FieldRow label="Assignee">
              <AssigneeDropdown
                current={assignee}
                onChange={setAssignee}
                members={orgMembers}
              />
            </FieldRow>
          </Stack>
        </Section>

        {/* ── Issue at-a-glance ── */}
        <Section title="Issue">
          <Box
            sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0.6 }}
          >
            {[
              {
                label: "Traces",
                value: error.trace_count?.toLocaleString(),
                icon: "mdi:counter",
              },
              {
                label: "Users affected",
                value: error.users_affected?.toLocaleString(),
                icon: "solar:users-group-rounded-linear",
              },
              {
                label: "Sessions",
                value: error.sessions?.toLocaleString(),
                icon: "solar:monitor-linear",
              },
              {
                label: "Issue ID",
                value: error.cluster_id,
                icon: "solar:hashtag-linear",
              },
            ].map((item) => (
              <Box
                key={item.label}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  px: 1,
                  py: 0.6,
                }}
              >
                <Stack direction="row" alignItems="center" gap={0.4} mb={0.2}>
                  <Iconify
                    icon={item.icon}
                    width={12}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography fontSize="12px" color="text.secondary">
                    {item.label}
                  </Typography>
                </Stack>
                <Typography
                  fontSize="12px"
                  fontWeight={600}
                  color="text.primary"
                  noWrap
                >
                  {item.value ?? "—"}
                </Typography>
              </Box>
            ))}
          </Box>
        </Section>

        {/* ── Timeline ── */}
        <Section title="Timeline">
          <Stack gap={0.5}>
            <MetaRow
              label="First seen"
              value={error.firstSeenHuman ?? humanizeTime(error.first_seen)}
              icon="solar:stopwatch-linear"
            />
            <MetaRow
              label="Last seen"
              value={error.lastSeenHuman ?? humanizeTime(error.last_seen)}
              icon="solar:clock-circle-linear"
            />
            {ageDays != null && (
              <MetaRow
                label="Age"
                value={`${ageDays} days`}
                icon="solar:calendar-mark-linear"
              />
            )}
          </Stack>
        </Section>

        {/* ── AI Metadata ── */}
        <Section title="AI Metadata">
          <Stack gap={0.5}>
            <MetaRow
              label="Model"
              value={sidebar?.ai_metadata?.model ?? error.model}
              icon="solar:brain-linear"
              monospace
            />
            <MetaRow
              label="Version"
              value={sidebar?.ai_metadata?.model_version ?? error.model_version}
              icon="solar:tag-linear"
              monospace
            />
            <MetaRow
              label="Agent"
              value={error.agent}
              icon="solar:bot-linear"
              monospace
            />
            <MetaRow label="Pipeline" value={error.pipeline} icon="mdi:pipe" />
            {error.connector && (
              <MetaRow
                label="Connector"
                value={error.connector}
                icon="solar:plug-circle-linear"
              />
            )}
            <MetaRow
              label="Project"
              value={sidebar?.ai_metadata?.project ?? error.project}
              icon="solar:folder-linear"
            />
            {(sidebar?.ai_metadata?.eval_score ?? error.eval_score) != null && (
              <MetaRow
                label="Eval score"
                value={`${(
                  sidebar?.ai_metadata?.eval_score ?? error.eval_score
                ).toFixed(2)} / 1.00`}
                icon="solar:graph-up-linear"
              />
            )}
            <MetaRow
              label="Trace ID"
              value={sidebar?.ai_metadata?.trace_id ?? error.trace_id}
              icon="solar:graph-linear"
              monospace
            />
          </Stack>
        </Section>

        {/* ── Evaluations ── */}
        {sidebarPending ? (
          <Section title="Evaluations">
            <Stack gap={0.75}>
              {Array.from({ length: 3 }).map((_, i) => (
                <Stack key={i} direction="row" alignItems="center" gap={0.75}>
                  <Skeleton
                    width={90}
                    height={12}
                    sx={{ borderRadius: "4px" }}
                  />
                  <Box sx={{ flex: 1 }} />
                  <Skeleton
                    width={36}
                    height={12}
                    sx={{ borderRadius: "4px" }}
                  />
                </Stack>
              ))}
            </Stack>
          </Section>
        ) : (
          qualityScores.length > 0 && (
            <Section title="Evaluations">
              <Stack gap={0.5}>
                {qualityScores.map((qs) => (
                  <EvalRow
                    key={qs.label}
                    label={qs.label}
                    type={qs.type}
                    result={qs.result}
                    score={qs.score}
                    value={qs.value}
                  />
                ))}
              </Stack>
            </Section>
          )
        )}

        {/* ── Co-occurring Issues ── */}
        {sidebarPending ? (
          <Section title="Co-occurring Issues">
            <Stack gap={0.75}>
              {Array.from({ length: 3 }).map((_, i) => (
                <Stack key={i} direction="row" alignItems="center" gap={0.75}>
                  <Skeleton
                    width="65%"
                    height={12}
                    sx={{ borderRadius: "4px" }}
                  />
                  <Box sx={{ flex: 1 }} />
                  <Skeleton
                    width={28}
                    height={12}
                    sx={{ borderRadius: "4px" }}
                  />
                </Stack>
              ))}
            </Stack>
          </Section>
        ) : (
          coOccurringIssues.length > 0 && (
            <Section title="Co-occurring Issues">
              <CoOccurringList issues={coOccurringIssues} />
            </Section>
          )
        )}

        {/* ── Activity ── */}
        {activityLog.length > 0 && (
          <Section title="Activity">
            <Stack gap={0}>
              {activityLog.map((item, i) => (
                <ActivityItem
                  key={i}
                  text={item.text}
                  meta={item.meta}
                  isLast={i === activityLog.length - 1}
                />
              ))}
            </Stack>
          </Section>
        )}

        {/* ── Integrations ── */}
        <Stack gap={1}>
          <Typography
            sx={{
              fontSize: "12px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "none",
              letterSpacing: 0,
            }}
          >
            Integrations
          </Typography>
          <Integrations
            clusterId={error?.cluster_id}
            traceId={effectiveTraceId}
            externalIssueUrl={error?.external_issue_url}
            externalIssueId={error?.external_issue_id}
          />
        </Stack>
      </Stack>
    </Box>
  );
}

ErrorMetadataPanel.propTypes = {
  error: PropTypes.object,
};
