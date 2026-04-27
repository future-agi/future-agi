import React, { useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
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
  useErrorFeedDeepAnalysis,
  useErrorFeedSidebar,
  useLinearTeams,
  useRunDeepAnalysis,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/error-feed";
import { useOrgMembers } from "src/api/annotation-queues/annotation-queues";
import { useAuthContext } from "src/auth/hooks";
import { useErrorFeedStore } from "../store";
import PropTypes from "prop-types";

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
        sx={{ fontSize: "11px", color: "text.secondary", flexShrink: 0 }}
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
            width={11}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            fontSize: "11px",
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
            fontSize: "11px",
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
              fontSize: "11px",
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
          fontSize: "10px",
          fontWeight: 600,
          color: "text.secondary",
          textTransform: "uppercase",
          letterSpacing: "0.07em",
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
    icon: "mdi:trending-up",
    color: "#DB2F2D",
  },
  {
    value: "acknowledged",
    label: "Acknowledged",
    icon: "mdi:check-circle-outline",
    color: "#938FA3",
  },
  {
    value: "for_review",
    label: "For review",
    icon: "mdi:eye-outline",
    color: "#F5A623",
  },
  {
    value: "resolved",
    label: "Resolved",
    icon: "mdi:check-circle",
    color: "#5ACE6D",
  },
];

function StatusDropdown({ clusterId, current }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const updateIssue = useUpdateErrorFeedIssue();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const cur =
    STATUS_OPTIONS.find((s) => s.value === current) || STATUS_OPTIONS[0];

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
          borderRadius: "5px",
          cursor: "pointer",
          border: "1px solid",
          borderColor: isDark ? alpha(cur.color, 0.3) : alpha(cur.color, 0.25),
          bgcolor: isDark ? alpha(cur.color, 0.1) : alpha(cur.color, 0.07),
          "&:hover": { borderColor: alpha(cur.color, 0.5) },
          transition: "all 0.15s",
        }}
      >
        <Iconify icon={cur.icon} width={11} sx={{ color: cur.color }} />
        <Typography fontSize="11px" fontWeight={600} sx={{ color: cur.color }}>
          {cur.label}
        </Typography>
        <Iconify
          icon="mdi:chevron-down"
          width={11}
          sx={{ color: cur.color, opacity: 0.7 }}
        />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          elevation: 3,
          sx: {
            borderRadius: 1,
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
              fontSize: "10px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Change status
          </Typography>
        </Box>
        <Divider sx={{ borderColor: "divider" }} />
        {STATUS_OPTIONS.filter((s) => s.value !== current).map((s) => (
          <MenuItem
            key={s.value}
            onClick={() => {
              updateIssue.mutate({ clusterId, status: s.value });
              setAnchorEl(null);
            }}
            sx={{ gap: 1, fontSize: "13px", py: 0.75 }}
          >
            <Iconify icon={s.icon} width={15} sx={{ color: s.color }} />
            <Typography fontSize="12px" sx={{ color: s.color }}>
              {s.label}
            </Typography>
          </MenuItem>
        ))}
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
    color: "#DB2F2D",
    darkColor: "#E87876",
    icon: "mdi:alert-octagon-outline",
  },
  {
    value: "high",
    label: "High",
    color: "#E9690C",
    darkColor: "#F49A54",
    icon: "mdi:alert-circle-outline",
  },
  {
    value: "medium",
    label: "Medium",
    color: "#8C7A00",
    darkColor: "#F5E65F",
    icon: "mdi:alert-outline",
  },
  {
    value: "low",
    label: "Low",
    color: "#605C70",
    darkColor: "#71717a",
    icon: "mdi:information-outline",
  },
];

function SeverityDropdown({ current, onChange }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const base =
    SEVERITY_OPTIONS.find((s) => s.value === current) || SEVERITY_OPTIONS[1];
  const cur = { ...base, color: isDark ? base.darkColor : base.color };

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
          borderRadius: "5px",
          cursor: "pointer",
          border: "1px solid",
          borderColor: isDark ? alpha(cur.color, 0.3) : alpha(cur.color, 0.25),
          bgcolor: isDark ? alpha(cur.color, 0.1) : alpha(cur.color, 0.07),
          "&:hover": { borderColor: alpha(cur.color, 0.5) },
          transition: "all 0.15s",
        }}
      >
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: cur.color,
            flexShrink: 0,
          }}
        />
        <Typography fontSize="11px" fontWeight={600} sx={{ color: cur.color }}>
          {cur.label}
        </Typography>
        <Iconify
          icon="mdi:chevron-down"
          width={11}
          sx={{ color: cur.color, opacity: 0.7 }}
        />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          elevation: 3,
          sx: {
            borderRadius: 1,
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
              fontSize: "10px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Change severity
          </Typography>
        </Box>
        <Divider sx={{ borderColor: "divider" }} />
        {SEVERITY_OPTIONS.filter((s) => s.value !== current).map((s) => {
          const c = isDark ? s.darkColor : s.color;
          return (
            <MenuItem
              key={s.value}
              onClick={() => {
                onChange?.(s.value);
                setAnchorEl(null);
              }}
              sx={{ gap: 1, fontSize: "13px", py: 0.75 }}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: c,
                  flexShrink: 0,
                }}
              />
              <Typography fontSize="12px" sx={{ color: c }}>
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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const member = members.find((m) => m.email === current);

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
          gap: 0.6,
          px: 0.75,
          py: 0.3,
          borderRadius: "5px",
          cursor: "pointer",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.03),
          "&:hover": {
            borderColor: alpha("#7857FC", 0.4),
            bgcolor: isDark ? alpha("#7857FC", 0.07) : alpha("#7857FC", 0.04),
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
                bgcolor: alpha("#7857FC", 0.2),
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <Typography
                fontSize="8px"
                fontWeight={700}
                sx={{ color: "#7857FC" }}
              >
                {getInitials(member.name, member.email)}
              </Typography>
            </Box>
            <Typography
              fontSize="11px"
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
              icon="mdi:account-plus-outline"
              width={12}
              sx={{ color: "text.disabled" }}
            />
            <Typography fontSize="11px" color="text.disabled">
              Assign
            </Typography>
          </>
        )}
        <Iconify
          icon="mdi:chevron-down"
          width={11}
          sx={{ color: "text.disabled", opacity: 0.7 }}
        />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          elevation: 3,
          sx: {
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            minWidth: 180,
            mt: 0.5,
          },
        }}
      >
        <Box sx={{ px: 1.5, py: 0.75 }}>
          <Typography
            sx={{
              fontSize: "10px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
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
              icon="mdi:account-remove-outline"
              width={15}
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
                  bgcolor: alpha("#7857FC", 0.18),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <Typography
                  fontSize="9px"
                  fontWeight={700}
                  sx={{ color: "#7857FC" }}
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
  const isDark = theme.palette.mode === "dark";

  const passed = result === "passed";
  const resultColor = passed ? "#5ACE6D" : "#DB2F2D";
  const resultIcon = passed
    ? "mdi:check-circle-outline"
    : "mdi:close-circle-outline";

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
        bgcolor: isDark ? alpha("#fff", 0.018) : alpha("#000", 0.015),
      }}
    >
      {/* Header: name only */}
      <Typography
        fontSize="11px"
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
              bgcolor: isDark ? alpha("#fff", 0.08) : alpha("#000", 0.07),
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
            fontSize="10px"
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
          <Iconify icon={resultIcon} width={11} sx={{ color: resultColor }} />
          <Typography
            fontSize="10px"
            fontWeight={600}
            sx={{ color: resultColor }}
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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
            borderColor: isDark ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.35)",
            bgcolor: isDark ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.06)",
            mt: "3px",
          }}
        />
        {!isLast && (
          <Box
            sx={{
              width: 1.5,
              flex: 1,
              minHeight: 14,
              bgcolor: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.1)",
              mt: "2px",
            }}
          />
        )}
      </Stack>
      <Stack gap={0} pb={isLast ? 0 : 1}>
        <Typography
          fontSize="11px"
          color="text.primary"
          sx={{ lineHeight: 1.5 }}
        >
          {text}
        </Typography>
        <Typography fontSize="10px" color="text.secondary">
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
const SEV_COLOR = {
  critical: "#DB2F2D",
  high: "#E9690C",
  medium: "#F5A623",
  low: "#71717a",
};

function CoOccurringList({ issues }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const navigate = useNavigate();
  return (
    <Stack gap={0.5}>
      {issues.map((issue) => {
        const c = SEV_COLOR[issue.severity] || "#71717a";
        return (
          <Stack
            key={issue.id}
            direction="row"
            alignItems="center"
            gap={0.75}
            onClick={() => navigate(paths.dashboard.errorFeed.detail(issue.id))}
            sx={{
              px: 1,
              py: 0.7,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor: isDark ? alpha("#fff", 0.02) : "transparent",
              cursor: "pointer",
              "&:hover": {
                borderColor: alpha("#7857FC", 0.3),
                bgcolor: isDark
                  ? alpha("#7857FC", 0.05)
                  : alpha("#7857FC", 0.02),
              },
              transition: "all 0.15s",
            }}
          >
            <Stack gap={0.1} flex={1} minWidth={0}>
              <Typography fontSize="11px" color="text.primary" noWrap>
                {issue.title}
              </Typography>
              <Typography fontSize="10px" color="text.secondary">
                {issue.count?.toLocaleString()} shared
                {issue.coOccurrence != null &&
                  ` · ${Math.round(issue.coOccurrence * 100)}%`}
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

function LinearTeamPicker({ open, onClose, clusterId }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { enqueueSnackbar } = useSnackbar();
  const { data: linearData } = useLinearTeams({ enabled: open });
  const createIssue = useCreateLinearIssue();
  const teams = linearData?.teams ?? [];

  const handleCreate = (teamId) => {
    createIssue.mutate(
      { clusterId, teamId },
      {
        onSuccess: (res) => {
          const result = res?.data?.result;
          if (result?.alreadyLinked) {
            window.open(result.issueUrl, "_blank");
          } else {
            enqueueSnackbar(`Created ${result?.issueId}`, {
              variant: "success",
            });
            if (result?.issueUrl) window.open(result.issueUrl, "_blank");
          }
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to create Linear issue", {
            variant: "error",
          });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: "12px",
          bgcolor: isDark ? "#111111" : "background.paper",
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
            <Typography fontSize="14px" fontWeight={700} color="text.primary">
              Create Linear Issue
            </Typography>
            <Typography fontSize="11px" color="text.disabled">
              Select a team to create the issue in.
            </Typography>
          </Stack>
          <Box
            onClick={onClose}
            sx={{
              cursor: "pointer",
              color: "text.disabled",
              "&:hover": { color: "text.primary" },
            }}
          >
            <Iconify icon="mdi:close" width={18} />
          </Box>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ px: 2.5, pb: 2.5, pt: 0 }}>
        {teams.length === 0 ? (
          <Typography fontSize="12px" color="text.disabled" sx={{ py: 2 }}>
            {linearData ? "No teams found." : "Loading teams..."}
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
                    borderColor: "#5E6AD2",
                    bgcolor: isDark
                      ? alpha("#5E6AD2", 0.06)
                      : alpha("#5E6AD2", 0.03),
                  },
                  transition: "all 0.15s",
                }}
              >
                <Box
                  sx={{
                    width: 28,
                    height: 28,
                    borderRadius: "6px",
                    bgcolor: isDark
                      ? alpha("#5E6AD2", 0.12)
                      : alpha("#5E6AD2", 0.08),
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <Typography fontSize="12px" fontWeight={700} color="#5E6AD2">
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
};

function ConnectorRow({ icon, color, name, subtitle, action, onAction }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

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
        bgcolor: isDark ? alpha("#fff", 0.02) : "transparent",
      }}
    >
      <Box
        sx={{
          width: 22,
          height: 22,
          borderRadius: "5px",
          bgcolor: isDark ? alpha(color, 0.12) : alpha(color, 0.08),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={13} sx={{ color }} />
      </Box>

      <Stack gap={0} flex={1} minWidth={0}>
        <Typography fontSize="11px" fontWeight={600} color="text.primary">
          {name}
        </Typography>
        <Typography
          fontSize="10px"
          color={subtitle === "Connected" ? undefined : "text.secondary"}
          sx={subtitle === "Connected" ? { color: "#5ACE6D" } : undefined}
        >
          {subtitle}
        </Typography>
      </Stack>

      {action && (
        <Box
          onClick={onAction}
          sx={{
            px: 0.75,
            py: 0.25,
            borderRadius: "4px",
            cursor: "pointer",
            border: "1px solid",
            borderColor: alpha(color, 0.3),
            bgcolor: isDark ? alpha(color, 0.1) : alpha(color, 0.07),
            "&:hover": {
              bgcolor: isDark ? alpha(color, 0.18) : alpha(color, 0.12),
            },
            transition: "all 0.15s",
          }}
        >
          <Typography
            fontSize="10px"
            fontWeight={600}
            sx={{ color, whiteSpace: "nowrap" }}
          >
            {action}
          </Typography>
        </Box>
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

function Integrations({ clusterId, externalIssueUrl, externalIssueId }) {
  const navigate = useNavigate();
  const { data: linearData } = useLinearTeams();
  const linearConnected = linearData?.connected === true;
  const [teamPickerOpen, setTeamPickerOpen] = useState(false);

  const linked = !!externalIssueUrl;

  const handleLinearAction = () => {
    if (!linearConnected) {
      navigate(paths.dashboard.settings.integrations);
      return;
    }
    if (linked) {
      window.open(externalIssueUrl, "_blank");
      return;
    }
    setTeamPickerOpen(true);
  };

  const linearSubtitle = linked
    ? externalIssueId
    : linearConnected
      ? "Connected"
      : "Not connected";
  const linearAction = linearConnected
    ? linked
      ? `View ${externalIssueId ?? "issue"}`
      : "Create issue"
    : "Connect";

  return (
    <>
      <Stack gap={0.75}>
        <ConnectorRow
          icon="simple-icons:linear"
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
        />
      )}
    </>
  );
}
Integrations.propTypes = {
  clusterId: PropTypes.string,
  externalIssueUrl: PropTypes.string,
  externalIssueId: PropTypes.string,
};

// ── Deep Analysis Button ──────────────────────────────────────────────────────
//
// State machine driven entirely by backend:
//   - GET /root-cause/ returns status: idle | running | done | failed
//   - POST /deep-analysis/ dispatches a run (or no-ops if cached and not forced)
//   - Running state survives navigation because it's derived from
//     Trace.error_analysis_status on the server, not client memory.
function DeepAnalysisButton({ clusterId, traceId }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { enqueueSnackbar } = useSnackbar();

  const { data: deepAnalysis, isLoading } = useErrorFeedDeepAnalysis(
    clusterId,
    traceId,
  );
  const runMutation = useRunDeepAnalysis();

  const status = deepAnalysis?.status ?? "idle";
  const isDispatching = runMutation.isPending;
  const isRunning = status === "running" || isDispatching;

  const dispatch = (force) => {
    if (!traceId) return;
    runMutation.mutate(
      { clusterId, traceId, force },
      {
        onSuccess: (res) => {
          const newStatus = res?.data?.result?.status;
          if (newStatus === "running") {
            enqueueSnackbar(
              "Deep analysis started — takes about a minute. You can keep browsing; it'll show up here when ready.",
              { variant: "info", autoHideDuration: 6000 },
            );
          } else if (newStatus === "done") {
            // Cached result; frontend will scroll on its own via the
            // effect in OverviewTab that watches the done state.
            enqueueSnackbar("Showing existing analysis results.", {
              variant: "success",
              autoHideDuration: 3000,
            });
          }
        },
        onError: () => {
          enqueueSnackbar("Failed to start deep analysis. Please try again.", {
            variant: "error",
          });
        },
      },
    );
  };

  // No trace selected yet (e.g. cluster has zero traces) — show disabled button
  if (!traceId) {
    return (
      <Button
        variant="contained"
        fullWidth
        disabled
        startIcon={<Iconify icon="mdi:magnify-expand" width={14} />}
        sx={{
          height: 34,
          fontSize: "12px",
          fontWeight: 600,
          borderRadius: "7px",
          textTransform: "none",
        }}
      >
        Run Deep Analysis
      </Button>
    );
  }

  // Initial query still loading — neutral placeholder
  if (isLoading && !deepAnalysis) {
    return (
      <Box
        sx={{
          height: 34,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "7px",
        }}
      >
        <CircularProgress size={12} thickness={5} />
      </Box>
    );
  }

  if (isRunning) {
    return (
      <Box
        sx={{
          height: 34,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 1,
          border: "1px solid",
          borderColor: "primary.main",
          borderRadius: "7px",
          bgcolor: (t) => alpha(t.palette.primary.main, 0.06),
        }}
      >
        <CircularProgress size={12} thickness={5} />
        <Typography fontSize="12px" fontWeight={600} color="primary.main">
          Running analysis…
        </Typography>
      </Box>
    );
  }

  if (status === "done") {
    return (
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Iconify
            icon="mdi:check-circle"
            width={13}
            sx={{ color: "#5ACE6D" }}
          />
          <Typography fontSize="11px" fontWeight={600} color="text.secondary">
            Analysis complete
          </Typography>
        </Stack>
        <Button
          size="small"
          variant="contained"
          startIcon={<Iconify icon="mdi:refresh" width={10} />}
          onClick={() => dispatch(true)}
          disabled={isDispatching}
          sx={{
            height: 22,
            fontSize: "10px",
            fontWeight: 600,
            px: 1,
            borderRadius: "6px",
            textTransform: "none",
            bgcolor: isDark ? "#fff" : "#111",
            color: isDark ? "#111" : "#fff",
            boxShadow: "none",
            "&:hover": {
              bgcolor: isDark ? "#e8e8e8" : "#333",
              boxShadow: "none",
            },
          }}
        >
          Re-run
        </Button>
      </Stack>
    );
  }

  // idle or failed
  const label =
    status === "failed" ? "Retry Deep Analysis" : "Run Deep Analysis";
  return (
    <Button
      variant="contained"
      fullWidth
      startIcon={<Iconify icon="mdi:magnify-expand" width={14} />}
      onClick={() => dispatch(false)}
      disabled={isDispatching}
      sx={{
        height: 34,
        fontSize: "12px",
        fontWeight: 600,
        borderRadius: "7px",
        textTransform: "none",
        bgcolor: isDark ? "#fff" : "#111",
        color: isDark ? "#111" : "#fff",
        boxShadow: "none",
        "&:hover": {
          bgcolor: isDark ? "#e8e8e8" : "#333",
          boxShadow: "none",
        },
      }}
    >
      {label}
    </Button>
  );
}

DeepAnalysisButton.propTypes = {
  clusterId: PropTypes.string,
  traceId: PropTypes.string,
};

// Backend returns `"llm_judge" | "deterministic"` — EvalRow expects
// `"llm" | "pass" | "deterministic"`. Translate on the way in.
const EVAL_TYPE_MAP = {
  llm_judge: "llm",
  deterministic: "deterministic",
};

export default function ErrorMetadataPanel({ error }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [severity, setSeverityLocal] = useState(error?.severity ?? "high");
  const [assignee, setAssigneeLocal] = useState(error?.assignees?.[0] ?? null);
  const { user } = useAuthContext();
  const { data: orgMembers = [] } = useOrgMembers(user?.organization?.id);
  const updateIssue = useUpdateErrorFeedIssue();

  const setSeverity = (val) => {
    setSeverityLocal(val);
    updateIssue.mutate({ clusterId: error?.clusterId, severity: val });
  };
  const setAssignee = (val) => {
    setAssigneeLocal(val);
    updateIssue.mutate({ clusterId: error?.clusterId, assignee: val });
  };

  // Selected trace drives the sidebar's trace-level sections (AI Metadata,
  // Evaluations, Deep Analysis). When nothing's selected, backend falls
  // back to the cluster's latest trace.
  const selectedTraceId = useErrorFeedStore(
    (s) => s.selectedTraceIdByCluster[error?.clusterId] ?? null,
  );
  const { data: sidebar, isLoading: isSidebarLoading } = useErrorFeedSidebar(
    error?.clusterId,
    selectedTraceId,
  );
  const sidebarPending = isSidebarLoading && !sidebar;
  // Effective trace id (what the backend actually used) — hand to the
  // deep-analysis button so Re-run hits the same trace.
  const effectiveTraceId = sidebar?.aiMetadata?.traceId ?? null;

  if (!error) return null;

  // Prefer backend-computed ageDays; fall back to client calc if sidebar
  // hasn't loaded yet so the UI still renders something.
  const fallbackAgeMs = error.firstSeen
    ? Date.now() - new Date(error.firstSeen).getTime()
    : null;
  const ageDays =
    sidebar?.timeline?.ageDays ??
    (fallbackAgeMs ? Math.floor(fallbackAgeMs / (1000 * 60 * 60 * 24)) : null);

  const qualityScores = (sidebar?.evaluations ?? []).map((ev) => ({
    label: ev.label,
    type: EVAL_TYPE_MAP[ev.type] ?? ev.type,
    result: ev.result,
    score: ev.score ?? undefined,
    value: ev.value ?? undefined,
  }));
  const coOccurringIssues = sidebar?.coOccurringIssues ?? [];

  // Minimal activity trail built from what we actually know: first detection.
  // Full audit log (status changes, assignment history) is out of scope —
  // see feed-api-plan.md.
  const activityLog = error.firstSeen
    ? [
        {
          color: "#F5A623",
          text: "First detected",
          meta: `System · ${error.firstSeenHuman ?? humanizeTime(error.firstSeen)}${
            ageDays != null ? ` — ${ageDays} days open` : ""
          }`,
        },
      ]
    : [];

  return (
    <Box
      sx={{
        width: 260,
        flexShrink: 0,
        borderLeft: "1px solid",
        borderColor: "divider",
        bgcolor: isDark ? "background.neutral" : "background.default",
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
                clusterId={error.clusterId}
                current={error.status}
              />
            </FieldRow>
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

        {/* ── Cluster at-a-glance ── */}
        <Section title="Cluster">
          <Box
            sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0.6 }}
          >
            {[
              {
                label: "Traces",
                value: error.traceCount?.toLocaleString(),
                icon: "mdi:counter",
              },
              {
                label: "Users affected",
                value: error.usersAffected?.toLocaleString(),
                icon: "mdi:account-group-outline",
              },
              {
                label: "Sessions",
                value: error.sessions?.toLocaleString(),
                icon: "mdi:monitor-eye",
              },
              {
                label: "Cluster ID",
                value: error.clusterId,
                icon: "mdi:identifier",
              },
            ].map((item) => (
              <Box
                key={item.label}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "5px",
                  px: 1,
                  py: 0.6,
                }}
              >
                <Stack direction="row" alignItems="center" gap={0.4} mb={0.2}>
                  <Iconify
                    icon={item.icon}
                    width={10}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography fontSize="9px" color="text.secondary">
                    {item.label}
                  </Typography>
                </Stack>
                <Typography
                  fontSize="11px"
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

        {/* ── Deep Analysis ── */}
        <Section title="Deep Analysis">
          <DeepAnalysisButton
            clusterId={error.clusterId}
            traceId={effectiveTraceId}
          />
        </Section>

        {/* ── Timeline ── */}
        <Section title="Timeline">
          <Stack gap={0.5}>
            <MetaRow
              label="First seen"
              value={error.firstSeenHuman ?? humanizeTime(error.firstSeen)}
              icon="mdi:clock-start"
            />
            <MetaRow
              label="Last seen"
              value={error.lastSeenHuman ?? humanizeTime(error.lastSeen)}
              icon="mdi:clock-outline"
            />
            {ageDays != null && (
              <MetaRow
                label="Age"
                value={`${ageDays} days`}
                icon="mdi:calendar-clock"
              />
            )}
          </Stack>
        </Section>

        {/* ── AI Metadata ── */}
        <Section title="AI Metadata">
          <Stack gap={0.5}>
            <MetaRow
              label="Model"
              value={error.model}
              icon="mdi:brain"
              monospace
            />
            <MetaRow
              label="Version"
              value={error.modelVersion}
              icon="mdi:tag-outline"
              monospace
            />
            <MetaRow
              label="Agent"
              value={error.agent}
              icon="mdi:robot-outline"
              monospace
            />
            <MetaRow label="Pipeline" value={error.pipeline} icon="mdi:pipe" />
            {error.connector && (
              <MetaRow
                label="Connector"
                value={error.connector}
                icon="mdi:connection"
              />
            )}
            <MetaRow
              label="Project"
              value={error.project}
              icon="mdi:folder-outline"
            />
            {error.evalScore != null && (
              <MetaRow
                label="Eval score"
                value={`${error.evalScore.toFixed(2)} / 1.00`}
                icon="mdi:chart-line"
              />
            )}
            <MetaRow
              label="Trace ID"
              value={error.traceId}
              icon="mdi:sitemap-outline"
              monospace
            />
          </Stack>
        </Section>

        {/* ── Evaluations ── */}
        {sidebarPending ? (
          <Section title="Evaluations">
            <Stack gap={0.75}>
              {Array.from({ length: 3 }).map((_, i) => (
                <Stack
                  key={i}
                  direction="row"
                  alignItems="center"
                  gap={0.75}
                >
                  <Skeleton width={90} height={12} sx={{ borderRadius: "3px" }} />
                  <Box sx={{ flex: 1 }} />
                  <Skeleton width={36} height={12} sx={{ borderRadius: "3px" }} />
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
                <Stack
                  key={i}
                  direction="row"
                  alignItems="center"
                  gap={0.75}
                >
                  <Skeleton width="65%" height={12} sx={{ borderRadius: "3px" }} />
                  <Box sx={{ flex: 1 }} />
                  <Skeleton width={28} height={12} sx={{ borderRadius: "3px" }} />
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
              fontSize: "10px",
              fontWeight: 600,
              color: "text.disabled",
              textTransform: "uppercase",
              letterSpacing: "0.07em",
            }}
          >
            Integrations
          </Typography>
          <Integrations
            clusterId={error?.clusterId}
            externalIssueUrl={error?.externalIssueUrl}
            externalIssueId={error?.externalIssueId}
          />
        </Stack>
      </Stack>
    </Box>
  );
}

ErrorMetadataPanel.propTypes = {
  error: PropTypes.object,
};
