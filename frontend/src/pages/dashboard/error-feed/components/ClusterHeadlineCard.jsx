import React, { useMemo } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { formatDistanceToNowStrict } from "date-fns";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import openExternal from "../openExternal";
import { useErrorFeedStore } from "../store";
import { CONFIDENCE, MESSAGE_TYPE, RUN_STATE, STEP_STATUS } from "../constants";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";

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

// ── State: not_analyzed (empty) ──────────────────────────────────────────────
function NotAnalyzedState({ onAnalyze }) {
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
          bgcolor: "status.discovery.bg",
          flexShrink: 0,
        }}
      >
        <Iconify
          icon="solar:stars-minimalistic-linear"
          width={20}
          sx={{ color: "status.discovery.fg" }}
        />
      </Box>
      <Stack flex={1} gap={0.4} minWidth={0}>
        <Typography fontSize="13px" fontWeight={600} color="text.primary">
          No root cause yet
        </Typography>
        <Typography
          fontSize="12px"
          color="text.secondary"
          sx={{ lineHeight: 1.55 }}
        >
          Get a plain-English explanation and a suggested fix.
        </Typography>
      </Stack>
      <Button
        size="small"
        variant="contained"
        startIcon={
          <Iconify icon="solar:stars-minimalistic-linear" width={14} />
        }
        onClick={onAnalyze}
        sx={{ flexShrink: 0 }}
      >
        Find root cause
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
              borderRadius: "4px",
              border: "1px solid",
              borderColor: isActive ? "status.discovery.border" : "divider",
              bgcolor: isActive ? "status.discovery.bg" : "action.hover",
              opacity: status === "queued" ? 0.55 : 1,
            }}
          >
            <Iconify
              icon={
                isDone
                  ? "solar:check-linear"
                  : isActive
                    ? "solar:menu-dots-linear"
                    : "solar:circle-linear"
              }
              width={12}
              sx={{
                color: isDone
                  ? "status.success.fg"
                  : isActive
                    ? "status.discovery.fg"
                    : "text.disabled",
              }}
            />
            <Typography
              fontSize="12px"
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
            borderColor: "status.discovery.border",
            borderTopColor: "status.discovery.solid",
            animation: "spin 0.8s linear infinite",
            "@keyframes spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
        <Typography fontSize="13px" fontWeight={600} color="text.primary">
          Finding root cause…
        </Typography>
      </Stack>
      <StepChips activeStepIdx={activeStepIdx} />
    </Stack>
  );
}
AnalyzingState.propTypes = { activeStepIdx: PropTypes.number };

// ── State: analyzed (default after run) ──────────────────────────────────────
function AnalyzedState({ data, linkedIssue, onCreateLinear, onOpenAnalyze }) {
  return (
    <Stack gap={1.25}>
      <Typography
        fontSize="13px"
        fontWeight={500}
        color="text.primary"
        sx={{ lineHeight: 1.6 }}
      >
        {data.synthesis}
      </Typography>

      <Stack direction="row" gap={1} alignItems="flex-start">
        <Typography
          fontSize="12px"
          fontWeight={600}
          sx={{
            color: "status.success.fg",
            textTransform: "none",
            letterSpacing: 0,
            mt: "3px",
            flexShrink: 0,
            px: 0.75,
            py: 0.25,
            borderRadius: "4px",
            bgcolor: "status.success.bg",
          }}
        >
          Suggested fix
        </Typography>
        <Typography
          fontSize="13px"
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
              // eslint-disable-next-line no-restricted-syntax -- ds: data colour
              sx={{ color: "#5E6AD2" }}
            />
          }
          onClick={
            linkedIssue?.url
              ? () => openExternal(linkedIssue.url)
              : onCreateLinear
          }
          sx={{
            borderColor: "divider",
            color: "text.primary",
            "&:hover": {
              borderColor: "text.secondary",
              bgcolor: "action.hover",
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
          startIcon={<Iconify icon="solar:file-search-linear" width={14} />}
          onClick={onOpenAnalyze}
          sx={{
            borderColor: "divider",
            color: "text.primary",
            "&:hover": {
              borderColor: "text.secondary",
              bgcolor: "action.hover",
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
  // Failures are carried as synthesis messages so they render in the thread,
  // but they are not results: promoting one here would headline the cluster
  // with "Couldn't start the analysis", stamped "Analyzed just now" and
  // offering to file it as a Linear issue. Fall back to the last real result.
  const synthesisMsg = [...(thread?.messages ?? [])]
    .reverse()
    .find((m) => m.type === MESSAGE_TYPE.SYNTHESIS && m.category !== "error");

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
      ? "Finding root cause…"
      : state === "not_analyzed"
        ? "No root cause yet"
        : `${CONFIDENCE_LABEL[data.confidence] ?? "Found"} · ${data.category}`;

  return (
    <Box
      sx={{
        position: "relative",
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        px: 2.25,
        py: 1.75,
        overflow: "hidden",
        transition: "border-color 0.2s",
      }}
    >
      {/* ── Accordion header (always visible) ── */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        {...clickable(toggleCollapsed, { expanded: !collapsed })}
        sx={{
          cursor: "pointer",
          userSelect: "none",
          minWidth: 0,
          ...focusRing,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.4}
          sx={{ flexShrink: 0 }}
        >
          <Iconify
            icon="solar:stars-minimalistic-linear"
            width={12}
            sx={{ color: "status.discovery.fg" }}
          />
          <Typography
            fontSize="12px"
            fontWeight={600}
            sx={{
              color: "status.discovery.fg",
              textTransform: "none",
              letterSpacing: 0,
            }}
          >
            Root cause
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
                  borderColor: "status.discovery.border",
                  borderTopColor: "status.discovery.solid",
                  animation: "spin 0.8s linear infinite",
                  "@keyframes spin": { to: { transform: "rotate(360deg)" } },
                  flexShrink: 0,
                }}
              />
            )}
            <Typography
              fontSize="12px"
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
              fontSize="12px"
              color="text.disabled"
              sx={{ textTransform: "none", letterSpacing: 0 }}
            >
              Found {data?.analyzedAt ?? "just now"}
            </Typography>
            <CustomTooltip show title="Find root cause again (1 credit)" arrow>
              <Box
                {...clickable(
                  (e) => {
                    e.stopPropagation();
                    runAnalysis();
                  },
                  { label: "Find root cause again" },
                )}
                sx={{
                  ...focusRing,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 22,
                  height: 22,
                  borderRadius: "4px",
                  cursor: "pointer",
                  color: "text.disabled",
                  "&:hover": {
                    color: "text.primary",
                    bgcolor: "action.selected",
                  },
                }}
              >
                <Iconify icon="solar:refresh-linear" width={14} />
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
              borderRadius: "4px",
              color: "text.secondary",
              flexShrink: 0,
              "&:hover": {
                bgcolor: "action.selected",
              },
            }}
          >
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={16}
              sx={{
                transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
                transition: "transform 0.2s",
              }}
            />
          </Box>
        </CustomTooltip>
      </Stack>

      {/* ── Accordion body ── */}
      <Collapse in={!collapsed} timeout={200} unmountOnExit>
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
