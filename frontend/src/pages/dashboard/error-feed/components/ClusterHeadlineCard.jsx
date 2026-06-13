import React, { useMemo } from "react";
import {
  Box,
  Button,
  Collapse,
  Stack,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import openExternal from "../openExternal";
import { useErrorFeedStore } from "../store";

// Format the cached-analysis timestamp into a short relative label.
function formatAnalyzedAt(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const mins = Math.floor((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return then.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Pull the cached cluster-RCA result (PRD §7.1) off the detail payload. Returns
// null when the cluster has never been analyzed — the card shows its empty
// state then, never a fabricated summary. snake_case is the canonical wire
// form; the axios bridge also exposes camel aliases, so tolerate both.
function cachedRcaFrom(error) {
  const rca = error?.rca;
  if (!rca || !rca.synthesis) return null;
  return {
    synthesis: rca.synthesis,
    fix: rca.fix,
    confidence: rca.confidence ?? "M",
    analyzedAt: formatAnalyzedAt(rca.analyzed_at ?? rca.analyzedAt),
    failuresAtRun: rca.failures_at_run ?? rca.failuresAtRun ?? null,
  };
}

const CONFIDENCE_LABEL = {
  H: "High confidence",
  M: "Medium confidence",
  L: "Low confidence",
};
const CONFIDENCE_DOT = { H: "#5ACE6D", M: "#F5A623", L: "#DB2F2D" };

const ACCENT = "#7857FC";

// ── Meta strip (top-row pills) ───────────────────────────────────────────────
function MetaStrip({
  confidence,
  category,
  analyzedAt,
  newSinceAnalysis,
  onRerun,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const stale = newSinceAnalysis > 0;

  return (
    <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
      <Stack direction="row" alignItems="center" gap={0.4}>
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: CONFIDENCE_DOT[confidence] ?? "#888",
          }}
        />
        <Typography
          fontSize="10.5px"
          fontWeight={600}
          color="text.secondary"
          sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
        >
          {CONFIDENCE_LABEL[confidence] ?? "—"}
        </Typography>
      </Stack>

      {category && (
        <>
          <Box
            sx={{
              width: 3,
              height: 3,
              borderRadius: "50%",
              bgcolor: "text.disabled",
            }}
          />
          <Typography
            fontSize="10.5px"
            fontWeight={500}
            color="text.disabled"
            sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
          >
            {category}
          </Typography>
        </>
      )}

      <Box sx={{ flex: 1 }} />

      <Stack direction="row" alignItems="center" gap={0.6}>
        {stale ? (
          <Tooltip title="Re-run analysis to include the new occurrences" arrow>
            <Stack
              direction="row"
              alignItems="center"
              gap={0.4}
              onClick={onRerun}
              sx={{
                cursor: "pointer",
                px: 0.75,
                py: 0.2,
                borderRadius: "4px",
                bgcolor: alpha("#F5A623", isDark ? 0.16 : 0.12),
                color: "#F5A623",
                "&:hover": { bgcolor: alpha("#F5A623", isDark ? 0.24 : 0.18) },
              }}
            >
              <Iconify icon="mdi:bell-outline" width={11} />
              <Typography
                fontSize="10.5px"
                fontWeight={600}
                sx={{ letterSpacing: "0.04em" }}
              >
                +{newSinceAnalysis} new · re-run
              </Typography>
            </Stack>
          </Tooltip>
        ) : (
          <Typography
            fontSize="10.5px"
            color="text.disabled"
            sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
          >
            Analyzed {analyzedAt}
          </Typography>
        )}
        <Tooltip title="Re-run analysis (1 credit)" arrow>
          <Box
            onClick={onRerun}
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 20,
              height: 20,
              borderRadius: "4px",
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
        </Tooltip>
      </Stack>
    </Stack>
  );
}
MetaStrip.propTypes = {
  confidence: PropTypes.string,
  category: PropTypes.string,
  analyzedAt: PropTypes.string,
  newSinceAnalysis: PropTypes.number,
  onRerun: PropTypes.func,
};

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

// Compact horizontal step chips. `activeStepIdx` marks progress: steps before
// it are done, the one at it is active, after it queued. Pass a value ≥ the
// step count to render them all as done (the analyzed state). Single-line,
// wraps if needed — keeps the card height stable across states.
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
function AnalyzedState({
  data,
  linkedIssue,
  onCreateLinear,
  onOpenAnalyze,
  onRerun,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack gap={1.25}>
      {/* MetaStrip (confidence dot + category + timestamp + rerun) used to
          live here. Confidence + category are still readable from the
          collapsed-summary text in the accordion header; the timestamp
          and rerun control moved next to the chevron at the top so the
          card stays compact post-analysis. */}

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
        {/* One Linear issue per cluster — once linked, this is a link-out,
            never a silent redirect dressed up as "create". */}
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

        {/* Secondary — jumps to the Analyze tab to read the full reasoning. */}
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
  onRerun: PropTypes.func,
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
  const clusterId = error?.clusterId;

  // Observe the SAME thread state the Analyze tab observes — both views
  // are now driven by the single shared `useAnalyzeRunner` hook that the
  // parent (ErrorFeedDetailView) mounts. Clicking any analyze button
  // anywhere just sets the pending flag, which the runner consumes.
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

  // The category pill comes from the cluster's fix layer (the agent's
  // synthesis doesn't carry one), shared by the live and cached paths.
  const category = error?.fixLayer ?? error?.fix_layer ?? "root cause";

  // Step pill progress is derived from how many of the runner's step
  // messages have advanced past "queued". The runner emits 5 steps; we
  // collapse them to 3 visual phases for the card (each phase covers
  // ~2 of the runner's steps).
  const stepMessages = (thread?.messages ?? []).filter(
    (m) => m.type === "step",
  );
  const advancedCount = stepMessages.filter(
    (m) => m.status === "running" || m.status === "done",
  ).length;
  const activeStepIdx = Math.min(2, Math.floor(advancedCount / 2));

  // Synthesis content + display mode. Priority:
  //   1. live synthesis from the current run (thread) — freshest
  //   2. cached rca_* from a prior run (detail payload) — survives reload
  //   3. nothing → genuine "not analyzed" empty state (never a fake summary)
  const synthesisMsg = [...(thread?.messages ?? [])]
    .reverse()
    .find((m) => m.type === "synthesis");

  let state;
  let data = null;
  let newSinceAnalysis = 0;
  if (thread?.runState === "streaming") {
    state = "analyzing";
  } else if (synthesisMsg) {
    state = "analyzed";
    data = {
      synthesis: synthesisMsg.headline,
      fix: synthesisMsg.fix,
      confidence: synthesisMsg.confidence ?? "M",
      category,
      analyzedAt: "just now",
    };
    // Just ran against the current cluster state — nothing new since.
  } else if (cachedRca) {
    state = "analyzed";
    data = { ...cachedRca, category };
    const current = error?.traceCount ?? error?.occurrences ?? 0;
    newSinceAnalysis =
      cachedRca.failuresAtRun != null
        ? Math.max(0, current - cachedRca.failuresAtRun)
        : 0;
  } else {
    state = "not_analyzed";
  }

  // Empty-state CTA: kick the shared run via the pending flag. The runner
  // (hooked at the parent level) will pick it up and start streaming.
  const runAnalysis = () => {
    setAnalyzePendingStart(clusterId, true);
    if (typeof onStartAnalysis === "function") onStartAnalysis();
  };

  const noop = () => {};

  // One-line summary shown next to the header when collapsed, so the card
  // still communicates its state without being expanded.
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
        // Subtle gradient wash for analyzed/stale; flat for empty/analyzing.
        backgroundImage:
          state === "analyzed" || state === "stale"
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

        {/* Analyzed timestamp + rerun control — visible when expanded on
            an analyzed state. Lives in the header now (replaces the old
            MetaStrip that sat in the body) so the box stays compact. */}
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
            <Tooltip title="Re-run analysis (1 credit)" arrow>
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
            </Tooltip>
          </Stack>
        )}

        <Tooltip title={collapsed ? "Expand" : "Collapse"} arrow>
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
        </Tooltip>
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
          {(state === "analyzed" || state === "stale") && (
            <AnalyzedState
              data={{
                ...data,
                newSinceAnalysis,
              }}
              linkedIssue={
                error?.external_issue_url || error?.externalIssueUrl
                  ? {
                      id:
                        error?.external_issue_id ??
                        error?.externalIssueId ??
                        null,
                      url: error?.external_issue_url ?? error?.externalIssueUrl,
                    }
                  : null
              }
              onCreateLinear={onCreateLinear ?? noop}
              onOpenAnalyze={onOpenAnalyze ?? noop}
              onRerun={runAnalysis}
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
