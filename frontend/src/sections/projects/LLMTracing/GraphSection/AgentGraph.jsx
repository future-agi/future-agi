/* eslint-disable no-restricted-syntax -- ds: data colours (span-type encodings) */
import { formatExactMoney } from "src/sections/common/format/money";
import { isIncompleteCost, monetaryCostLabel } from "./common";
/**
 * AgentGraph — Aggregate agent DAG visualization
 *
 * Horizontal (LR) layout showing span type nodes with edges.
 *   - Nodes show: icon, name, span count
 *   - Hover tooltip: type, span count, avg latency, tokens, errors, error rate
 *   - Edges: thickness scaled by transition count, labels for high-count transitions
 *   - Start/Stop sentinel nodes as circles
 */
import React, { useMemo, useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  useReactFlow,
  useNodesInitialized,
  useStore,
  Handle,
  Position,
  MarkerType,
  getViewportForBounds,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Dagre from "@dagrejs/dagre";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { error as errorPalette, success } from "src/theme/palette";
import FullscreenGraphDialog from "./FullscreenGraphDialog";
import {
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  GRAPH_LOADING_MESSAGE,
} from "src/utils/queryReadState";

// ---------------------------------------------------------------------------
// Diff-overlay status (set by buildGraphDiff for the error-feed split view)
// ---------------------------------------------------------------------------
const DIFF_STATUS = {
  FAIL_ONLY: "fail-only",
  PASS_ONLY: "pass-only",
  PASS_ONLY_GHOST: "pass-only-ghost",
  MATCHED_REGRESSED: "matched-regressed",
  MATCHED: "matched",
  // Two-run overlay (experiments): A = baseline run, B = the
  // other run. The ring takes the run's colour from `data._diffColor`.
  ONLY_A: "only-a",
  ONLY_B: "only-b",
  BOTH: "both",
  BOTH_REGRESSED: "both-regressed",
};
const RUN_DIFF_STATUSES = new Set([
  DIFF_STATUS.ONLY_A,
  DIFF_STATUS.ONLY_B,
  DIFF_STATUS.BOTH,
  DIFF_STATUS.BOTH_REGRESSED,
]);

// Amber regression accent — not a 1:1 palette token yet, kept local.
const DIFF_REGRESSED_COLOR = "#F5A623";

// `_isFailurePoint` is the headline and takes priority over diff status.
// Two-run statuses also return the visit chip `2× · 1×` from
// `_visits = {a, b}`; every other status returns `visits: null`.
export const getDiffOverlay = (diffStatus, isFailurePoint, data = {}) => {
  if (RUN_DIFF_STATUSES.has(diffStatus)) {
    const v = data?._visits;
    const visits =
      v && (Number.isFinite(v.a) || Number.isFinite(v.b))
        ? `${v.a ?? 0}× · ${v.b ?? 0}×`
        : null;
    if (isFailurePoint)
      return { ringColor: errorPalette.main, badge: "✕", label: "Failed", visits }; // prettier-ignore
    const runColor = data?._diffColor ?? "#64748b";
    switch (diffStatus) {
      case DIFF_STATUS.ONLY_A:
        return { ringColor: runColor, badge: "A", label: null, visits };
      case DIFF_STATUS.ONLY_B:
        return { ringColor: runColor, badge: "B", label: null, visits };
      case DIFF_STATUS.BOTH_REGRESSED:
        return { ringColor: DIFF_REGRESSED_COLOR, badge: "Δ", label: null, visits }; // prettier-ignore
      default:
        return { ringColor: null, badge: null, label: null, visits };
    }
  }
  if (isFailurePoint) {
    return { ringColor: errorPalette.main, badge: "✕", label: "Failed" };
  }
  switch (diffStatus) {
    case DIFF_STATUS.FAIL_ONLY:
      return { ringColor: errorPalette.main, badge: "+", label: null };
    case DIFF_STATUS.PASS_ONLY:
      return { ringColor: success.main, badge: "+", label: null };
    case DIFF_STATUS.PASS_ONLY_GHOST:
      return { ringColor: success.main, badge: "−", label: "Missing" };
    case DIFF_STATUS.MATCHED_REGRESSED:
      return { ringColor: DIFF_REGRESSED_COLOR, badge: "Δ", label: null };
    default:
      return { ringColor: null, badge: null, label: null };
  }
};

// ---------------------------------------------------------------------------
// Node type → color + icon
// ---------------------------------------------------------------------------
const TYPE_COLORS = {
  agent: { text: "#7857FC", icon: "solar:bot-linear", label: "Agent" },
  llm: { text: "#2563eb", icon: "solar:brain-linear", label: "LLM" },
  generation: {
    text: "#2563eb",
    icon: "solar:brain-linear",
    label: "Generation",
  },
  tool: { text: "#16a34a", icon: "solar:toolbox-linear", label: "Tool" },
  retriever: {
    text: "#0d9488",
    icon: "solar:magnifier-linear",
    label: "Retriever",
  },
  chain: { text: "#c026d3", icon: "solar:link-linear", label: "Chain" },
  embedding: {
    text: "#ea580c",
    icon: "solar:graph-linear",
    label: "Embedding",
  },
  guardrail: {
    text: "#dc2626",
    icon: "solar:shield-check-linear",
    label: "Guardrail",
  },
  aggregate: {
    text: "#71717a",
    icon: "solar:menu-dots-linear",
    label: "Aggregate",
  },
  unknown: {
    text: "#6b7280",
    icon: "solar:question-circle-linear",
    label: "Unknown",
  },
  start: { text: "#16a34a", icon: "solar:play-circle-linear", label: "Start" },
  end: { text: "#dc2626", icon: "solar:stop-circle-linear", label: "Stop" },
};

// Derive bg (alpha 0.08) and border (alpha 0.24) from the text color at runtime
const getColor = (type) => {
  const c = TYPE_COLORS[type] || TYPE_COLORS.unknown;
  return { ...c, bg: alpha(c.text, 0.08), border: alpha(c.text, 0.24) };
};

// Format milliseconds nicely
const fmtMs = (ms) => {
  if (ms == null || ms <= 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
};

// ---------------------------------------------------------------------------
// Custom node — shows icon + name + span count, rich tooltip on hover
// ---------------------------------------------------------------------------
const handleStyle = {
  background: "transparent",
  border: "none",
  width: 1,
  height: 1,
};

const AgentNode = ({ data }) => {
  const color = getColor(data.type);
  const isSentinel = data.type === "start" || data.type === "end";
  const isStart = data.type === "start";
  const isEnd = data.type === "end";
  const isVertical = data._direction === "TB";
  const errorRate =
    data.span_count > 0 && data.error_count > 0
      ? ((data.error_count / data.span_count) * 100).toFixed(1)
      : null;
  // Dim nodes that don't match the active filter
  const isDimmed = data._hasMatch === false;

  // ── Optional diff overlay (used by the error-feed Split-with-working view).
  // The Observe Tracing view never sets `_diffStatus` / `_isFailurePoint` so
  // this is fully inert there.
  //   "fail-only"          → extra step in failing trace          (red, "+")
  //   "pass-only"          → step missing from failing            (green, "+")
  //   "pass-only-ghost"    → missing step ghosted INTO failing    (green dash, "−")
  //   "matched-regressed"  → same step both sides, but failing has
  //                          errors / much slower                 (amber, "Δ")
  //   "matched" / unset    → no overlay
  //
  // `_isFailurePoint` is the actual failure marker — it takes visual priority
  // over diff status (a failed node is the headline, not a diff cue).
  const diffStatus = data._diffStatus;
  const isFailurePoint = !!data._isFailurePoint;
  // The baseline-only side of a two-run overlay is a ghost too.
  const isGhost =
    diffStatus === DIFF_STATUS.PASS_ONLY_GHOST ||
    diffStatus === DIFF_STATUS.ONLY_A;

  const {
    ringColor: overlayRingColor,
    badge: diffBadge,
    label: diffBadgeLabel,
    visits: visitChip,
  } = getDiffOverlay(diffStatus, isFailurePoint, data);
  // Additive, inert when absent: `_heat` 0–1 tints the ring red
  // by failure share (Paths), `_loops` ≥ 2 shows `↻ 2×`, `_selected` rings
  // the selected node.
  const heat = Number.isFinite(data._heat) && data._heat > 0 ? data._heat : 0;
  // IT-R5 G4 P1 (additive, inert when absent): `_status` rings a step
  // green (ok) or red (error) when no diff or heat colour applies.
  const statusRing =
    data._status === "error"
      ? errorPalette.main
      : data._status === "ok"
        ? success.main
        : null;
  const diffRingColor =
    overlayRingColor ??
    (heat
      ? alpha(errorPalette.main, 0.25 + 0.75 * Math.min(1, heat))
      : statusRing);
  const loops =
    Number.isFinite(data._loops) && data._loops > 1 ? data._loops : 0;
  const isSelectedNode = data._selected === true;

  // Tooltip: name, duration/tokens side-by-side, cost, eval bars, annotations
  const evals = data.evals || [];
  const annotations = data.annotations || [];
  // Dedupe evals by name, average their scores
  const evalMap = {};
  evals.forEach((e) => {
    const name = e.eval_name || e.eval_config_id || "eval";
    const score =
      e.score ?? (e.result === true ? 100 : e.result === false ? 0 : null);
    if (score == null) return;
    if (!evalMap[name]) evalMap[name] = { name, total: 0, count: 0 };
    evalMap[name].total += score;
    evalMap[name].count += 1;
  });
  const evalSummary = Object.values(evalMap).map((e) => ({
    name: e.name,
    score: e.total / e.count,
  }));
  const lowEvals = evalSummary.filter((e) => e.score < 50);

  const tooltipContent = !isSentinel ? (
    <Box sx={{ minWidth: 200, maxWidth: 260 }}>
      {/* Header: icon + name */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          py: 0.75,
          borderBottom: "1px solid rgba(255,255,255,0.1)",
        }}
      >
        <Iconify icon={color.icon} width={14} color={color.border} />
        <Typography sx={{ fontWeight: 600, fontSize: 12, color: "#fff" }}>
          {data.name}
        </Typography>
      </Box>

      {/* Metrics: Duration | Total Tokens */}
      <Box sx={{ display: "flex", gap: 2, px: 1.25, pt: 0.75 }}>
        <Box>
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Duration
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {fmtMs(data.avg_latency_ms) || "0ms"}
          </Typography>
        </Box>
        <Box>
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Total Tokens
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {data.total_tokens > 0
              ? `${data.total_tokens.toLocaleString()} tok`
              : data._wrap
                ? "not reported"
                : "—"}
          </Typography>
        </Box>
      </Box>

      {/* Cost */}
      {(data.cost_coverage || data.total_cost > 0 || data.avg_cost > 0) && (
        <Box sx={{ px: 1.25, pt: 0.25 }}>
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Estimated LLM cost
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {isIncompleteCost(data.cost_coverage)
              ? monetaryCostLabel(
                  data.total_cost ?? data.avg_cost,
                  data.cost_coverage,
                )
              : formatExactMoney(data.total_cost ?? data.avg_cost, {
                  minimumFractionDigits: 4,
                })}
          </Typography>
        </Box>
      )}

      {/* Spans count */}
      {data.span_count > 1 && (
        <Box sx={{ px: 1.25, pt: 0.25 }}>
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Runs
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {data.span_count}
          </Typography>
        </Box>
      )}

      {/* Errors */}
      {data.error_count > 0 && (
        <Box sx={{ px: 1.25, pt: 0.25 }}>
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Errors
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#f87171", fontWeight: 600 }}>
            {data.error_count} {errorRate && `(${errorRate}%)`}
          </Typography>
        </Box>
      )}

      {/* Evals — all evals with progress bars */}
      {evalSummary.length > 0 && (
        <Box
          sx={{
            px: 1.25,
            pt: 0.75,
            pb: 0.5,
            borderTop: "1px solid rgba(255,255,255,0.1)",
            mt: 0.5,
          }}
        >
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
              mb: 0.5,
            }}
          >
            {lowEvals.length > 0 ? "Low performance evals" : "Evals"}
          </Typography>
          {(lowEvals.length > 0 ? lowEvals : evalSummary)
            .slice(0, 4)
            .map((ev) => (
              <Box key={ev.name} sx={{ mb: 0.5 }}>
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    mb: 0.15,
                  }}
                >
                  <Typography sx={{ fontSize: 12, color: "#fff" }}>
                    {ev.name}
                  </Typography>
                  <Typography
                    sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}
                  >
                    {Math.round(ev.score)}%
                  </Typography>
                </Box>
                <Box
                  sx={{
                    height: 4,
                    borderRadius: 2,
                    bgcolor: "rgba(255,255,255,0.15)",
                    overflow: "hidden",
                  }}
                >
                  <Box
                    sx={{
                      height: "100%",
                      width: `${Math.min(100, Math.max(0, ev.score))}%`,
                      borderRadius: 2,
                      bgcolor:
                        ev.score < 25
                          ? "#ef4444"
                          : ev.score < 50
                            ? "#f59e0b"
                            : "#22c55e",
                    }}
                  />
                </Box>
              </Box>
            ))}
        </Box>
      )}

      {/* Annotations */}
      {annotations.length > 0 && (
        <Box
          sx={{
            px: 1.25,
            pt: 0.5,
            pb: 0.5,
            borderTop: "1px solid rgba(255,255,255,0.1)",
            mt: evalSummary.length > 0 ? 0 : 0.5,
          }}
        >
          <Typography
            sx={{
              fontSize: 12,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
              mb: 0.25,
            }}
          >
            Annotations ({annotations.length})
          </Typography>
          {annotations.slice(0, 3).map((ann, i) => (
            <Box
              key={i}
              sx={{
                display: "flex",
                justifyContent: "space-between",
                py: 0.15,
              }}
            >
              <Typography sx={{ fontSize: 12, color: "#fff" }}>
                {ann.label_name ||
                  ann.annotation_label_name ||
                  `Annotation ${i + 1}`}
              </Typography>
              <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
                {ann.score != null ? `${ann.score}` : ann.value || "—"}
              </Typography>
            </Box>
          ))}
          {annotations.length > 3 && (
            <Typography
              sx={{ fontSize: 12, color: "rgba(255,255,255,0.4)", mt: 0.25 }}
            >
              +{annotations.length - 3} more
            </Typography>
          )}
        </Box>
      )}

      <Box sx={{ pb: 0.25 }} />
    </Box>
  ) : null;

  return (
    <>
      {/* Handles — direction-aware */}
      {!isStart && (
        <Handle
          type="target"
          position={isVertical ? Position.Top : Position.Left}
          style={handleStyle}
        />
      )}
      {!isEnd && (
        <Handle
          type="source"
          position={isVertical ? Position.Bottom : Position.Right}
          style={handleStyle}
        />
      )}

      <CustomTooltip
        show={!isSentinel}
        type="black"
        size="small"
        placement="top"
        title={tooltipContent}
        // IT-R2 (additive, `_wrap` only): the card closes as the pointer
        // moves off the node (never sticks over the toolbar).
        {...(data._wrap ? { disableInteractive: true } : {})}
      >
        <Box
          sx={{
            position: "relative",
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            px: isSentinel ? 1 : 1.25,
            py: isSentinel ? 0.25 : 0.5,
            borderRadius: isSentinel ? "20px" : "8px",
            // Ghosts get a dashed border so they read as "what could have been"
            // at a glance, even before the user notices the badge.
            border: isSentinel
              ? "1.5px solid"
              : isGhost
                ? "2px dashed"
                : "2px solid",
            borderColor: diffRingColor ?? color.border,
            // Failure point gets a soft red wash so the failed node is the
            // most visible thing in the graph.
            bgcolor: isSentinel
              ? "transparent"
              : isFailurePoint
                ? "status.danger.bg"
                : "background.paper",
            boxShadow: (theme) => {
              // DS uses a flat surface; selection and diff halos are state cues.
              if (isSelectedNode) {
                const selection = `0 0 0 3px ${theme.palette.primary.main}`;
                if (!diffRingColor) return selection;
                const haloAlpha = isFailurePoint ? 0.35 : 0.22;
                const haloRadius = isFailurePoint ? 4 : 3;
                return `${selection}, 0 0 0 ${haloRadius}px ${alpha(diffRingColor, haloAlpha)}`;
              }
              if (!diffRingColor) return "none";
              // Outer halo so the diff status reads at a glance even when
              // the graph is zoomed out. Failure points get a stronger halo.
              const haloAlpha = isFailurePoint ? 0.35 : 0.22;
              const haloRadius = isFailurePoint ? 4 : 3;
              const halo = `0 0 0 ${haloRadius}px ${alpha(diffRingColor, haloAlpha)}`;
              return halo;
            },
            minWidth: isSentinel ? 44 : 80,
            justifyContent: isSentinel ? "center" : "flex-start",
            cursor: isSentinel ? "default" : "pointer",
            // Ghosts read as semi-transparent without losing the badge.
            opacity: isGhost ? 0.6 : isDimmed ? 0.3 : 1,
            transition: "all 150ms ease",
            "&:hover": isSentinel
              ? {}
              : {
                  boxShadow: `0 0 0 2px ${diffRingColor ?? color.border}`,
                  opacity: 1,
                },
          }}
        >
          {/* Diff badge (top-right) — only rendered when this node was tagged
              by the error-feed diff helper. Pure decoration; doesn't affect
              the node's layout dimensions. Failure points and ghosts get a
              text label alongside the symbol so the meaning is unambiguous. */}
          {diffBadge && (
            <Box
              sx={{
                position: "absolute",
                // IT-R2 (additive, `_wrap` only = prototype readable graphs):
                // the badge sits beside the node, never over its label.
                ...(data._wrap
                  ? {
                      top: "50%",
                      left: "calc(100% + 6px)",
                      transform: "translateY(-50%)",
                    }
                  : { top: -8, right: -6 }),
                minWidth: 16,
                height: 16,
                px: diffBadgeLabel ? 0.65 : 0.5,
                gap: diffBadgeLabel ? 0.3 : 0,
                borderRadius: "8px",
                bgcolor: diffRingColor,
                color: (theme) => theme.palette.common.white,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 600,
                lineHeight: 1,
                fontFamily: "ui-monospace, SFMono-Regular, monospace",
                letterSpacing: "0.02em",
                textTransform: "none",
                pointerEvents: "none",
                whiteSpace: "nowrap",
              }}
            >
              <span>{diffBadge}</span>
              {diffBadgeLabel && <span>{diffBadgeLabel}</span>}
            </Box>
          )}
          <Iconify
            icon={color.icon}
            width={isSentinel ? 12 : 14}
            color={color.text}
          />
          <Box sx={{ overflow: "hidden" }}>
            {/* IT-R1 (additive, inert unless `_wrap`): up to 2 lines of
                about 24 characters; the full name is in the tooltip. */}
            <Typography
              noWrap={!data._wrap}
              sx={{
                fontWeight: 600,
                fontSize: isSentinel ? 10 : 12,
                color: color.text,
                lineHeight: 1.2,
                maxWidth: data._wrap && !isSentinel ? WRAP_LABEL_W : 100,
                ...(data._wrap && !isSentinel
                  ? {
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                      wordBreak: "break-word",
                    }
                  : {}),
              }}
            >
              {data._wrap && !isSentinel
                ? String(data.name).replace(/_/g, "_\u200b")
                : data.name}
            </Typography>
            {!isSentinel && !data._subtitle && data._model ? (
              // IT-R5 G4 P1 (additive): the step's model as a small chip.
              <Typography
                noWrap
                data-testid="graph-node-model"
                sx={{
                  display: "inline-block",
                  fontSize: 10,
                  lineHeight: 1.3,
                  px: 0.5,
                  mt: 0.25,
                  borderRadius: "4px",
                  bgcolor: "action.hover",
                  color: "text.secondary",
                  maxWidth: data._wrap ? WRAP_LABEL_W : 100,
                }}
              >
                {data._model}
              </Typography>
            ) : null}
            {!isSentinel && data._subtitle ? (
              <Typography
                noWrap
                sx={{
                  fontSize: 10,
                  color: "text.secondary",
                  lineHeight: 1.2,
                  maxWidth: data._wrap ? WRAP_LABEL_W : 100,
                }}
              >
                {data._subtitle}
              </Typography>
            ) : null}
            {/* Show span count directly on the node for non-sentinel */}
            {!isSentinel && data._meta ? (
              // IT-R5 G4 P1 (additive): `×7 · 2.6 s · 1,081 tok`.
              <Typography
                data-testid="graph-node-meta"
                sx={{
                  fontSize: 10.5,
                  color: "text.secondary",
                  lineHeight: 1.25,
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                }}
              >
                {data._meta}
              </Typography>
            ) : null}
            {!isSentinel && visitChip ? (
              <Typography
                data-visit-chip={visitChip}
                sx={{
                  fontSize: 12,
                  color: "text.secondary",
                  lineHeight: 1,
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                }}
              >
                {visitChip}
                {loops ? ` · ↻ ${loops}×` : ""}
              </Typography>
            ) : (
              !isSentinel &&
              !data._meta &&
              data.span_count > 0 && (
                <Typography
                  sx={{ fontSize: 9, color: "text.disabled", lineHeight: 1 }}
                >
                  {data.span_count.toLocaleString()}{" "}
                  {data.span_count === 1 ? "span" : "spans"}
                  {loops ? ` · ↻ ${loops}×` : ""}
                </Typography>
              )
            )}
          </Box>
        </Box>
      </CustomTooltip>
    </>
  );
};

AgentNode.propTypes = { data: PropTypes.object };

const nodeTypes = { agentNode: AgentNode };

// ---------------------------------------------------------------------------
// Dagre layout — direction-aware (LR for trace list, TB for trace detail)
// ---------------------------------------------------------------------------

// Stop closes the drawing; its edges record no relation between spans. If
// they took part in the layout, a leaf's distance to its parent and to Stop
// would cost the same, so dagre sank leaf siblings onto the row above Stop
// while siblings with nested work stayed one level below the parent:
// parallel steps landed on different levels and read as a sequence
// (TH-4321). Lay out the recorded graph alone, then put Stop one level past
// its deepest node.
const SENTINEL_WIDTH = 50;
const SENTINEL_HEIGHT = 32;

const isStopNode = (node) => node.data?.type === "end";

// Read rankdir the way dagre does: case-insensitive, LR/RL lay ranks along
// x, BT/RL reverse the rank axis, anything else is top-to-bottom.
const rankAxis = (direction) => {
  const dir = String(direction).toLowerCase();
  return {
    horizontal: dir === "lr" || dir === "rl",
    reversed: dir === "bt" || dir === "rl",
  };
};

const placeStop = (g, laidOutIds, direction, ranksep) => {
  if (!laidOutIds.length) return { x: 0, y: 0 };
  const boxes = laidOutIds.map((id) => g.node(id));
  const min = (pick) => Math.min(...boxes.map(pick));
  const max = (pick) => Math.max(...boxes.map(pick));
  const { horizontal, reversed } = rankAxis(direction);
  const [main, mainSize, stopSize, cross, crossSize] = horizontal
    ? ["x", "width", SENTINEL_WIDTH, "y", "height"]
    : ["y", "height", SENTINEL_HEIGHT, "x", "width"];
  const stopMain = reversed
    ? min((b) => b[main] - b[mainSize] / 2) - ranksep - stopSize / 2
    : max((b) => b[main] + b[mainSize] / 2) + ranksep + stopSize / 2;
  const stopCross =
    (min((b) => b[cross] - b[crossSize] / 2) +
      max((b) => b[cross] + b[crossSize] / 2)) /
    2;
  return { [main]: stopMain, [cross]: stopCross };
};

// A row means "runs after everything above it", so each node sits on the
// first row after its deepest recorded parent. dagre's default ranker only
// minimises total edge length, and whenever two rows cost the same it may
// push a node down next to an unrelated deeper branch (for example when
// every ChatOpenAI call is grouped into one node with parents on two
// levels). Called on dagre's internal graph after its acyclic pass, so a
// topological order exists; `minlen` already includes dagre's own spacing.
const rankAtEarliestLevel = (g) => {
  Dagre.graphlib.alg.topsort(g).forEach((v) => {
    g.node(v).rank = g
      .inEdges(v)
      .reduce(
        (rank, edge) =>
          Math.max(rank, g.node(edge.v).rank + g.edge(edge).minlen),
        0,
      );
  });
};

// IT-R1 (additive): wrapped labels need a wider, taller node box.
const WRAP_LABEL_W = 172;
const nodeBox = (node) =>
  node.data?._wrap
    ? { w: 212, h: node.data?._meta ? 76 : 62 }
    : { w: 140, h: 44 };

const layoutGraph = (nodes, edges, direction = "LR") => {
  const ranksep = direction === "LR" ? 80 : 50;
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: direction,
    ranksep,
    nodesep: 25,
    ranker: rankAtEarliestLevel,
  });

  const stopIds = new Set(nodes.filter(isStopNode).map((node) => node.id));
  const laidOutIds = [];
  nodes.forEach((node) => {
    if (stopIds.has(node.id)) return;
    const isSentinel = node.data?.type === "start";
    const box = nodeBox(node);
    g.setNode(node.id, {
      width: isSentinel ? SENTINEL_WIDTH : box.w,
      height: isSentinel ? SENTINEL_HEIGHT : box.h,
    });
    laidOutIds.push(node.id);
  });
  edges.forEach((edge) => {
    if (stopIds.has(edge.source) || stopIds.has(edge.target)) return;
    g.setEdge(edge.source, edge.target);
  });

  Dagre.layout(g);
  const stop = placeStop(g, laidOutIds, direction, ranksep);

  return nodes.map((node) => {
    const pos = stopIds.has(node.id) ? stop : g.node(node.id);
    const box = nodeBox(node);
    return {
      ...node,
      position: { x: pos.x - box.w / 2, y: pos.y - box.h / 2 },
    };
  });
};

// ---------------------------------------------------------------------------
// Build React Flow nodes + edges from API data
// ---------------------------------------------------------------------------
const NODE_METRICS = [
  "span_count",
  "avg_latency_ms",
  "total_tokens",
  "total_cost",
  "error_count",
];
const EDGE_METRICS = [
  "transition_count",
  "avg_latency_ms",
  "total_tokens",
  "total_cost",
  "error_count",
];

const assertFiniteMetric = (record, key, label) => {
  if (!Number.isFinite(record?.[key]) || record[key] < 0) {
    throw new Error(`${label} is missing canonical ${key}`);
  }
};

const assertTraceCount = (record, label) => {
  if (
    record?.trace_count_exact !== undefined &&
    typeof record.trace_count_exact !== "boolean"
  ) {
    throw new Error(`${label} has malformed trace_count_exact`);
  }
  if (record?.trace_count === null) {
    if (record.trace_count_exact !== false) {
      throw new Error(`${label} has an inexact trace_count without disclosure`);
    }
    return;
  }
  assertFiniteMetric(record, "trace_count", label);
};

const assertCanonicalGraph = (graphData) => {
  if (!graphData || typeof graphData !== "object") {
    throw new Error("Agent Graph data is missing");
  }
  if (!Array.isArray(graphData.nodes) || !Array.isArray(graphData.edges)) {
    throw new Error("Agent Graph data is missing nodes or edges");
  }

  graphData.nodes.forEach((node, index) => {
    const label = `Agent Graph node #${index}`;
    if (
      typeof node?.id !== "string" ||
      !node.id ||
      typeof node.name !== "string" ||
      !node.name ||
      typeof node.type !== "string" ||
      !node.type
    ) {
      throw new Error(`${label} is malformed`);
    }
    NODE_METRICS.forEach((key) => assertFiniteMetric(node, key, label));
    assertTraceCount(node, label);
  });

  graphData.edges.forEach((edge, index) => {
    const label = `Agent Graph edge #${index}`;
    if (
      typeof edge?.source !== "string" ||
      !edge.source ||
      typeof edge.target !== "string" ||
      !edge.target ||
      typeof edge.is_self_loop !== "boolean"
    ) {
      throw new Error(`${label} is malformed`);
    }
    EDGE_METRICS.forEach((key) => assertFiniteMetric(edge, key, label));
    assertTraceCount(edge, label);
  });
};

export const buildFlowData = (
  graphData,
  direction = "LR",
  theme = null,
  { wrapLabels = false } = {},
) => {
  assertCanonicalGraph(graphData);
  if (!graphData.nodes.length) return { nodes: [], edges: [] };

  const nodeIdSet = new Set(graphData.nodes.map((n) => n.id));
  if (nodeIdSet.size !== graphData.nodes.length) {
    throw new Error("Agent Graph contains duplicate node ids");
  }

  // Agent Graph renders the producer's canonical aggregate projection.
  // Agent Path consumes the separately recorded `path_edges` projection.
  // Substituting one for the other changes the meaning of both views.
  const graphEdges = graphData.edges;
  graphEdges.forEach((edge) => {
    if (!nodeIdSet.has(edge.source) || !nodeIdSet.has(edge.target)) {
      throw new Error(
        `Agent Graph edge references an unknown node: ${edge.source}->${edge.target}`,
      );
    }
  });

  const flowNodes = graphData.nodes.map((node) => ({
    id: node.id,
    type: "agentNode",
    data: {
      ...node,
      _direction: direction,
      ...(wrapLabels ? { _wrap: true } : {}),
    },
    position: { x: 0, y: 0 },
    connectable: false,
  }));

  // Find max transition count for scaling edge thickness
  const maxTransitions = Math.max(
    1,
    ...graphEdges.map((edge) => edge.transition_count),
  );

  const flowEdges = graphEdges.map((edge, idx) => {
    const count = edge.transition_count;
    // Scale thickness: 0.75px min, 2px max — subtle, not overpowering
    const thickness = 0.75 + (count / maxTransitions) * 1.25;

    const edgeColor = theme ? theme.palette.text.disabled : "#94a3b8";
    const strokeColor = theme ? theme.palette.divider : "#cbd5e1";
    const labelBgColor = theme ? theme.palette.background.paper : "#ffffff";

    // Synthetic "skipped path" edges from the error-feed diff helper
    // override the regular styling: dashed red stroke + a SKIPPED PATH
    // pill so the divergence is unmistakable. Inert in the Observe view.
    if (edge._skipped) {
      const skippedColor = errorPalette.main;
      return {
        id: `e-skipped-${idx}`,
        source: edge.source,
        target: edge.target,
        type: "default",
        animated: false,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: skippedColor,
          width: 10,
          height: 10,
        },
        style: {
          stroke: skippedColor,
          strokeWidth: 1.6,
          strokeDasharray: "6 4",
        },
        label: "SKIPPED PATH",
        labelStyle: {
          fontSize: 12,
          fill: "#fff",
          fontWeight: 600,
          letterSpacing: 0,
        },
        labelBgStyle: { fill: skippedColor, fillOpacity: 0.95 },
        labelBgPadding: [5, 3],
        labelBgBorderRadius: 4,
        zIndex: 5,
      };
    }

    // Additive, inert when absent: two-run overlay edges take
    // the run colour (`_diffColor`; A-only paths dashed, like ghosts), and
    // `_label` replaces the `×n` label (Paths: `18 · all fail`).
    if (RUN_DIFF_STATUSES.has(edge._diffStatus) || edge._label) {
      const runColor =
        edge._diffStatus === "both" || !edge._diffColor
          ? strokeColor
          : edge._diffColor;
      const markerColor =
        edge._diffStatus === "both" || !edge._diffColor
          ? edgeColor
          : edge._diffColor;
      const label =
        edge._label ?? (count > 1 && edge._diffStatus ? `×${count}` : null);
      return {
        id: `e-${idx}`,
        source: edge.source,
        target: edge.target,
        type: "default",
        animated: false,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: markerColor,
          width: 9,
          height: 9,
        },
        style: {
          stroke: runColor,
          strokeWidth: Math.max(1.2, Math.round(thickness * 10) / 10),
          ...(edge._diffStatus === "only-a"
            ? { strokeDasharray: "6 4", opacity: 0.6 }
            : {}),
        },
        ...(label && {
          label,
          labelStyle: { fontSize: 9, fill: edgeColor, fontWeight: 600 },
          labelBgStyle: { fill: labelBgColor, fillOpacity: 0.95 },
          labelBgPadding: [3, 2],
          labelBgBorderRadius: 3,
        }),
      };
    }

    return {
      id: `e-${idx}`,
      source: edge.source,
      target: edge.target,
      type: "default",
      animated: edge.is_self_loop,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: edgeColor,
        width: 8,
        height: 8,
      },
      style: {
        stroke: strokeColor,
        strokeWidth: Math.round(thickness * 10) / 10,
      },
      // Show transition count on edges with > 1 transition
      ...((count > 1 || isIncompleteCost(edge.cost_coverage)) && {
        label: isIncompleteCost(edge.cost_coverage)
          ? `×${count} · ${monetaryCostLabel(null, edge.cost_coverage)}`
          : `×${count}`,
        labelStyle: { fontSize: 12, fill: edgeColor, fontWeight: 500 },
        labelBgStyle: { fill: labelBgColor, fillOpacity: 0.9 },
        labelBgPadding: [3, 2],
      }),
    };
  });

  const layoutNodes = layoutGraph(flowNodes, flowEdges, direction);
  return { nodes: layoutNodes, edges: flowEdges };
};

// ---------------------------------------------------------------------------
// Zoom controls — top-right, appear on hover or keyboard focus. They stay
// mounted (hidden while idle) so Tab can reach them.
// ---------------------------------------------------------------------------
const ZoomControls = ({ isFullscreen, onToggleFullscreen, revealed }) => {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const btnSx = {
    p: 0.5,
    borderRadius: 0,
    borderRight: "1px solid",
    borderRightColor: "divider",
    "&:last-child": { borderRight: "none" },
    color: "text.secondary",
  };
  return (
    <Box
      sx={{
        position: "absolute",
        top: 8,
        right: 8,
        zIndex: 10,
        display: "flex",
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        overflow: "hidden",
        opacity: revealed ? 1 : 0,
        pointerEvents: revealed ? "auto" : "none",
        "&:focus-within": { opacity: 1, pointerEvents: "auto" },
      }}
    >
      <IconButton
        size="small"
        aria-label="Zoom in"
        title="Zoom in"
        onClick={() => zoomIn({ duration: 200 })}
        sx={btnSx}
      >
        <Iconify icon="solar:add-linear" width={14} />
      </IconButton>
      <IconButton
        size="small"
        aria-label="Zoom out"
        title="Zoom out"
        onClick={() => zoomOut({ duration: 200 })}
        sx={btnSx}
      >
        <Iconify icon="solar:minus-linear" width={14} />
      </IconButton>
      <IconButton
        size="small"
        aria-label="Fit to view"
        title="Fit to view"
        onClick={() => fitView({ duration: 300, padding: 0.3 })}
        sx={btnSx}
      >
        <Iconify icon="solar:target-linear" width={14} />
      </IconButton>
      <IconButton
        size="small"
        title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
        onClick={onToggleFullscreen}
        sx={btnSx}
      >
        <Iconify
          icon={
            isFullscreen
              ? "solar:quit-full-screen-linear"
              : "solar:full-screen-linear"
          }
          width={14}
        />
      </IconButton>
    </Box>
  );
};

ZoomControls.propTypes = {
  isFullscreen: PropTypes.bool,
  onToggleFullscreen: PropTypes.func,
  revealed: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// AgentGraph component
// ---------------------------------------------------------------------------
// A long graph fitted into a laptop-height box shrinks its labels below
// reading size. With `readableMinZoom` the graph keeps that zoom, starts at
// its top (Start) and pans on scroll.
const READABLE_PAD = 0.2;
// React Flow's zoom floor: its first fit and Fit to view stop here.
const FIT_FLOOR_ZOOM = 0.3;
// The zoom a top-to-bottom graph opens at when its caller sets none and
// React Flow's plain fit would stop at FIT_FLOOR_ZOOM. Such a graph is cut
// at the pane edges and its 12 px labels draw under 4 px. At this zoom they
// draw at 11 px (as in DiffGraph), with Start at the top. Every graph that
// fits at or above the floor keeps React Flow's view.
const DEFAULT_READABLE_ZOOM = 0.92;
// `onlyBelow`: take over only when React Flow's plain fit is below this
// zoom. Without it, below `minZoom` (the graph does not fit at that zoom).
const ReadableViewport = ({ minZoom, fitWidth = false, onlyBelow }) => {
  const initialized = useNodesInitialized();
  const nodeCount = useStore((s) => s.nodes.length);
  const domNode = useStore((s) => s.domNode);
  const { getNodes, getNodesBounds, setViewport } = useReactFlow();
  useEffect(() => {
    if (!initialized || !domNode) return;
    const b = getNodesBounds(getNodes());
    const W = domNode.clientWidth;
    const H = domNode.clientHeight;
    if (!b?.width || !b?.height || !W || !H) return;
    // IT-R5 G4 P1 (additive, `fitWidth` only): fit the bounding-box WIDTH,
    // never below the reading zoom; a tall graph starts at its top and pans.
    if (fitWidth) {
      const z = Math.max(minZoom, Math.min(W / (b.width + 48), 1));
      const tall = b.height * z + 32 > H;
      const tooWide = b.width * z + 32 > W;
      const first =
        getNodes().find((n) => n.data?.type === "start") ?? getNodes()[0];
      const fw = first?.width ?? first?.measured?.width ?? 0;
      const cx = tooWide
        ? (first?.position?.x ?? b.x) + fw / 2
        : b.x + b.width / 2;
      setViewport({
        x: W / 2 - cx * z,
        y: tall ? 16 - b.y * z : H / 2 - (b.y + b.height / 2) * z,
        zoom: z,
      });
      return;
    }
    // The zoom React Flow's plain fit lands on (same padding and cap, no
    // floor), so a graph it fits keeps exactly that view.
    const fit = getViewportForBounds(b, W, H, 0, 1.3, READABLE_PAD).zoom;
    if (fit >= (onlyBelow ?? minZoom)) return;
    // Too big to fit at a reading size: keep the zoom, start at the top and
    // centre the first node (Start) when the graph is wider than the pane,
    // so no node is cut at the left edge first (IT-R1).
    const first =
      getNodes().find((n) => n.data?.type === "start") ?? getNodes()[0];
    const fw = first?.width ?? first?.measured?.width ?? 0;
    const cx =
      b.width * minZoom + 32 <= W
        ? b.x + b.width / 2
        : (first?.position?.x ?? b.x) + fw / 2;
    setViewport({
      x: W / 2 - cx * minZoom,
      y: 16 - b.y * minZoom,
      zoom: minZoom,
    });
  }, [
    initialized,
    domNode,
    nodeCount,
    minZoom,
    fitWidth,
    onlyBelow,
    getNodes,
    getNodesBounds,
    setViewport,
  ]);
  return null;
};

ReadableViewport.propTypes = {
  minZoom: PropTypes.number.isRequired,
  fitWidth: PropTypes.bool,
  onlyBelow: PropTypes.number,
};

const AgentGraphInner = ({
  data,
  isLoading,
  isError,
  pollingPaused,
  direction = "LR",
  onNodeClick,
  isFullscreen = false,
  onToggleFullscreen,
  readableMinZoom: readableMinZoomProp,
  wrapLabels = false,
  fitAll = false,
  fitWidth = false,
  expandLabel,
}) => {
  const theme = useTheme();
  // IT-R1 (additive): `fitAll` = show the whole graph, even below the
  // reading minimum (the user pressed Fit).
  const readableMinZoom = fitAll ? undefined : readableMinZoomProp;
  // A top-to-bottom graph whose caller sets no zoom opens readable only
  // when React Flow's plain fit would stop at its floor (a long trace).
  const readableByDefault =
    readableMinZoomProp == null && !fitAll && direction === "TB";
  const [isHovering, setIsHovering] = useState(false);
  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () =>
      isLoading || isError || !data
        ? { nodes: [], edges: [] }
        : buildFlowData(data, direction, theme, { wrapLabels }),
    [data, direction, isError, isLoading, theme, wrapLabels],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges);

  useEffect(() => {
    setNodes(layoutNodes);
    setEdges(layoutEdges);
  }, [layoutNodes, layoutEdges, setNodes, setEdges]);

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          gap: 1,
          height: 80,
        }}
      >
        <CircularProgress size={20} />
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: 13 }}
        >
          {GRAPH_LOADING_MESSAGE}
        </Typography>
      </Box>
    );
  }

  if (isError) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
          color: "text.disabled",
        }}
      >
        <Typography variant="body2" sx={{ fontSize: 13 }}>
          We couldn&apos;t load the agent graph. Please retry in a moment.
        </Typography>
      </Box>
    );
  }

  if (pollingPaused && !data) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
          color: "text.disabled",
        }}
      >
        <Typography role="status" variant="body2" sx={{ fontSize: 13 }}>
          {AGGREGATION_POLLING_PAUSED_MESSAGE}
        </Typography>
      </Box>
    );
  }

  if (!data?.nodes?.length) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
          color: "text.disabled",
        }}
      >
        <Typography variant="body2" sx={{ fontSize: 13 }}>
          No agent graph data available
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 120,
        width: "100%",
        position: "relative",
        bgcolor: "background.paper",
      }}
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
    >
      {pollingPaused && (
        <Typography
          role="status"
          variant="caption"
          color="text.secondary"
          sx={{
            position: "absolute",
            zIndex: 2,
            top: 4,
            left: "50%",
            transform: "translateX(-50%)",
            bgcolor: "background.paper",
            px: 1,
            borderRadius: "4px",
            whiteSpace: "nowrap",
          }}
        >
          {AGGREGATION_POLLING_PAUSED_MESSAGE}
        </Typography>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        onNodeClick={(_, node) => {
          if (
            node.data?.type !== "start" &&
            node.data?.type !== "end" &&
            onNodeClick
          ) {
            onNodeClick(node.data);
          }
        }}
        fitView
        fitViewOptions={{
          // IT-R2 (additive): a fitted graph (`fitAll`) uses a thin margin,
          // so the labels stay at reading size while everything fits.
          padding: fitAll ? 0.06 : READABLE_PAD,
          maxZoom: 1.3,
          ...(readableMinZoom && !isFullscreen
            ? { minZoom: readableMinZoom }
            : {}),
        }}
        panOnScroll={Boolean(readableMinZoom) && !isFullscreen}
        minZoom={FIT_FLOOR_ZOOM}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        {readableMinZoom && !isFullscreen ? (
          <ReadableViewport minZoom={readableMinZoom} fitWidth={fitWidth} />
        ) : null}
        {readableByDefault && !isFullscreen ? (
          <ReadableViewport
            minZoom={DEFAULT_READABLE_ZOOM}
            onlyBelow={FIT_FLOOR_ZOOM}
          />
        ) : null}
        {/* IT-R1 (additive, inert when absent): an always-visible Expand. */}
        {expandLabel && !isFullscreen && !isHovering ? (
          <Box
            component="button"
            type="button"
            onClick={onToggleFullscreen}
            data-testid="agent-graph-expand"
            sx={{
              position: "absolute",
              top: 8,
              right: 8,
              zIndex: 10,
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              px: 1,
              height: 26,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor: "background.paper",
              color: "text.primary",
              typography: "s3",
              cursor: "pointer",
            }}
          >
            <Iconify icon="mdi:arrow-expand" width={14} />
            {expandLabel}
          </Box>
        ) : null}
        <ZoomControls
          isFullscreen={isFullscreen}
          onToggleFullscreen={onToggleFullscreen}
          revealed={isHovering || isFullscreen}
        />
      </ReactFlow>
    </Box>
  );
};

AgentGraphInner.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  isError: PropTypes.bool,
  pollingPaused: PropTypes.bool,
  direction: PropTypes.oneOf(["LR", "TB"]),
  onNodeClick: PropTypes.func,
  isFullscreen: PropTypes.bool,
  onToggleFullscreen: PropTypes.func,
  readableMinZoom: PropTypes.number,
  wrapLabels: PropTypes.bool,
  fitAll: PropTypes.bool,
  fitWidth: PropTypes.bool,
  expandLabel: PropTypes.string,
};

const AgentGraph = (props) => (
  <FullscreenGraphDialog
    onNodeClick={props.onNodeClick}
    renderGraph={({ isFullscreen, onToggleFullscreen, onNodeClick }) => (
      <ReactFlowProvider>
        <AgentGraphInner
          {...props}
          isFullscreen={isFullscreen}
          onToggleFullscreen={onToggleFullscreen}
          onNodeClick={onNodeClick}
        />
      </ReactFlowProvider>
    )}
  />
);

AgentGraph.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  isError: PropTypes.bool,
  pollingPaused: PropTypes.bool,
  direction: PropTypes.oneOf(["LR", "TB"]),
  onNodeClick: PropTypes.func,
  readableMinZoom: PropTypes.number,
  wrapLabels: PropTypes.bool,
  fitAll: PropTypes.bool,
  fitWidth: PropTypes.bool,
  expandLabel: PropTypes.string,
};

export default AgentGraph;
