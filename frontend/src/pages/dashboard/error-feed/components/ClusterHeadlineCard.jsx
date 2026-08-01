import React, { useMemo } from "react";
import {
  Box,
  Button,
  Collapse,
  Stack,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { formatDistanceToNowStrict } from "date-fns";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { purple } from "src/theme/palette";
import openExternal from "../openExternal";
import { useErrorFeedStore } from "../store";
import { CONFIDENCE, MESSAGE_TYPE, RUN_STATE, STEP_STATUS } from "../constants";

// Short relative label for the cached-analysis timestamp (date-fns, same
// pattern as ErrorFeedTable / ErrorMetadataPanel).
function formatAnalyzedAt(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  try {
    return `${formatDistanceToNowStrict(then)} ago`;
  } catch {
    return null;
  }
}

// snake_case is the canonical wire form; the axios bridge also exposes camel aliases.
function cachedRcaFrom(error) {
  const rca = error?.rca;
  if (!rca || !rca.synthesis) return null;
  return {
    synthesis: rca.synthesis,
    fix: rca.fix,
    confidence: rca.confidence ?? CONFIDENCE.MEDIUM,
    analyzedAt: formatAnalyzedAt(rca.analyzed_at),
    failuresAtRun: rca.failures_at_run ?? null,
  };
}

const CONFIDENCE_LABEL = {
  [CONFIDENCE.HIGH]: "High confidence",
  [CONFIDENCE.MEDIUM]: "Medium confidence",
  [CONFIDENCE.LOW]: "Low confidence",
};

const ACCENT = purple[500];

// ── State: not_analyzed (empty) ──────────────────────────────────────────────
function NotAnalyzedState({ onAnalyze }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack direction="row" alignItems="center" gap={2} sx={{ py: 0.5 }}>
      <Box
        sx={{
          width: 38,
          height: 38,
          borderRadius: "50%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: alpha(ACCENT, isDark ? 0.16 : 0.1),
          flexShrink: 0,
        }}
      >
        <Iconify
          icon="mdi:star-four-points-outline"
          width={18}
          sx={{ color: ACCENT }}
        />
      </Box>
      <Stack flex={1} gap={0.4} minWidth={0}>
        <Typography fontSize="14px" fontWeight={600} color="text.primary">
          No analysis yet
        </Typography>
        <Typography
          fontSize="12px"
          color="text.secondary"
          sx={{ lineHeight: 1.55 }}
        >
          No analysis yet — get a plain-English explanation and a suggested fix.
        </Typography>
      </Stack>
      <Button
        size="small"
        variant="contained"
        startIcon={<Iconify icon="mdi:star-four-points" width={13} />}
        onClick={onAnalyze}
        sx={{
          height: 32,
          fontSize: "12.5px",
          fontWeight: 600,
          borderRadius: "8px",
          textTransform: "none",
          // White button in dark theme, purple in light.
          bgcolor: isDark ? "#fff" : ACCENT,
          color: isDark ? "#111" : "#fff",
          px: 1.75,
          "&:hover": { bgcolor: isDark ? "#e8e8e8" : "#6845E8" },
          boxShadow: "none",
          flexShrink: 0,
        }}
      >
        Debug this cluster
      </Button>
    </Stack>
  );
}
NotAnalyzedState.propTypes = { onAnalyze: PropTypes.func };

const ANALYSIS_STEP_LABELS = [
  "Sampling traces",
  "Comparing to passing baseline",
  "Synthesising",
];

function StepChips({ activeStepIdx }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
      {ANALYSIS_STEP_LABELS.map((label, i) => {
        const status =
          i < activeStepIdx
            ? "done"
            : i === activeStepIdx
              ? "active"
              : "queued";
        const isActive = status === "active";
        const isDone = status === "done";
        return (
          <Stack
            key={label}
            direction="row"
            alignItems="center"
            gap={0.45}
            sx={{
              px: 0.85,
              py: 0.3,
              borderRadius: "5px",
              border: "1px solid",
              borderColor: isActive ? alpha(ACCENT, 0.3) : "divider",
              bgcolor: isActive
                ? alpha(ACCENT, isDark ? 0.1 : 0.05)
                : isDark
                  ? alpha("#fff", 0.02)
                  : alpha("#000", 0.02),
              opacity: status === "queued" ? 0.55 : 1,
            }}
          >
            <Iconify
              icon={
                isDone
                  ? "mdi:check"
                  : isActive
                    ? "mdi:dots-horizontal"
                    : "mdi:circle-outline"
              }
              width={11}
              sx={{
                color: isDone ? "#5ACE6D" : isActive ? ACCENT : "text.disabled",
              }}
            />
            <Typography
              fontSize="10.5px"
              fontWeight={500}
              color={isActive ? "text.primary" : "text.secondary"}
            >
              {label}
            </Typography>
          </Stack>
        );
      })}
    </Stack>
  );
}
StepChips.propTypes = { activeStepIdx: PropTypes.number };

// ── State: analyzing ─────────────────────────────────────────────────────────
function AnalyzingState({ activeStepIdx }) {
  return (
    <Stack gap={1.25} sx={{ py: 0.25 }}>
      <Stack direction="row" alignItems="center" gap={0.85}>
        <Box
          sx={{
            width: 14,
            height: 14,
            borderRadius: "50%",
            border: "2px solid",
            borderColor: alpha(ACCENT, 0.25),
            borderTopColor: ACCENT,
            animation: "spin 0.8s linear infinite",
            "@keyframes spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
        <Typography fontSize="13.5px" fontWeight={600} color="text.primary">
          Debugging this cluster…
        </Typography>
      </Stack>
      <StepChips activeStepIdx={activeStepIdx} />
    </Stack>
  );
}
AnalyzingState.propTypes = { activeStepIdx: PropTypes.number };

// ── State: analyzed (default after run) ──────────────────────────────────────
function AnalyzedState({ data, linkedIssue, onCreateLinear, onOpenAnalyze }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack gap={1.25}>
      <Typography
        fontSize="14px"
        fontWeight={500}
        color="text.primary"
        sx={{ lineHeight: 1.6 }}
      >
        {data.synthesis}
      </Typography>

      <Stack direction="row" gap={1} alignItems="flex-start">
        <Typography
          fontSize="10px"
          fontWeight={700}
          sx={{
            color: "#5ACE6D",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            mt: "3px",
            flexShrink: 0,
            px: 0.75,
            py: 0.25,
            borderRadius: "3px",
            bgcolor: alpha("#5ACE6D", isDark ? 0.14 : 0.12),
          }}
        >
          Fix
        </Typography>
        <Typography
          fontSize="12.5px"
          color="text.secondary"
          sx={{ lineHeight: 1.65, flex: 1 }}
        >
          {data.fix}
        </Typography>
      </Stack>

      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ mt: 0.25, flexWrap: "wrap" }}
      >
        <Button
          size="small"
          variant="outlined"
          startIcon={
            <Iconify
              icon="simple-icons:linear"
              width={12}
              sx={{ color: "#5E6AD2" }}
            />
          }
          onClick={
            linkedIssue?.url
              ? () => openExternal(linkedIssue.url)
              : onCreateLinear
          }
          sx={{
            height: 28,
            fontSize: "12px",
            borderRadius: "6px",
            textTransform: "none",
            borderColor: "divider",
            color: "text.primary",
            "&:hover": {
              borderColor: "text.secondary",
              bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.03),
            },
          }}
        >
          {linkedIssue?.url
            ? `View ${linkedIssue.id || "Linear issue"}`
            : "Create Linear issue"}
        </Button>

        <Box sx={{ flex: 1 }} />

        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:text-search" width={13} />}
          onClick={onOpenAnalyze}
          sx={{
            height: 28,
            fontSize: "12px",
            fontWeight: 600,
            borderRadius: "6px",
            textTransform: "none",
            borderColor: "divider",
            color: "text.primary",
            "&:hover": {
              borderColor: "text.secondary",
              bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.03),
            },
          }}
        >
          View reasoning
        </Button>
      </Stack>
    </Stack>
  );
}
AnalyzedState.propTypes = {
  data: PropTypes.object.isRequired,
  linkedIssue: PropTypes.shape({
    id: PropTypes.string,
    url: PropTypes.string,
  }),
  onCreateLinear: PropTypes.func,
  onOpenAnalyze: PropTypes.func,
};

// ── Main: ClusterHeadlineCard ────────────────────────────────────────────────
export default function ClusterHeadlineCard({
  error,
  onOpenAnalyze,
  onStartAnalysis,
  onCreateLinear,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const cachedRca = useMemo(() => cachedRcaFrom(error), [error]);
  const clusterId = error?.cluster_id;

  const thread = useErrorFeedStore(
    (s) => s.analyzeThreadsByCluster[clusterId] ?? null,
  );
  const setAnalyzePendingStart = useErrorFeedStore(
    (s) => s.setAnalyzePendingStart,
  );
  const collapsed = useErrorFeedStore((s) => s.clusterAnalysisCollapsed);
  const toggleCollapsed = useErrorFeedStore(
    (s) => s.toggleClusterAnalysisCollapsed,
  );

  const category = error?.fix_layer ?? "root cause";

  // Runner emits 5 steps; collapse to 3 visual phases (~2 runner steps each).
  const stepMessages = (thread?.messages ?? []).filter(
    (m) => m.type === MESSAGE_TYPE.STEP,
  );
  const advancedCount = stepMessages.filter(
    (m) => m.status === STEP_STATUS.RUNNING || m.status === STEP_STATUS.DONE,
  ).length;
  const activeStepIdx = Math.min(2, Math.floor(advancedCount / 2));

  // Priority: live thread > cached rca > not_analyzed. Re-runs append a fresh
  // synthesis message, so reading the LAST one keeps multiple syntheses
  // collapsed to the most recent result.
  const synthesisMsg = [...(thread?.messages ?? [])]
    .reverse()
    .find((m) => m.type === MESSAGE_TYPE.SYNTHESIS);

  let state;
  let data = null;
  if (thread?.runState === RUN_STATE.STREAMING) {
    state = "analyzing";
  } else if (synthesisMsg) {
    state = "analyzed";
    data = {
      synthesis: synthesisMsg.headline,
      fix: synthesisMsg.fix,
      confidence: synthesisMsg.confidence ?? CONFIDENCE.MEDIUM,
      category,
      analyzedAt: "just now",
    };
  } else if (cachedRca) {
    state = "analyzed";
    data = { ...cachedRca, category };
  } else {
    state = "not_analyzed";
  }

  const runAnalysis = () => {
    setAnalyzePendingStart(clusterId, true);
    if (typeof onStartAnalysis === "function") onStartAnalysis();
  };

  const noop = () => {};

  const collapsedSummary =
    state === "analyzing"
      ? "Debugging this cluster…"
      : state === "not_analyzed"
        ? "Not analyzed yet"
        : `${CONFIDENCE_LABEL[data.confidence] ?? "Analyzed"} · ${data.category}`;

  return (
    <Box
      sx={{
        position: "relative",
        borderRadius: "12px",
        border: "1px solid",
        borderColor: state === "analyzed" ? alpha(ACCENT, 0.25) : "divider",
        bgcolor: isDark ? alpha("#fff", 0.025) : "background.paper",
        // Subtle gradient wash for the analyzed state; flat for empty/analyzing.
        backgroundImage:
          state === "analyzed"
            ? `linear-gradient(135deg, ${alpha(ACCENT, isDark ? 0.05 : 0.025)} 0%, transparent 55%)`
            : "none",
        px: 2.25,
        py: 1.75,
        overflow: "hidden",
        transition: "border-color 0.2s, background-image 0.2s",
      }}
    >
      {/* ── Accordion header (always visible) ── */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        onClick={toggleCollapsed}
        sx={{ cursor: "pointer", userSelect: "none", minWidth: 0 }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.4}
          sx={{ flexShrink: 0 }}
        >
          <Iconify
            icon="mdi:star-four-points"
            width={11}
            sx={{ color: ACCENT }}
          />
          <Typography
            fontSize="10.5px"
            fontWeight={700}
            sx={{
              color: ACCENT,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
            }}
          >
            Cluster Analysis
          </Typography>
        </Stack>

        {/* Collapsed-only state hint */}
        {collapsed && (
          <>
            <Box
              sx={{
                width: 3,
                height: 3,
                borderRadius: "50%",
                bgcolor: "text.disabled",
                flexShrink: 0,
              }}
            />
            {state === "analyzing" && (
              <Box
                sx={{
                  width: 11,
                  height: 11,
                  borderRadius: "50%",
                  border: "2px solid",
                  borderColor: alpha(ACCENT, 0.25),
                  borderTopColor: ACCENT,
                  animation: "spin 0.8s linear infinite",
                  "@keyframes spin": { to: { transform: "rotate(360deg)" } },
                  flexShrink: 0,
                }}
              />
            )}
            <Typography
              fontSize="11px"
              color="text.secondary"
              noWrap
              sx={{ minWidth: 0 }}
            >
              {collapsedSummary}
            </Typography>
          </>
        )}

        <Box sx={{ flex: 1 }} />

        {!collapsed && state === "analyzed" && (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.6}
            sx={{ flexShrink: 0 }}
          >
            <Typography
              fontSize="10.5px"
              color="text.disabled"
              sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
            >
              Analyzed {data?.analyzedAt ?? "just now"}
            </Typography>
            <CustomTooltip show title="Re-run analysis (1 credit)" arrow>
              <Box
                onClick={(e) => {
                  e.stopPropagation();
                  runAnalysis();
                }}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 22,
                  height: 22,
                  borderRadius: "5px",
                  cursor: "pointer",
                  color: "text.disabled",
                  "&:hover": {
                    color: "text.primary",
                    bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.04),
                  },
                }}
              >
                <Iconify icon="mdi:refresh" width={13} />
              </Box>
            </CustomTooltip>
          </Stack>
        )}

        <CustomTooltip show title={collapsed ? "Expand" : "Collapse"} arrow>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22,
              height: 22,
              borderRadius: "5px",
              color: "text.secondary",
              flexShrink: 0,
              "&:hover": {
                bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.04),
              },
            }}
          >
            <Iconify
              icon="mdi:chevron-down"
              width={17}
              sx={{
                transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
                transition: "transform 0.2s",
              }}
            />
          </Box>
        </CustomTooltip>
      </Stack>

      {/* ── Accordion body ── */}
      <Collapse in={!collapsed} timeout={220} unmountOnExit>
        <Box sx={{ mt: 1.5 }}>
          {state === "not_analyzed" && (
            <NotAnalyzedState onAnalyze={runAnalysis} />
          )}
          {state === "analyzing" && (
            <AnalyzingState activeStepIdx={activeStepIdx} />
          )}
          {state === "analyzed" && (
            <AnalyzedState
              data={data}
              linkedIssue={
                error?.external_issue_url
                  ? {
                      id: error?.external_issue_id ?? null,
                      url: error?.external_issue_url,
                    }
                  : null
              }
              onCreateLinear={onCreateLinear ?? noop}
              onOpenAnalyze={onOpenAnalyze ?? noop}
            />
          )}
        </Box>
      </Collapse>
    </Box>
  );
}
ClusterHeadlineCard.propTypes = {
  error: PropTypes.object.isRequired,
  onOpenAnalyze: PropTypes.func,
  onStartAnalysis: PropTypes.func,
  onCreateLinear: PropTypes.func,
};
