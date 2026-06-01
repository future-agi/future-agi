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
import { useErrorFeedStore } from "../store";

// Inline stub — replace with real data once `useClusterRCA(clusterId)` lands.
// PRD §7.1 specifies `rca_synthesis`, `rca_fix`, `rca_confidence`, `rca_at`,
// `rca_failures_at_run` fields on TraceErrorGroup; this function fakes them
// off the cluster name + size until that work ships.
function buildStubRCA(error) {
  const name = error?.error?.name ?? "this cluster";
  const traceCount = error?.traceCount ?? 0;
  return {
    confidence: "H",
    category: "fix in prompt",
    analyzedAt: "just now",
    synthesis:
      `${name} occurs when the agent drops critical user context across turns. ` +
      `The model re-asks for already-provided inputs, degrading task completion in ~31% of the ${traceCount.toLocaleString()} affected traces.`,
    fix:
      "Add a one-line guard in the system prompt restating already-supplied user inputs before each tool dispatch.",
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
    <Stack
      direction="row"
      alignItems="center"
      gap={2}
      sx={{ py: 0.5 }}
    >
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
        <Iconify icon="mdi:star-four-points-outline" width={18} sx={{ color: ACCENT }} />
      </Box>
      <Stack flex={1} gap={0.4} minWidth={0}>
        <Typography fontSize="14px" fontWeight={600} color="text.primary">
          No analysis yet
        </Typography>
        <Typography fontSize="12px" color="text.secondary" sx={{ lineHeight: 1.55 }}>
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
          i < activeStepIdx ? "done" : i === activeStepIdx ? "active" : "queued";
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
  onApplyFix,
  onCreateLinear,
  onOpenAnalyze,
  onRerun,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack gap={1.25}>
      <MetaStrip
        confidence={data.confidence}
        category={data.category}
        analyzedAt={data.analyzedAt}
        newSinceAnalysis={data.newSinceAnalysis}
        onRerun={onRerun}
      />

      {/* Step chips are intentionally NOT rendered post-completion. Earlier
          we kept them visible (all-done) to keep the box height stable
          across analyzing → analyzed, but the resulting card was too tall
          for an at-a-glance result; we'd rather have a compact summary. */}

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
        <Tooltip title="Code-aware fix application — coming soon" arrow>
          <span>
            <Button
              size="small"
              variant="contained"
              disabled
              startIcon={<Iconify icon="mdi:auto-fix" width={13} />}
              onClick={onApplyFix}
              sx={{
                height: 28,
                fontSize: "12px",
                fontWeight: 600,
                borderRadius: "6px",
                textTransform: "none",
                bgcolor: ACCENT,
                "&:hover": { bgcolor: "#6845E8" },
                "&.Mui-disabled": {
                  bgcolor: "action.disabledBackground",
                  color: "action.disabled",
                },
                boxShadow: "none",
              }}
            >
              Apply fix
            </Button>
          </span>
        </Tooltip>

        <Button
          size="small"
          variant="outlined"
          startIcon={
            <Iconify icon="simple-icons:linear" width={12} sx={{ color: "#5E6AD2" }} />
          }
          onClick={onCreateLinear}
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
          Create Linear issue
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
  onApplyFix: PropTypes.func,
  onCreateLinear: PropTypes.func,
  onOpenAnalyze: PropTypes.func,
  onRerun: PropTypes.func,
};

// ── Main: ClusterHeadlineCard ────────────────────────────────────────────────
export default function ClusterHeadlineCard({
  error,
  onOpenAnalyze,
  onStartAnalysis,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const stubFallback = useMemo(() => buildStubRCA(error), [error]);
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

  // Map the shared run state to the headline card's 3 display modes.
  let state;
  if (!thread) state = "not_analyzed";
  else if (thread.runState === "streaming") state = "analyzing";
  else state = "analyzed";

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

  // Synthesis content for the analyzed state — prefer the latest synthesis
  // message produced by the runner; otherwise fall back to the static stub
  // so the card never shows a blank.
  const synthesisMsg = [...(thread?.messages ?? [])]
    .reverse()
    .find((m) => m.type === "synthesis");

  const data = synthesisMsg
    ? {
        synthesis: synthesisMsg.headline,
        fix: synthesisMsg.fix,
        confidence: synthesisMsg.confidence ?? "H",
        category: synthesisMsg.category ?? "fix in prompt",
        analyzedAt: "just now",
      }
    : stubFallback;

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
                newSinceAnalysis: state === "stale" ? 47 : 0,
              }}
              onApplyFix={noop}
              onCreateLinear={noop}
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
};
