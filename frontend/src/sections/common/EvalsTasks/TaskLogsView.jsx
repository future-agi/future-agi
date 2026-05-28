import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  IconButton,
  LinearProgress,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { format, formatDistanceToNow, differenceInSeconds } from "date-fns";
import { enrichErrorGroups } from "./classifyTaskError";

// ── Stat Card ──

const StatCard = ({ icon, label, value, color, bgColor }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 1.5,
      p: 1.5,
      borderRadius: 1,
      border: "1px solid",
      borderColor: "divider",
      flex: 1,
      minWidth: 120,
    }}
  >
    <Box
      sx={{
        width: 32,
        height: 32,
        borderRadius: "8px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: bgColor || "action.hover",
      }}
    >
      <Iconify
        icon={icon}
        width={18}
        sx={{ color: color || "text.secondary" }}
      />
    </Box>
    <Box>
      <Typography
        variant="h6"
        sx={{ fontSize: "18px", fontWeight: 700, lineHeight: 1.2 }}
      >
        {value}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: "11px" }}
      >
        {label}
      </Typography>
    </Box>
  </Box>
);

StatCard.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  color: PropTypes.string,
  bgColor: PropTypes.string,
};

// ── Duration formatter ──

function formatDuration(startTime, endTime) {
  if (!startTime) return "—";
  const start = new Date(startTime);
  const end = endTime ? new Date(endTime) : new Date();
  const secs = differenceInSeconds(end, start);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  const hrs = Math.floor(secs / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  return `${hrs}h ${mins}m`;
}

// ── Error Group Card ──
// Renders one classified error group: a titled header with icon, occurrence
// count, normalized message, and an expandable body with actionable hints
// and up to 3 raw examples. `group` comes from `groupTaskErrors()`.

const ErrorGroupCard = ({ group, defaultExpanded = false }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [showFullError, setShowFullError] = useState(false);
  const hasMoreError = group.raw && group.raw !== group.normalized;
  // Every entry in this log is a real failure — there's no soft tier.
  // Kept as a const so the styling chain below can still parameterize
  // off it if we ever bring back severity tiers.
  const sev = "error";

  return (
    <Box
      sx={(t) => ({
        border: "1px solid",
        borderColor: alpha(t.palette[sev].main, 0.3),
        bgcolor: alpha(
          t.palette[sev].main,
          t.palette.mode === "dark" ? 0.08 : 0.04,
        ),
        borderRadius: 1,
        overflow: "hidden",
      })}
    >
      {/* Header — always visible, click to expand */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1.25,
          p: 1.5,
          cursor: "pointer",
        }}
        onClick={() => setExpanded((v) => !v)}
      >
        <Box
          sx={(t) => ({
            width: 32,
            height: 32,
            borderRadius: "8px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: alpha(t.palette[sev].main, 0.14),
            flexShrink: 0,
          })}
        >
          <Iconify icon={group.icon} width={18} sx={{ color: `${sev}.main` }} />
        </Box>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              flexWrap: "wrap",
              mb: 0.25,
            }}
          >
            <Typography
              variant="subtitle2"
              fontWeight={600}
              sx={{ fontSize: "13px" }}
            >
              {group.title}
            </Typography>
            <Chip
              label={`${group.count} ${
                group.count === 1 ? "occurrence" : "occurrences"
              }`}
              size="small"
              color={sev}
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
            />
          </Box>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              fontSize: "12px",
              fontFamily: "monospace",
              wordBreak: "break-word",
              whiteSpace: "pre-wrap",
              lineHeight: 1.5,
            }}
          >
            {showFullError && hasMoreError ? group.raw : group.normalized}
            {hasMoreError && (
              <Typography
                component="span"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowFullError((prev) => !prev);
                }}
                sx={{
                  ml: 0.5,
                  fontSize: "11px",
                  color: "primary.main",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  "&:hover": { textDecoration: "underline" },
                }}
              >
                {showFullError ? "Show less" : "Show more"}
              </Typography>
            )}
          </Typography>
        </Box>

        <IconButton size="small" sx={{ p: 0.25, mt: 0.25, flexShrink: 0 }}>
          <Iconify
            icon={
              expanded
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </IconButton>
      </Box>

      {/* Expandable body — actionable hints + raw examples */}
      <Collapse in={expanded} unmountOnExit>
        <Divider sx={{ borderColor: alpha("#000", 0) }} />
        <Box
          sx={{
            px: 1.5,
            pb: 1.5,
            pt: 0.5,
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
          }}
        >
          {/* How to fix */}
          {group.hints?.length > 0 && (
            <Box>
              <Typography
                variant="caption"
                fontWeight={600}
                sx={{
                  display: "block",
                  mb: 0.5,
                  fontSize: "10px",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: "text.disabled",
                }}
              >
                How to fix
              </Typography>
              <Box
                component="ul"
                sx={{
                  m: 0,
                  pl: 2.25,
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                }}
              >
                {group.hints.map((hint, i) => (
                  <Typography
                    // eslint-disable-next-line react/no-array-index-key
                    key={i}
                    component="li"
                    variant="body2"
                    sx={{
                      fontSize: "12px",
                      lineHeight: 1.55,
                      color: "text.secondary",
                    }}
                  >
                    {hint}
                  </Typography>
                ))}
              </Box>
            </Box>
          )}

          {/* Raw examples — show up to 3 verbatim so users can dig in */}
          {group.examples?.length > 0 && (
            <Box>
              <Typography
                variant="caption"
                fontWeight={600}
                sx={{
                  display: "block",
                  mb: 0.5,
                  fontSize: "10px",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: "text.disabled",
                }}
              >
                Raw error
                {group.examples.length < group.count &&
                  ` (${group.examples.length} of ${group.count})`}
              </Typography>
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                {group.examples.map((ex, i) => (
                  <Box
                    // eslint-disable-next-line react/no-array-index-key
                    key={i}
                    sx={(t) => ({
                      p: 1,
                      borderRadius: "4px",
                      bgcolor: alpha(t.palette[sev].main, 0.06),
                      border: "1px solid",
                      borderColor: alpha(t.palette[sev].main, 0.18),
                    })}
                  >
                    <Typography
                      sx={{
                        fontSize: "11px",
                        fontFamily: "monospace",
                        color: "text.secondary",
                        wordBreak: "break-word",
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {ex}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </Box>
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

ErrorGroupCard.propTypes = {
  group: PropTypes.shape({
    category: PropTypes.string.isRequired,
    title: PropTypes.string.isRequired,
    icon: PropTypes.string.isRequired,
    severity: PropTypes.oneOf(["error"]).isRequired,
    hints: PropTypes.arrayOf(PropTypes.string),
    normalized: PropTypes.string.isRequired,
    raw: PropTypes.string,
    count: PropTypes.number.isRequired,
    examples: PropTypes.arrayOf(PropTypes.string),
  }).isRequired,
  defaultExpanded: PropTypes.bool,
};

const WarningGroupCard = ({ group, defaultExpanded = false }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const emptyKeys = group.empty_keys || [];
  const filledKeys = group.filled_keys || [];
  const message =
    group.message ||
    "Eval ran with some inputs empty. Result may be less reliable. Ignore if this is intentional.";

  return (
    <Box
      sx={(t) => ({
        border: "1px solid",
        borderColor: alpha(t.palette.warning.main, 0.35),
        bgcolor: alpha(
          t.palette.warning.main,
          t.palette.mode === "dark" ? 0.1 : 0.05,
        ),
        borderRadius: 1,
        overflow: "hidden",
      })}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1.25,
          p: 1.5,
          cursor: "pointer",
        }}
        onClick={() => setExpanded((v) => !v)}
      >
        <Box
          sx={(t) => ({
            width: 32,
            height: 32,
            borderRadius: "8px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: alpha(t.palette.warning.main, 0.16),
            flexShrink: 0,
          })}
        >
          <Iconify
            icon="solar:danger-triangle-linear"
            width={18}
            sx={{ color: "warning.main" }}
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              flexWrap: "wrap",
              mb: 0.25,
            }}
          >
            <Typography
              variant="subtitle2"
              fontWeight={600}
              sx={{ fontSize: "13px" }}
            >
              Partial inputs
            </Typography>
            <Chip
              label={`${group.count} ${
                group.count === 1 ? "occurrence" : "occurrences"
              }`}
              size="small"
              color="warning"
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
            />
          </Box>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontSize: "12px", lineHeight: 1.5 }}
          >
            {message}
          </Typography>
        </Box>
        <IconButton size="small" sx={{ p: 0.25, mt: 0.25, flexShrink: 0 }}>
          <Iconify
            icon={
              expanded
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </IconButton>
      </Box>
      <Collapse in={expanded} unmountOnExit>
        <Divider sx={{ borderColor: alpha("#000", 0) }} />
        <Box sx={{ px: 1.5, pb: 1.5, pt: 0.5 }}>
          <Typography
            variant="overline"
            sx={{
              display: "block",
              mb: 0.5,
              fontSize: "10px",
              color: "text.disabled",
            }}
          >
            Missing variables
          </Typography>
          <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mb: 1 }}>
            {emptyKeys.length > 0 ? (
              emptyKeys.map((key) => (
                <Chip
                  key={key}
                  label={key}
                  size="small"
                  color="warning"
                  variant="outlined"
                  sx={{ fontSize: "10px", height: 18 }}
                />
              ))
            ) : (
              <Typography variant="caption" color="text.disabled">
                Unknown
              </Typography>
            )}
          </Box>
          {filledKeys.length > 0 && (
            <>
              <Typography
                variant="overline"
                sx={{
                  display: "block",
                  mb: 0.5,
                  fontSize: "10px",
                  color: "text.disabled",
                }}
              >
                Present variables
              </Typography>
              <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
                {filledKeys.map((key) => (
                  <Chip
                    key={key}
                    label={key}
                    size="small"
                    variant="outlined"
                    sx={{ fontSize: "10px", height: 18 }}
                  />
                ))}
              </Box>
            </>
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

WarningGroupCard.propTypes = {
  group: PropTypes.shape({
    type: PropTypes.string,
    empty_keys: PropTypes.arrayOf(PropTypes.string),
    filled_keys: PropTypes.arrayOf(PropTypes.string),
    message: PropTypes.string,
    count: PropTypes.number.isRequired,
  }).isRequired,
  defaultExpanded: PropTypes.bool,
};

// ── Main Component ──

const TaskLogsView = ({ evalTaskId, taskStatus }) => {
  const theme = useTheme();
  const isRunning = taskStatus === "running";

  const { data, isLoading } = useQuery({
    queryKey: ["eval-task-logs", evalTaskId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskLogs(), {
        params: { eval_task_id: evalTaskId },
      }),
    select: (d) => d?.data?.result,
    enabled: !!evalTaskId,
    refetchInterval: isRunning ? 5000 : false, // Auto-refresh every 5s when running
  });

  // Enrich the backend-aggregated error groups with classifier metadata
  // (title, icon, severity, actionable hints). The backend
  // (`get_eval_task_logs`) does the GROUP BY in SQL and returns rows
  // like `{ normalized, count, sample }`, so this is just a per-group
  // regex match to attach UI metadata — not a full walk of every error.
  //
  // Placed above the early returns so hook order stays stable. Memoized
  // on the groups array reference so the poll interval (every 5s while
  // the task is running) doesn't re-enrich unnecessarily.
  const errorGroups = useMemo(
    () => enrichErrorGroups(data?.error_groups || []),
    [data?.error_groups],
  );
  // Some response paths (older DRF camelCase middleware) emit camelCase;
  // current path is snake_case — accept either so the panel doesn't
  // silently render empty if a stale renderer hits a new backend (or
  // vice versa).
  const warningGroups =
    data?.warning_groups || data?.warningGroups || [];
  const errorGroupsTruncated =
    data?.error_groups_truncated ?? data?.errorGroupsTruncated ?? false;
  const warningGroupsTruncated =
    data?.warning_groups_truncated ?? data?.warningGroupsTruncated ?? false;

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          minHeight: 200,
        }}
      >
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (!data) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          minHeight: 200,
        }}
      >
        <Typography variant="body2" color="text.disabled">
          No log data available
        </Typography>
      </Box>
    );
  }

  // Response keys are snake_case — the DRF camelCase middleware was
  // removed, so we alias locally to keep the rest of the component
  // readable.
  const {
    success_count: successCount = 0,
    errors_count: errorsCount = 0,
    warnings_count: warningsCount = data?.warningsCount ?? 0,
    total_count: totalCount = 0,
    start_time: startTime,
    end_time: endTime,
    row_type: rowType = "spans",
  } = data;
  const TOTAL_LABEL_BY_ROW_TYPE = {
    spans: "Total Spans",
    traces: "Total Traces",
    sessions: "Total Sessions",
    voiceCalls: "Total Calls",
  };
  const totalLabel = TOTAL_LABEL_BY_ROW_TYPE[rowType] || "Total Spans";
  const successRate =
    totalCount > 0 ? Math.round((successCount / totalCount) * 100) : 0;
  const errorRate =
    totalCount > 0 ? Math.round((errorsCount / totalCount) * 100) : 0;
  const hasErrors = errorGroups.length > 0;
  const hasWarnings = warningGroups.length > 0;
  const isHighErrorRate = errorRate > 50;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
      {/* Progress Bar */}
      <Box>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mb: 0.75,
          }}
        >
          <Typography variant="caption" color="text.secondary" fontWeight={500}>
            {isRunning
              ? "Running..."
              : totalCount > 0
                ? "Completed"
                : "No data"}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {totalCount > 0 ? `${successCount} / ${totalCount} passed` : "—"}
          </Typography>
        </Box>
        <LinearProgress
          variant={
            isRunning && totalCount === 0 ? "indeterminate" : "determinate"
          }
          value={successRate}
          sx={{
            height: 8,
            borderRadius: 4,
            bgcolor: alpha(theme.palette.divider, 0.3),
            "& .MuiLinearProgress-bar": {
              borderRadius: 4,
              bgcolor: isHighErrorRate
                ? "error.main"
                : successRate === 100
                  ? "success.main"
                  : "primary.main",
            },
          }}
        />
        {isHighErrorRate && (
          <Typography
            variant="caption"
            color="error.main"
            sx={{ mt: 0.5, display: "block" }}
          >
            High error rate detected ({errorRate}%)
          </Typography>
        )}
      </Box>

      {/* Stat Cards */}
      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}>
        <StatCard
          icon="solar:check-circle-linear"
          label="Successful"
          value={successCount ?? 0}
          color="success.main"
          bgColor={alpha(theme.palette.success.main, 0.1)}
        />
        <StatCard
          icon="solar:close-circle-linear"
          label="Errors"
          value={errorsCount ?? 0}
          color="error.main"
          bgColor={alpha(theme.palette.error.main, 0.1)}
        />
        {warningsCount > 0 && (
          <StatCard
            icon="solar:danger-triangle-linear"
            label="Partial Inputs"
            value={warningsCount}
            color="warning.main"
            bgColor={alpha(theme.palette.warning.main, 0.1)}
          />
        )}
        <StatCard
          icon="solar:layers-linear"
          label={totalLabel}
          value={totalCount ?? 0}
          color="info.main"
          bgColor={alpha(theme.palette.info.main, 0.1)}
        />
        <StatCard
          icon="solar:clock-circle-linear"
          label="Duration"
          value={formatDuration(startTime, endTime)}
          color="secondary.main"
          bgColor={alpha(theme.palette.secondary.main, 0.1)}
        />
      </Box>

      {/* Task Run Time */}
      {startTime && (
        <Box
          sx={{
            display: "flex",
            gap: 3,
            p: 1.5,
            borderRadius: 1,
            bgcolor: "action.hover",
          }}
        >
          <Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "11px" }}
            >
              Started
            </Typography>
            <Typography variant="body2" sx={{ fontSize: "12px" }}>
              {format(new Date(startTime), "MMM dd, yyyy h:mm a")}
            </Typography>
          </Box>
          {endTime && (
            <Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "11px" }}
              >
                Completed
              </Typography>
              <Typography variant="body2" sx={{ fontSize: "12px" }}>
                {format(new Date(endTime), "MMM dd, yyyy h:mm a")}
              </Typography>
            </Box>
          )}
          {!endTime && isRunning && (
            <Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "11px" }}
              >
                Status
              </Typography>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <CircularProgress size={12} thickness={5} />
                <Typography variant="body2" sx={{ fontSize: "12px" }}>
                  Running ({formatDistanceToNow(new Date(startTime))} elapsed)
                </Typography>
              </Box>
            </Box>
          )}
        </Box>
      )}

      {hasWarnings && (
        <Box>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              mb: 1.5,
              flexWrap: "wrap",
              gap: 1,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="subtitle2" sx={{ fontSize: "13px" }}>
                Partial Input Warnings
              </Typography>
              <Chip
                label={`${warningGroups.length} ${
                  warningGroups.length === 1 ? "type" : "types"
                }`}
                size="small"
                color="warning"
                variant="outlined"
                sx={{ fontSize: "11px", height: 20 }}
              />
            </Box>
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ fontSize: "11px" }}
            >
              {warningsCount} total warning
              {warningsCount !== 1 ? "s" : ""} — grouped by missing variables
            </Typography>
          </Box>
          {warningGroupsTruncated && (
            <Box
              sx={(t) => ({
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                p: 1,
                mb: 1,
                borderRadius: "6px",
                bgcolor: alpha(t.palette.info.main, 0.08),
                border: "1px solid",
                borderColor: alpha(t.palette.info.main, 0.25),
              })}
            >
              <Iconify
                icon="solar:info-circle-linear"
                width={14}
                sx={{ color: "info.main", flexShrink: 0 }}
              />
              <Typography
                variant="caption"
                sx={{ fontSize: "11px", color: "text.secondary" }}
              >
                Showing the most common warning types. Rarer warning groups are
                hidden.
              </Typography>
            </Box>
          )}
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {warningGroups.map((group, index) => (
              <WarningGroupCard
                key={`${group.type}-${(group.empty_keys || []).join(",")}`}
                group={group}
                defaultExpanded={index === 0}
              />
            ))}
          </Box>
        </Box>
      )}

      {/* Error Log — grouped by category with actionable fixes */}
      {hasErrors && (
        <Box>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              mb: 1.5,
              flexWrap: "wrap",
              gap: 1,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="subtitle2" sx={{ fontSize: "13px" }}>
                Error Log
              </Typography>
              <Chip
                label={`${errorGroups.length} ${
                  errorGroups.length === 1 ? "type" : "types"
                }`}
                size="small"
                color="error"
                variant="outlined"
                sx={{ fontSize: "11px", height: 20 }}
              />
            </Box>
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ fontSize: "11px" }}
            >
              {errorsCount} total error
              {errorsCount !== 1 ? "s" : ""} — grouped by type
            </Typography>
          </Box>

          {/* Truncation banner — SQL aggregation caps at _ERROR_GROUPS_LIMIT
              (currently 50) to keep the payload bounded. */}
          {errorGroupsTruncated && (
            <Box
              sx={(t) => ({
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                p: 1,
                mb: 1,
                borderRadius: "6px",
                bgcolor: alpha(t.palette.info.main, 0.08),
                border: "1px solid",
                borderColor: alpha(t.palette.info.main, 0.25),
              })}
            >
              <Iconify
                icon="solar:info-circle-linear"
                width={14}
                sx={{ color: "info.main", flexShrink: 0 }}
              />
              <Typography
                variant="caption"
                sx={{ fontSize: "11px", color: "text.secondary" }}
              >
                Showing the top {errorGroups.length} error types by frequency.
                Rarer error types are hidden — fix the biggest groups first and
                re-check.
              </Typography>
            </Box>
          )}

          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {errorGroups.map((group, index) => (
              <ErrorGroupCard
                // eslint-disable-next-line react/no-array-index-key
                key={`${group.category}-${index}`}
                group={group}
                // Auto-expand the first (largest) group so users see
                // fixes without having to click.
                defaultExpanded={index === 0}
              />
            ))}
          </Box>
        </Box>
      )}

      {/* Empty state for no errors */}
      {!hasErrors && !hasWarnings && totalCount > 0 && (
        <Box
          sx={{
            p: 3,
            textAlign: "center",
            borderRadius: 1,
            border: "1px dashed",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="solar:check-circle-bold"
            width={32}
            sx={{ color: "success.main", mb: 1 }}
          />
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontSize: "13px" }}
          >
            All evaluations completed successfully
          </Typography>
        </Box>
      )}
    </Box>
  );
};

TaskLogsView.propTypes = {
  evalTaskId: PropTypes.string,
  taskStatus: PropTypes.string,
};

export default TaskLogsView;
