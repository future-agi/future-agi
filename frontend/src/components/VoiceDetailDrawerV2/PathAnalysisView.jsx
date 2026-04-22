import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  Collapse,
  IconButton,
  Modal,
  Stack,
  Tooltip,
  Typography,
  alpha,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import {
  Handle,
  Position,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import useKpis from "src/hooks/useKpis";
import GraphView from "src/components/GraphBuilder/GraphView";
import { dagreTransformAndLayout } from "src/components/GraphBuilder/common";
import { enrichTurns, formatClock } from "./transcriptUtils";
import useVoiceAudioStore from "./voiceAudioStore";

// ─────────────────────────────────────────────────────────────────────────────
// Path Analysis — scenario-step checklist + audio links
//
// The original graph view and the coverage heatmap both gave density
// without clarity. This is a linear "test runner" report: every scenario
// step is one self-contained card showing:
//   • pass / fail / partial
//   • expected opener line (from messagePlan.firstMessage)
//   • the actual conversation turn that matched best, with a ▶ play
//     button that seeks the audio player
//   • a one-line "why" explanation when the step was missed
//   • expandable "see full intent" for the step prompt
// ─────────────────────────────────────────────────────────────────────────────

const STOPWORDS = new Set([
  "the",
  "a",
  "an",
  "and",
  "or",
  "but",
  "if",
  "then",
  "to",
  "of",
  "in",
  "on",
  "at",
  "for",
  "with",
  "is",
  "are",
  "was",
  "were",
  "be",
  "been",
  "am",
  "do",
  "does",
  "did",
  "have",
  "has",
  "had",
  "will",
  "would",
  "could",
  "should",
  "may",
  "might",
  "can",
  "cant",
  "dont",
  "isnt",
  "you",
  "your",
  "i",
  "me",
  "my",
  "we",
  "us",
  "our",
  "they",
  "them",
  "he",
  "she",
  "it",
  "this",
  "that",
  "these",
  "those",
  "there",
  "here",
  "what",
  "when",
  "where",
  "who",
  "how",
  "why",
  "so",
  "as",
  "like",
  "just",
  "very",
  "not",
  "no",
  "yes",
  "too",
  "also",
  "only",
  "more",
]);

const tokenize = (text) => {
  if (!text || typeof text !== "string") return [];
  return (text.toLowerCase().match(/\b[a-z][a-z']{1,}\b/g) || []).filter(
    (w) => !STOPWORDS.has(w),
  );
};

// Overlap coefficient = |A ∩ B| / min(|A|, |B|). Asymmetric texts
// (short turn vs long step prompt) match better with this than Jaccard.
const overlapCoefficient = (tokensA, tokensB) => {
  if (!tokensA?.length || !tokensB?.length) return 0;
  const setA = new Set(tokensA);
  const setB = new Set(tokensB);
  let inter = 0;
  setA.forEach((t) => {
    if (setB.has(t)) inter += 1;
  });
  return inter / Math.min(setA.size, setB.size);
};

const stepTargetText = (node) =>
  [node?.messagePlan?.firstMessage, node?.prompt, node?.name]
    .filter(Boolean)
    .join(" ");

// Count meaningful overlapping tokens for the "why" explanation —
// returns an array of up to N shared keywords so we can render them
// as chips in the missed/partial case.
const sharedKeywords = (a, b, limit = 4) => {
  if (!a?.length || !b?.length) return [];
  const setB = new Set(b);
  const out = [];
  const seen = new Set();
  for (const t of a) {
    if (seen.has(t) || !setB.has(t)) continue;
    seen.add(t);
    out.push(t);
    if (out.length >= limit) break;
  }
  return out;
};

const HIT_THRESHOLD = 0.22;
const PARTIAL_THRESHOLD = 0.1;

const getStatus = (score, confirmed) => {
  if (confirmed) return "pass";
  if (score >= HIT_THRESHOLD) return "pass";
  if (score >= PARTIAL_THRESHOLD) return "partial";
  return "miss";
};

const STATUS_META = {
  pass: {
    label: "Addressed",
    color: "success.main",
    bgAlpha: 0.08,
    icon: "mdi:check-circle",
  },
  partial: {
    label: "Partially",
    color: "warning.main",
    bgAlpha: 0.08,
    icon: "mdi:alert-circle-outline",
  },
  miss: {
    label: "Missed",
    color: "error.main",
    bgAlpha: 0.08,
    icon: "mdi:close-circle-outline",
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Main view
// ─────────────────────────────────────────────────────────────────────────────

const PathAnalysisView = ({
  data,
  scenarioId,
  openedExecutionId,
  enabled,
  viewMode = "checklist",
  onRequestTranscript,
}) => {
  const { executionId } = useParams();
  const seekTo = useVoiceAudioStore((s) => s.seekTo);

  // `viewMode` is controlled by the parent (VoiceLeftPanel). Fullscreen
  // remains local so it only matters while the user is on one of the
  // path views.
  const [fullscreen, setFullscreen] = useState(false);
  const [expanded, setExpanded] = useState(() => new Set());
  const [statusFilter, setStatusFilter] = useState("all");
  // Selected step survives tab switches via the URL-less state here —
  // clicking a graph node selects the matching checklist card and vice
  // versa, although cross-tab navigation is now controlled at the
  // parent tab bar level.
  const [selectedStepName, setSelectedStepName] = useState(null);

  const { data: kpis, isLoading: isKpisLoading } = useKpis(executionId, {
    enabled: false,
  });

  const {
    data: flowAnalysis,
    isLoading: isFlowLoading,
    isError: isFlowError,
  } = useQuery({
    queryKey: ["flow-analysis", openedExecutionId],
    queryFn: () =>
      axios.get(endpoints.testExecutions.flowAnalysis(openedExecutionId)),
    enabled: !!enabled && !!openedExecutionId,
    select: (res) => res.data,
  });

  const scenarioGraph = kpis?.scenarioGraphs?.[scenarioId];

  const turns = useMemo(() => {
    const raw = (data?.transcript || []).filter(
      (t) => (t.speakerRole || t.role) !== "system",
    );
    return enrichTurns(raw);
  }, [data]);

  const steps = useMemo(() => {
    const nodes = scenarioGraph?.nodes || [];
    if (!nodes.length) return [];
    const expectedPath = flowAnalysis?.analysis?.expectedPath;
    if (Array.isArray(expectedPath) && expectedPath.length) {
      const byName = new Map(nodes.map((n) => [n.name, n]));
      return expectedPath.map((name) => byName.get(name)).filter(Boolean);
    }
    return nodes;
  }, [scenarioGraph, flowAnalysis]);

  const currentPathNames = useMemo(() => {
    const cp = flowAnalysis?.analysis?.currentPath;
    return Array.isArray(cp) ? new Set(cp) : new Set();
  }, [flowAnalysis]);

  // Per-step analysis — precompute once.
  //
  // Pass/fail source of truth is `flowAnalysis.currentPath` — the same
  // list the Graph view uses — so the two views always agree on which
  // scenario steps were addressed.
  //
  // Turn assignment is done **sequentially and 1:1**: we walk the
  // currentPath in order, and each step claims the best-matching turn
  // that lies AFTER the previous step's assignment. This prevents the
  // bug where two adjacent steps with similar prompts both point at the
  // same turn.
  //
  // Steps that the agent skipped (not in currentPath) get no turn
  // assignment and render as "missed — no matching turn".
  const stepRows = useMemo(() => {
    if (!steps.length || !turns.length) return [];
    const turnTokens = turns.map((t) => tokenize(t.content));
    const currentPathList = Array.isArray(flowAnalysis?.analysis?.currentPath)
      ? flowAnalysis.analysis.currentPath
      : null;

    // Sequential claim: stepName → { idx, score }. Later-occurring
    // duplicates (same step hit twice) overwrite; that's fine for MVP.
    const claims = new Map();
    if (currentPathList?.length) {
      const stepByName = new Map(steps.map((s) => [s.name, s]));
      let cursor = 0;
      for (const name of currentPathList) {
        const step = stepByName.get(name);
        if (!step) continue;
        const target = tokenize(stepTargetText(step));
        let bestIdx = -1;
        let bestScore = 0;
        for (let i = cursor; i < turns.length; i++) {
          const score = overlapCoefficient(target, turnTokens[i]);
          if (score > bestScore) {
            bestScore = score;
            bestIdx = i;
          }
        }
        if (bestIdx >= 0) {
          claims.set(name, { idx: bestIdx, score: bestScore });
          // Advance cursor PAST the claimed turn so the next step picks
          // a later turn — guarantees 1:1, no turn stolen twice.
          cursor = bestIdx + 1;
        }
      }
    }

    return steps.map((step) => {
      const claim = claims.get(step.name);
      const confirmed = currentPathNames.has(step.name);

      // Fallback similarity scan — only for cases where flowAnalysis
      // isn't available (currentPath is null). When flowAnalysis is
      // present, we trust its pass/fail exclusively so the Graph and
      // Checklist always agree.
      let bestIdx = claim?.idx ?? -1;
      let bestScore = claim?.score ?? 0;
      if (!flowAnalysis?.analysis?.currentPath && bestIdx < 0) {
        const target = tokenize(stepTargetText(step));
        turnTokens.forEach((tt, i) => {
          const score = overlapCoefficient(target, tt);
          if (score > bestScore) {
            bestScore = score;
            bestIdx = i;
          }
        });
      }

      const bestTurn = bestIdx >= 0 ? turns[bestIdx] : null;
      // When flowAnalysis is authoritative: `confirmed` alone decides
      // pass vs miss. The similarity score is only cosmetic (shown as
      // "% match") and used for the partial state when there's no
      // authority.
      const status = flowAnalysis?.analysis?.currentPath
        ? confirmed
          ? "pass"
          : "miss"
        : getStatus(bestScore, confirmed);
      const keywords = bestTurn
        ? sharedKeywords(
            tokenize(stepTargetText(step)),
            tokenize(bestTurn.content),
          )
        : [];
      return {
        step,
        bestIdx,
        bestTurn,
        score: bestScore,
        status,
        confirmed,
        keywords,
      };
    });
  }, [steps, turns, flowAnalysis, currentPathNames]);

  const coverage = useMemo(() => {
    if (!stepRows.length) return { pass: 0, partial: 0, miss: 0, total: 0 };
    let pass = 0;
    let partial = 0;
    let miss = 0;
    stepRows.forEach((r) => {
      if (r.status === "pass") pass += 1;
      else if (r.status === "partial") partial += 1;
      else miss += 1;
    });
    return { pass, partial, miss, total: stepRows.length };
  }, [stepRows]);

  const filteredRows = useMemo(() => {
    if (statusFilter === "all") return stepRows;
    return stepRows.filter((r) => r.status === statusFilter);
  }, [stepRows, statusFilter]);

  // ── Divergence — the first index where current and expected disagree ──
  // This powers the one-line breadcrumb above the graph / timeline views.
  const divergence = useMemo(() => {
    const cp = flowAnalysis?.analysis?.currentPath;
    const ep = flowAnalysis?.analysis?.expectedPath;
    if (!Array.isArray(cp) || !Array.isArray(ep)) return null;
    const len = Math.max(cp.length, ep.length);
    for (let i = 0; i < len; i++) {
      if (cp[i] !== ep[i]) {
        return {
          index: i,
          expected: ep[i] || null,
          got: cp[i] || null,
        };
      }
    }
    return null;
  }, [flowAnalysis]);

  // ── Legacy graph view ───────────────────────────────────────────────────
  const { graphNodes, graphEdges } = useMemo(() => {
    const currentPath = flowAnalysis?.analysis?.currentPath;
    const expectedPath = flowAnalysis?.analysis?.expectedPath;
    const newNodes = flowAnalysis?.analysis?.newNodes;
    const newEdges = flowAnalysis?.analysis?.newEdges;
    if (
      !scenarioGraph?.nodes ||
      !Array.isArray(currentPath) ||
      !Array.isArray(expectedPath)
    )
      return { graphNodes: [], graphEdges: [] };
    const greenNodes = [];
    const redNodes = [];
    for (
      let i = 0;
      i < Math.max(currentPath.length, expectedPath.length);
      i++
    ) {
      const c = currentPath?.[i];
      const e = expectedPath?.[i];
      if (c === e) greenNodes.push(c);
      else {
        if (c) redNodes.push(c);
        if (e) greenNodes.push(e);
      }
    }
    const finalNodes = [...scenarioGraph.nodes, ...(newNodes || [])].filter(
      (n) => currentPath.includes(n.name) || expectedPath.includes(n.name),
    );
    const finalEdges = [...scenarioGraph.edges, ...(newEdges || [])].filter(
      (e) =>
        (currentPath.includes(e.from) && currentPath.includes(e.to)) ||
        (expectedPath.includes(e.from) && expectedPath.includes(e.to)),
    );
    const { nodes, edges } = dagreTransformAndLayout(
      finalNodes,
      finalEdges,
      (n) => {
        if (greenNodes.includes(n.name)) return { highlightColor: "success" };
        if (redNodes.includes(n.name)) return { highlightColor: "error" };
        return undefined;
      },
      (e) => {
        if (greenNodes.includes(e.to)) return { highlightColor: "success" };
        if (redNodes.includes(e.to)) return { highlightColor: "error" };
        return undefined;
      },
      // Compact sizes for path analysis — full-size 400×200 nodes make
      // the whole graph zoom out to 10% and the text unreadable.
      { nodeWidth: 200, nodeHeight: 60, ranksep: 80, nodesep: 40 },
    );
    // Force every rendered node through our lightweight custom renderer
    // so the graph shows just the name + a hover tooltip, not a 400px
    // block of prompt text.
    const recoloredNodes = nodes.map((n) => ({
      ...n,
      type: "pathStep",
      data: {
        ...n.data,
        isSelected: n.data?.name === selectedStepName,
      },
    }));
    return { graphNodes: recoloredNodes, graphEdges: edges };
  }, [scenarioGraph, flowAnalysis, selectedStepName]);

  // ── Loading / empty ─────────────────────────────────────────────────────
  const loading = isKpisLoading || isFlowLoading;
  if (loading) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 1,
          minHeight: 240,
        }}
      >
        <CircularProgress size={16} thickness={5} />
        <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
          Analyzing your flow…
        </Typography>
      </Box>
    );
  }
  if (!scenarioGraph || steps.length === 0 || turns.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 220,
          gap: 1,
          p: 2,
        }}
      >
        <Iconify
          icon="mdi:graph-outline"
          width={28}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          sx={{ fontSize: 12, color: "text.secondary", textAlign: "center" }}
        >
          {isFlowError
            ? "This call does not have enough data to analyze its flow."
            : "Path analysis needs a scenario and a transcript to run."}
        </Typography>
      </Box>
    );
  }

  const toggle = (i) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const handleNodeClick = (_evt, node) => {
    // Keep selection local — parent tab bar owns view switching now.
    setSelectedStepName(node?.id || node?.data?.name || null);
    setFullscreen(false);
  };

  const renderGraph = (options = {}) => (
    <Box
      sx={{
        width: "100%",
        height: options.height || 480,
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        overflow: "hidden",
      }}
    >
      <ReactFlowProvider>
        <GraphView
          nodes={graphNodes}
          edges={graphEdges}
          onNodeClick={handleNodeClick}
          nodeTypesOverride={{
            pathStep: PathStepNode,
            conversation: PathStepNode,
            end: PathStepNode,
            transfer: PathStepNode,
            endChat: PathStepNode,
            transferChat: PathStepNode,
          }}
        />
        <ForceFitView deps={[graphNodes, graphEdges, fullscreen, viewMode]} />
        <JumpToNode nodes={graphNodes} selectedName={selectedStepName} />
      </ReactFlowProvider>
    </Box>
  );

  return (
    <Stack gap={1.25} sx={{ minHeight: 0 }}>
      {/* Header — coverage strip + fullscreen button */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
      >
        <CoverageStrip
          coverage={coverage}
          activeFilter={statusFilter}
          onFilter={(f) => setStatusFilter((cur) => (cur === f ? "all" : f))}
        />
        {viewMode === "graph" && (
          <Tooltip title="Open fullscreen" arrow placement="top">
            <IconButton
              size="small"
              onClick={() => setFullscreen(true)}
              sx={{
                width: 24,
                height: 24,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "3px",
              }}
            >
              <Iconify icon="mdi:fullscreen" width={14} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>

      {/* Divergence breadcrumb — the one-line "what happened" summary */}
      {viewMode === "graph" && divergence && (
        <DivergenceBreadcrumb
          divergence={divergence}
          newNodes={flowAnalysis?.analysis?.newNodes || []}
          onJump={(name) => setSelectedStepName(name)}
        />
      )}

      {viewMode === "graph" ? (
        renderGraph()
      ) : (
        <>
          {/* Step list */}
          {filteredRows.length === 0 ? (
            <Box
              sx={{
                py: 3,
                textAlign: "center",
              }}
            >
              <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
                No steps match the current filter
              </Typography>
            </Box>
          ) : (
            <Stack gap={0.75}>
              {filteredRows.map((row, i) => (
                <StepCard
                  key={row.step.name + i}
                  row={row}
                  index={
                    stepRows.indexOf(row) + 1 /* true position, not filtered */
                  }
                  totalSteps={stepRows.length}
                  isExpanded={expanded.has(row.step.name + i)}
                  isSelected={selectedStepName === row.step.name}
                  onToggle={() => toggle(row.step.name + i)}
                  onSelect={() => setSelectedStepName(row.step.name)}
                  onSeek={(sec) => {
                    seekTo?.(sec);
                    // Jump to the Transcript tab so the audio player
                    // mounts (if it isn't already) and the user can
                    // hear the moment they just clicked on.
                    onRequestTranscript?.();
                  }}
                />
              ))}
            </Stack>
          )}
        </>
      )}

      {/* Fullscreen modal — shared by graph + timeline so the user can
          inspect dense scenarios without a squint. Escapes back on
          clicking the × in the top-right or pressing Esc. */}
      <Modal
        open={fullscreen}
        onClose={() => setFullscreen(false)}
        sx={{ display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <Box
          sx={{
            width: "95vw",
            height: "92vh",
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            display: "flex",
            flexDirection: "column",
            outline: "none",
            overflow: "hidden",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{
              px: 1.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
              bgcolor: "background.default",
              flexShrink: 0,
            }}
          >
            <Stack direction="row" alignItems="center" gap={1}>
              <Iconify
                icon="mdi:graph-outline"
                width={14}
                sx={{ color: "primary.main" }}
              />
              <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
                Path Analysis — Graph
              </Typography>
              {divergence && (
                <Box sx={{ ml: 1 }}>
                  <DivergenceBreadcrumb
                    divergence={divergence}
                    newNodes={flowAnalysis?.analysis?.newNodes || []}
                    onJump={(name) => {
                      setSelectedStepName(name);
                      setFullscreen(false);
                    }}
                    inline
                  />
                </Box>
              )}
            </Stack>
            <IconButton
              size="small"
              onClick={() => setFullscreen(false)}
              sx={{ width: 24, height: 24 }}
            >
              <Iconify icon="mdi:close" width={14} />
            </IconButton>
          </Stack>
          <Box sx={{ flex: 1, minHeight: 0, p: 1 }}>
            {renderGraph({ height: "100%" })}
          </Box>
        </Box>
      </Modal>

      {/* Analysis summary — always visible, small */}
      {flowAnalysis?.analysis?.analysisSummary && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            p: 1,
            bgcolor: "background.default",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ mb: 0.25 }}
          >
            <Iconify
              icon="mdi:creation"
              width={12}
              sx={{ color: "primary.main" }}
            />
            <Typography
              sx={{
                fontSize: 10,
                fontWeight: 600,
                color: "text.secondary",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              Falcon analysis
            </Typography>
          </Stack>
          <Typography
            sx={{
              fontSize: 11,
              color: "text.primary",
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
            }}
          >
            {flowAnalysis.analysis.analysisSummary}
          </Typography>
        </Box>
      )}
    </Stack>
  );
};

PathAnalysisView.propTypes = {
  data: PropTypes.object,
  scenarioId: PropTypes.string,
  openedExecutionId: PropTypes.string,
  enabled: PropTypes.bool,
  viewMode: PropTypes.oneOf(["checklist", "graph"]),
  onRequestTranscript: PropTypes.func,
};

// ─────────────────────────────────────────────────────────────────────────────
// Coverage strip — one row of clickable status pills
// ─────────────────────────────────────────────────────────────────────────────

const CoverageStrip = ({ coverage, activeFilter, onFilter }) => {
  const { pass, partial, miss, total } = coverage;
  const pct = total > 0 ? Math.round((pass / total) * 100) : 0;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{ flexWrap: "wrap" }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{
          border: "1px solid",
          borderColor:
            pct >= 80
              ? "success.main"
              : pct >= 50
                ? "warning.main"
                : "error.main",
          borderRadius: "12px",
          px: 0.75,
          py: 0.25,
          bgcolor: (t) =>
            alpha(
              (pct >= 80
                ? t.palette.success
                : pct >= 50
                  ? t.palette.warning
                  : t.palette.error
              ).main,
              0.08,
            ),
        }}
      >
        <Typography
          sx={{
            fontSize: 11,
            fontWeight: 700,
            color:
              pct >= 80
                ? "success.main"
                : pct >= 50
                  ? "warning.main"
                  : "error.main",
            fontFamily: "monospace",
          }}
        >
          {pct}%
        </Typography>
        <Typography
          sx={{
            fontSize: 10,
            color: "text.secondary",
            fontFamily: "monospace",
          }}
        >
          {pass}/{total} steps
        </Typography>
      </Stack>
      <FilterPill
        label={`${pass} pass`}
        color="success.main"
        active={activeFilter === "pass"}
        onClick={() => onFilter("pass")}
      />
      <FilterPill
        label={`${partial} partial`}
        color="warning.main"
        active={activeFilter === "partial"}
        onClick={() => onFilter("partial")}
      />
      <FilterPill
        label={`${miss} missed`}
        color="error.main"
        active={activeFilter === "miss"}
        onClick={() => onFilter("miss")}
      />
    </Stack>
  );
};

CoverageStrip.propTypes = {
  coverage: PropTypes.object,
  activeFilter: PropTypes.string,
  onFilter: PropTypes.func,
};

const FilterPill = ({ label, color, active, onClick }) => (
  <Box
    onClick={onClick}
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.5,
      px: 0.75,
      py: 0.25,
      borderRadius: "12px",
      border: "1px solid",
      borderColor: active ? color : "divider",
      bgcolor: active
        ? (t) => alpha(t.palette.primary.main, 0.04)
        : "transparent",
      cursor: "pointer",
      "&:hover": { borderColor: color },
    }}
  >
    <Box
      sx={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        bgcolor: color,
      }}
    />
    <Typography
      sx={{
        fontSize: 10,
        fontWeight: active ? 700 : 500,
        color: active ? color : "text.secondary",
        fontFamily: "monospace",
      }}
    >
      {label}
    </Typography>
  </Box>
);

FilterPill.propTypes = {
  label: PropTypes.string,
  color: PropTypes.string,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

const ViewToggleButton = ({ active, icon, label, onClick }) => (
  <Box
    onClick={onClick}
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.4,
      px: 0.75,
      py: 0.25,
      borderRadius: "3px",
      border: "1px solid",
      borderColor: active ? "primary.main" : "divider",
      bgcolor: active
        ? (t) => alpha(t.palette.primary.main, 0.08)
        : "transparent",
      cursor: "pointer",
      "&:hover": {
        bgcolor: (t) => alpha(t.palette.primary.main, 0.06),
        borderColor: "primary.main",
      },
    }}
  >
    <Iconify
      icon={icon}
      width={12}
      sx={{ color: active ? "primary.main" : "text.secondary" }}
    />
    <Typography
      sx={{
        fontSize: 10.5,
        fontWeight: active ? 600 : 500,
        color: active ? "primary.main" : "text.secondary",
      }}
    >
      {label}
    </Typography>
  </Box>
);

ViewToggleButton.propTypes = {
  active: PropTypes.bool,
  icon: PropTypes.string,
  label: PropTypes.string,
  onClick: PropTypes.func,
};

// ─────────────────────────────────────────────────────────────────────────────
// Step card — one per scenario step
// ─────────────────────────────────────────────────────────────────────────────

const StepCard = ({
  row,
  index,
  totalSteps,
  isExpanded,
  isSelected,
  onToggle,
  onSelect,
  onSeek,
}) => {
  const { step, bestTurn, score, status, keywords } = row;
  const meta = STATUS_META[status];
  const cardRef = useRef(null);

  // When another view (graph / timeline / breadcrumb) jumps here by
  // setting the selected step, scroll this card into view and expand.
  useEffect(() => {
    if (isSelected && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [isSelected]);

  return (
    <Box
      ref={cardRef}
      sx={{
        border: "1px solid",
        borderColor: isSelected ? "primary.main" : "divider",
        borderLeft: "3px solid",
        borderLeftColor: meta.color,
        borderRadius: "4px",
        bgcolor: "background.paper",
        overflow: "hidden",
        boxShadow: isSelected
          ? (t) => `0 0 0 2px ${alpha(t.palette.primary.main, 0.2)}`
          : "none",
        transition: "border-color 120ms, box-shadow 120ms",
      }}
    >
      {/* Header row — always visible */}
      <Box
        onClick={() => {
          onSelect?.();
          onToggle?.();
        }}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          px: 1,
          py: 0.75,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={meta.icon}
          width={15}
          sx={{ color: meta.color, flexShrink: 0, mt: "2px" }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ mb: 0.25 }}
          >
            <Typography
              sx={{
                fontSize: 9.5,
                fontWeight: 700,
                color: "text.disabled",
                fontFamily: "monospace",
                flexShrink: 0,
              }}
            >
              {String(index).padStart(2, "0")}/
              {String(totalSteps).padStart(2, "0")}
            </Typography>
            <Typography
              sx={{
                fontSize: 12,
                fontWeight: 600,
                color: "text.primary",
                fontFamily: "monospace",
                wordBreak: "break-word",
              }}
            >
              {step.name}
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Box
              sx={{
                display: "inline-flex",
                alignItems: "center",
                px: 0.5,
                borderRadius: "3px",
                border: "1px solid",
                borderColor: meta.color,
                bgcolor: (t) =>
                  alpha(
                    t.palette[
                      status === "pass"
                        ? "success"
                        : status === "partial"
                          ? "warning"
                          : "error"
                    ].main,
                    meta.bgAlpha,
                  ),
                flexShrink: 0,
              }}
            >
              <Typography
                sx={{
                  fontSize: 9,
                  fontWeight: 700,
                  color: meta.color,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  lineHeight: "16px",
                }}
              >
                {meta.label}
              </Typography>
            </Box>
          </Stack>

          {/* Expected opener — inline, one line */}
          {step?.messagePlan?.firstMessage && (
            <Typography
              sx={{
                fontSize: 10.5,
                color: "text.secondary",
                lineHeight: 1.4,
                whiteSpace: isExpanded ? "normal" : "nowrap",
                overflow: isExpanded ? "visible" : "hidden",
                textOverflow: "ellipsis",
                fontStyle: "italic",
              }}
            >
              “{step.messagePlan.firstMessage}”
            </Typography>
          )}
        </Box>
        <Iconify
          icon="mdi:chevron-down"
          width={14}
          sx={{
            color: "text.disabled",
            flexShrink: 0,
            transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 120ms",
          }}
        />
      </Box>

      {/* Matched-turn line — always visible under the header when present */}
      {bestTurn && (
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            gap: 0.75,
            px: 1,
            py: 0.5,
            borderTop: "1px dashed",
            borderColor: "divider",
            bgcolor: (t) =>
              alpha(
                t.palette[
                  status === "miss"
                    ? "error"
                    : status === "partial"
                      ? "warning"
                      : "success"
                ].main,
                0.03,
              ),
          }}
        >
          <Box
            onClick={(e) => {
              e.stopPropagation();
              if (bestTurn.start != null) onSeek?.(bestTurn.start);
            }}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
              px: 0.5,
              py: 0.15,
              borderRadius: "3px",
              border: "1px solid",
              borderColor: "primary.main",
              color: "primary.main",
              cursor: bestTurn.start != null ? "pointer" : "default",
              fontSize: 10,
              fontFamily: "monospace",
              flexShrink: 0,
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.primary.main, 0.08),
              },
            }}
          >
            <Iconify icon="mdi:play" width={10} />
            {bestTurn.start != null ? formatClock(bestTurn.start) : "—"}
          </Box>
          <Typography
            sx={{
              fontSize: 9.5,
              fontWeight: 700,
              color: bestTurn.role === "assistant" ? "primary.main" : "#E9690C",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              flexShrink: 0,
              mt: "2px",
              width: 18,
            }}
          >
            {bestTurn.role === "assistant" ? "A" : "U"}
          </Typography>
          <Typography
            sx={{
              flex: 1,
              minWidth: 0,
              fontSize: 11,
              color: "text.primary",
              lineHeight: 1.4,
              whiteSpace: isExpanded ? "pre-wrap" : "nowrap",
              overflow: isExpanded ? "visible" : "hidden",
              textOverflow: "ellipsis",
              wordBreak: "break-word",
            }}
          >
            {bestTurn.content}
          </Typography>
          <Typography
            sx={{
              fontSize: 9.5,
              color: "text.disabled",
              fontFamily: "monospace",
              flexShrink: 0,
              mt: "2px",
            }}
          >
            {Math.round(score * 100)}%
          </Typography>
        </Box>
      )}

      {!bestTurn && (
        <Box
          sx={{
            px: 1,
            py: 0.5,
            borderTop: "1px dashed",
            borderColor: "divider",
            bgcolor: (t) => alpha(t.palette.error.main, 0.03),
          }}
        >
          <Typography
            sx={{
              fontSize: 10.5,
              color: "text.disabled",
              fontStyle: "italic",
            }}
          >
            No matching turn — the agent never touched this step.
          </Typography>
        </Box>
      )}

      {/* Expanded detail */}
      <Collapse in={isExpanded} unmountOnExit>
        <Box
          sx={{
            px: 1,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.default",
          }}
        >
          {keywords.length > 0 && (
            <Stack
              direction="row"
              alignItems="center"
              gap={0.5}
              sx={{ mb: 0.75, flexWrap: "wrap" }}
            >
              <Typography
                sx={{
                  fontSize: 9,
                  fontWeight: 600,
                  color: "text.disabled",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                Shared keywords
              </Typography>
              {keywords.map((k) => (
                <Box
                  key={k}
                  sx={{
                    px: 0.5,
                    py: "1px",
                    borderRadius: "3px",
                    bgcolor: (t) => alpha(t.palette.primary.main, 0.1),
                    color: "primary.main",
                    fontSize: 9.5,
                    fontWeight: 600,
                    fontFamily: "monospace",
                  }}
                >
                  {k}
                </Box>
              ))}
            </Stack>
          )}
          {step?.prompt && (
            <Box>
              <Typography
                sx={{
                  fontSize: 9,
                  fontWeight: 600,
                  color: "text.disabled",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  mb: 0.25,
                }}
              >
                Step intent
              </Typography>
              <Typography
                sx={{
                  fontSize: 10.5,
                  color: "text.secondary",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  lineHeight: 1.5,
                }}
              >
                {step.prompt}
              </Typography>
            </Box>
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

StepCard.propTypes = {
  row: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  totalSteps: PropTypes.number.isRequired,
  isExpanded: PropTypes.bool,
  isSelected: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  onSelect: PropTypes.func,
  onSeek: PropTypes.func,
};

// ─────────────────────────────────────────────────────────────────────────────
// ForceFitView — sits inside a ReactFlowProvider and calls fitView()
// whenever the nodes / edges / fullscreen state change. Works around the
// default viewport (zoom 0.1) that makes node labels illegible on mount.
// ─────────────────────────────────────────────────────────────────────────────

const ForceFitView = ({ deps }) => {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const handle = setTimeout(() => {
      try {
        fitView({ padding: 0.2, duration: 250, maxZoom: 1.2, minZoom: 0.35 });
      } catch {
        // ReactFlow throws if the container isn't measured yet — safe to ignore.
      }
    }, 80);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return null;
};

ForceFitView.propTypes = { deps: PropTypes.array };

// ─────────────────────────────────────────────────────────────────────────────
// JumpToNode — pans + zooms the graph viewport to the node whose name
// matches `selectedName`. Gives the divergence breadcrumb a visible
// effect in Graph mode; without this, clicking "expected: <stepName>"
// only updates internal state and the viewport never moves.
// ─────────────────────────────────────────────────────────────────────────────

const JumpToNode = ({ nodes, selectedName }) => {
  const { setCenter } = useReactFlow();
  useEffect(() => {
    if (!selectedName || !nodes?.length) return;
    const node = nodes.find(
      (n) => n.id === selectedName || n.data?.name === selectedName,
    );
    if (!node?.position) return;
    const cx = node.position.x + (node.width || 175) / 2;
    const cy = node.position.y + (node.height || 40) / 2;
    try {
      setCenter(cx, cy, { zoom: 1, duration: 400 });
    } catch {
      // ReactFlow throws if the viewport isn't ready — safe to ignore.
    }
  }, [selectedName, nodes, setCenter]);
  return null;
};

JumpToNode.propTypes = {
  nodes: PropTypes.array,
  selectedName: PropTypes.string,
};

// ─────────────────────────────────────────────────────────────────────────────
// Divergence breadcrumb — one-line human-readable "what happened" pinned
// above graph / timeline views. Clicking it jumps to the related step
// in the checklist.
// ─────────────────────────────────────────────────────────────────────────────

const DivergenceBreadcrumb = ({ divergence, newNodes, onJump, inline }) => {
  const { index, expected, got } = divergence;
  const gotNode = (newNodes || []).find((n) => n.name === got);
  const gotQuote = gotNode?.messagePlan?.firstMessage;
  const gotLine = gotQuote
    ? `“${gotQuote.slice(0, 80)}${gotQuote.length > 80 ? "…" : ""}”`
    : null;

  return (
    <Box
      onClick={() => (expected ? onJump?.(expected) : null)}
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 0.75,
        px: inline ? 1 : 1,
        py: inline ? 0.25 : 0.75,
        border: inline ? "none" : "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: inline
          ? "transparent"
          : (t) => alpha(t.palette.error.main, 0.06),
        cursor: expected ? "pointer" : "default",
        "&:hover": expected
          ? {
              bgcolor: inline
                ? (t) => alpha(t.palette.error.main, 0.08)
                : (t) => alpha(t.palette.error.main, 0.1),
            }
          : undefined,
      }}
    >
      <Iconify
        icon="mdi:alert-circle"
        width={14}
        sx={{ color: "error.main", flexShrink: 0, mt: "2px" }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          sx={{ fontSize: 11, color: "text.primary", lineHeight: 1.4 }}
        >
          <Box component="span" sx={{ fontWeight: 700 }}>
            Diverged at step {index + 1}
          </Box>
          {expected && (
            <>
              {" — expected "}
              <Box
                component="span"
                sx={{
                  fontFamily: "monospace",
                  fontWeight: 600,
                  color: "success.main",
                }}
              >
                {expected}
              </Box>
            </>
          )}
          {got && (
            <>
              {", got "}
              <Box
                component="span"
                sx={{
                  fontFamily: "monospace",
                  fontWeight: 600,
                  color: "error.main",
                }}
              >
                {got}
              </Box>
            </>
          )}
        </Typography>
        {gotLine && !inline && (
          <Typography
            sx={{
              fontSize: 10.5,
              color: "text.secondary",
              fontStyle: "italic",
              mt: 0.25,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            Agent said {gotLine}
          </Typography>
        )}
      </Box>
      {expected && !inline && (
        <Iconify
          icon="mdi:arrow-right-thick"
          width={12}
          sx={{ color: "text.disabled", flexShrink: 0, mt: "2px" }}
        />
      )}
    </Box>
  );
};

DivergenceBreadcrumb.propTypes = {
  divergence: PropTypes.object.isRequired,
  newNodes: PropTypes.array,
  onJump: PropTypes.func,
  inline: PropTypes.bool,
};

// ─────────────────────────────────────────────────────────────────────────────
// Lightweight node for the ReactFlow graph view. Shows just the step
// name in a colored pill and pops up a tooltip on hover with the opener
// + intent. Clicking is handled by ReactFlow's onNodeClick at the
// parent level.
// ─────────────────────────────────────────────────────────────────────────────

const PathStepNode = React.memo(({ data }) => {
  const color =
    data?.highlightColor === "success"
      ? "#22c55e"
      : data?.highlightColor === "error"
        ? "#ef4444"
        : "#64748b";
  const opener = data?.messagePlan?.firstMessage;
  const prompt = data?.prompt;

  return (
    <Tooltip
      arrow
      placement="top"
      // Give the tooltip a solid surface (instead of MUI's translucent
      // grey). Works for both dark and light themes: background matches
      // the drawer's paper color, border uses divider, and all three
      // text rows use primary / secondary / primary-ish tones rather
      // than "text.disabled" (which renders as muddy grey on grey in
      // dark mode and is what made the prompt text unreadable). Same
      // fix applies to the voice drawer — `PathStepNode` is shared.
      componentsProps={{
        tooltip: {
          sx: {
            bgcolor: "background.paper",
            color: "text.primary",
            border: "1px solid",
            borderColor: "divider",
            boxShadow: (theme) =>
              theme.palette.mode === "dark"
                ? "0 6px 20px rgba(0,0,0,0.5)"
                : "0 4px 12px rgba(0,0,0,0.12)",
            p: 1,
            maxWidth: 320,
          },
        },
        arrow: {
          sx: {
            color: "background.paper",
            "&::before": {
              border: "1px solid",
              borderColor: "divider",
              backgroundColor: "background.paper",
            },
          },
        },
      }}
      title={
        <Box sx={{ maxWidth: 280 }}>
          <Typography
            sx={{
              fontSize: 11,
              fontWeight: 700,
              color: "text.primary",
            }}
          >
            {data?.name}
          </Typography>
          {opener && (
            <Typography
              sx={{
                fontSize: 10,
                mt: 0.5,
                color: "text.secondary",
                lineHeight: 1.4,
              }}
            >
              “{opener.slice(0, 160)}
              {opener.length > 160 ? "…" : ""}”
            </Typography>
          )}
          {prompt && (
            <Typography
              sx={{
                fontSize: 10,
                mt: 0.5,
                color: "text.secondary",
                fontStyle: "italic",
                lineHeight: 1.4,
              }}
            >
              {prompt.slice(0, 180)}
              {prompt.length > 180 ? "…" : ""}
            </Typography>
          )}
        </Box>
      }
    >
      <Box
        sx={{
          position: "relative",
          minWidth: 150,
          maxWidth: 200,
          px: 1.25,
          py: 0.5,
          borderRadius: "16px",
          border: "2px solid",
          borderColor: color,
          bgcolor: data?.isSelected ? alpha(color, 0.28) : alpha(color, 0.14),
          cursor: "pointer",
          transition: "transform 120ms, box-shadow 120ms",
          boxShadow: data?.isSelected ? `0 0 0 3px ${alpha(color, 0.35)}` : "none",
          "&:hover": {
            transform: "scale(1.04)",
            boxShadow: data?.isSelected
              ? `0 0 0 3px ${alpha(color, 0.45)}, 0 2px 10px ${alpha(color, 0.4)}`
              : `0 2px 10px ${alpha(color, 0.4)}`,
            bgcolor: alpha(color, 0.22),
          },
        }}
      >
        <Handle
          type="target"
          position={Position.Top}
          style={{ background: color, width: 6, height: 6, border: "none" }}
        />
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            fontFamily: "monospace",
            color: "text.primary",
            textAlign: "center",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {data?.name}
        </Typography>
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ background: color, width: 6, height: 6, border: "none" }}
        />
      </Box>
    </Tooltip>
  );
});

PathStepNode.displayName = "PathStepNode";
PathStepNode.propTypes = { data: PropTypes.object };

export default PathAnalysisView;
