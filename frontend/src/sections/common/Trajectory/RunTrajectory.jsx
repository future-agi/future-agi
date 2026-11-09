// One trajectory UI on Observe's trace components (R4 lane T, R4-SPEC D11):
// `Steps` = TraceTreeV2 with milestone markers, `Graph` = AgentGraph over
// buildTraceGraph (agent-graph runs get their nodes from graphNodes.js on an
// in-memory copy) with the graph quality legend, `Timeline` =
// SpanTreeTimeline. Selecting a span opens SpanDetailPane (right, 45 %,
// resizable; a bottom sheet below 1100 px). Compare mode (`compareTraceId`):
// two trees with mirrored markers, ONE diff graph (full graphs) or side by
// side, two timelines on one time axis. Used by the result drawer, Task
// compare and the Matrix hover card (`compact`). Frozen props + additive.
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import TraceTreeV2 from "src/components/traceDetail/TraceTreeV2";
import SpanTreeTimeline from "src/components/traceDetail/SpanTreeTimeline";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useRunConfigs } from "src/api/develop/r2";
import ErrorState from "src/sections/common/ux/ErrorState";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { useLayoutPrefs } from "src/sections/common/ux/oneview";
import { fmtCost, fmtCount, fmtMs } from "src/sections/common/format/fmt";
import {
  firstDifferenceText,
  toLaneSteps,
} from "src/sections/common/Align/milestones";
import {
  flattenTrace,
  normalizeSteps,
} from "src/sections/common/Align/normalize";
import { alignPairMilestones } from "src/sections/experiment-detail/results/drawer/alignSteps";
import { runColorAt } from "src/sections/common/Runs/runColors";
import useTraceTree, { findEntry } from "./useTraceTree";
import ViewSwitch from "./ViewSwitch";
import GraphLegend from "./GraphLegend";
import DiffGraph, { SingleGraph } from "./DiffGraph";
import NodeSpansSplit from "./NodeSpansSplit";
import {
  captureLabelFor,
  flattenSpans,
  graphQualityFor,
  kindOfRun,
  withAgentGraphNodes,
} from "./graphNodes";
import {
  flowNameOf,
  flowQuality,
  isStepFlow,
  stepFlowKind,
  withStepFlow,
} from "./flowGraph";
import { recommendText, recommendView, retriesOf } from "./recommendView";
import { spanNameOfStep, trajectoryMarkers } from "./trajectoryMarkers";
import StepBadges from "./StepBadges";

const renderStepBadges = (entry, { isRoot }) => (
  <StepBadges entry={entry} isRoot={isRoot} />
);

export const NO_TRACE_REASON = "This run has no traces, so there are no steps.";
const NARROW = 1100;
const COMPACT_H = 240;
const spanOf = (e) => e?.observation_span ?? {};

/** `14 steps · 7 tool calls · 2 errors · 9.8 s · $0.011` from the spans. */
export function trajectorySummary(spanTree, data) {
  if (!spanTree?.length) return null;
  // W1-9 B2: the `orchestration` span is the try's uncovered time, not a
  // step (the record's step count leaves it out too).
  const steps0 = spanTree.map((e) => ({
    ...e,
    children: (e.children ?? []).filter(
      (c) => !(c?.observation_span?.tags ?? []).includes("orchestration"),
    ),
  }));
  const flat = flattenTrace({ observation_spans: steps0 });
  const steps = toLaneSteps(flat);
  const tools = steps.filter(
    (s) => s.kind === "tool" || s.kind === "retrieval",
  ).length;
  const errors = flat.filter((s) => s.error).length;
  const root = spanOf(spanTree[0]);
  const ms = data?.summary?.total_latency_ms ?? root.latency_ms;
  const cost = data?.summary?.total_cost ?? root.cost;
  return [
    fmtCount(steps.length, "step"),
    fmtCount(tools, "tool call"),
    errors ? fmtCount(errors, "error") : null,
    Number.isFinite(ms) ? fmtMs(ms) : null,
    Number.isFinite(cost) ? fmtCost(cost) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

const durationOf = (spanTree) => {
  const t = flattenTrace({ observation_spans: spanTree ?? [] });
  const root = spanOf(spanTree?.[0]);
  if (Number.isFinite(root.latency_ms)) return root.latency_ms;
  const ends = t.map((s) => (s.start ?? 0) + (s.ms ?? 0));
  const starts = t.map((s) => s.start ?? 0);
  return ends.length ? Math.max(...ends) - Math.min(...starts) : 0;
};

/** Container width, live (and its height, IT-R1 G4). */
function useWidth(ref, withHeight = false) {
  const [w, setW] = useState(0);
  const [h, setH] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) => {
      setW(Math.round(e.contentRect.width));
      setH(Math.round(e.contentRect.height));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return withHeight ? [w, h] : w;
}

function TreeSkeleton({ compact }) {
  return (
    <Stack spacing={0.75} sx={{ p: 1.5 }} data-testid="trajectory-loading">
      {Array.from({ length: compact ? 4 : 8 }).map((_, i) => (
        <Stack
          key={i}
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ pl: i ? 2.5 : 0 }}
        >
          <Skeleton variant="circular" width={12} height={12} />
          <Skeleton width={`${45 + ((i * 17) % 35)}%`} height={18} />
        </Stack>
      ))}
    </Stack>
  );
}
TreeSkeleton.propTypes = { compact: PropTypes.bool };

function NoTrace({ reason, compact }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      gap={0.5}
      sx={{
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: 1,
        p: compact ? 1.5 : 3,
        textAlign: "center",
      }}
      data-testid="trajectory-no-trace"
    >
      <Iconify
        icon="mdi:timeline-remove-outline"
        width={22}
        sx={{ color: "text.disabled" }}
      />
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        No trace for this try
      </Typography>
      <Typography typography="s2" color="text.secondary" sx={{ maxWidth: 440 }}>
        {reason ?? NO_TRACE_REASON}
      </Typography>
    </Stack>
  );
}
NoTrace.propTypes = { reason: PropTypes.string, compact: PropTypes.bool };

/** One side's derived data: node-applied tree, quality, markers. */
function useSideModel(q, run, marks, subject) {
  return useMemo(() => {
    if (!q.spans?.length) return null;
    // Harness transcripts and calls draw as a step flow (IT-R1 G4).
    const tree = withStepFlow(withAgentGraphNodes(q.spans, run), run);
    const flow = isStepFlow(tree);
    const quality = flow
      ? flowQuality(graphQualityFor(tree, run), stepFlowKind(tree, run))
      : graphQualityFor(tree, run);
    const retries = retriesOf(tree);
    const m = trajectoryMarkers(tree, marks ?? {}, retries);
    return {
      tree,
      quality,
      capture: captureLabelFor(tree, run),
      rec: recommendView(tree, quality, { subject }),
      marks: m,
      failedName: Number.isInteger(marks?.firstWrongStepN)
        ? (flow ? flowNameOf : (x) => x)(
            spanNameOfStep(tree, marks.firstWrongStepN),
          )
        : null,
      summary: trajectorySummary(tree, q.data),
      duration: durationOf(tree),
    };
  }, [q.spans, q.data, run, marks, subject]);
}

export default function RunTrajectory({
  traceId,
  runId,
  view: viewProp,
  onViewChange,
  compareTraceId,
  compareRunId,
  markers,
  height,
  showDetail = true,
  compact = false,
  run: runProp,
  compareRun: compareRunProp,
  datasetId,
  label,
  compareLabel,
  colors,
  surface = "drawer",
  urlState = !compact,
  keys = !compact,
  emptyReason,
  toolbarEnd,
  initialFocus,
  rootLabels,
  inDrawerTabs = false,
}) {
  const [sp, setSp] = useSearchParams();
  const rootRef = useRef(null);
  const [width, boxHeight] = useWidth(rootRef, true);
  // Stack the span detail under the tree only when there is height for
  // both; a short box (a laptop drawer) splits side by side instead, so the
  // span's input / output are on screen without a nested scroll (IT-R1).
  const narrow =
    width > 0 && width < NARROW && (width < 560 || boxHeight >= 520);
  // IT-R3 G4 P2: below ~700 px two trees side by side are ~250 px each
  // (unreadable); one lane at a time with a lane toggle instead.
  const laneToggle = width > 0 && width < 700;
  const [side, setSide] = useState("a");

  // ---- runs (for graph nodes + quality)
  const needRuns =
    Boolean(datasetId) &&
    (!runProp || Boolean(compareRunId && !compareRunProp));
  const { data: cfg } = useRunConfigs(datasetId, { enabled: needRuns });
  const runOf = (id) =>
    (cfg?.results ?? []).find((r) => r.run_id === id) ?? null;
  const run = runProp ?? (runId ? runOf(runId) : null);
  const compareRun =
    compareRunProp ?? (compareRunId ? runOf(compareRunId) : null);
  const kind = kindOfRun(run) ?? "run";

  const A = useTraceTree(traceId);
  const B = useTraceTree(compareTraceId ?? null);
  const compare = Boolean(compareTraceId);
  const marksA = markers?.a ?? (markers && !markers.b ? markers : null);
  const marksB = markers?.b ?? null;
  const ma = useSideModel(A, run, marksA, compare ? label : undefined);
  const mb = useSideModel(B, compareRun, marksB, compareLabel);
  const runColors = {
    a: colors?.a ?? runColorAt(0).solid,
    b: colors?.b ?? runColorAt(1).solid,
  };
  const labels = { a: label ?? "This run", b: compareLabel ?? "Compared run" };

  // ---- view: URL `tview` > the user's last choice (per run kind) > the recommendation
  const [prefs, patchPrefs] = useLayoutPrefs(`trajectory.${surface}`);
  const options = compact ? ["steps", "graph"] : ["steps", "graph", "timeline"];
  const rec = compare
    ? [ma?.rec, mb?.rec].find((r) => r?.view === "graph") ?? ma?.rec
    : ma?.rec;
  const urlView = urlState ? sp.get("tview") : null;
  const stored = prefs.extras?.[`tview.${kind}`];
  const [localView, setLocalView] = useState(null);
  const pick = (v) => (options.includes(v) ? v : null);
  const view =
    pick(viewProp) ??
    pick(urlView) ??
    pick(localView) ??
    pick(stored) ??
    pick(rec?.view) ??
    "steps";
  const prevView = useRef("steps");
  const setView = useCallback(
    (v) => {
      if (!options.includes(v)) return;
      if (v !== view) prevView.current = view;
      setLocalView(v);
      patchPrefs({ extras: { [`tview.${kind}`]: v } });
      if (urlState)
        setSp(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.set("tview", v);
            return next;
          },
          { replace: true },
        );
      onViewChange?.(v);
    },
    [options, view, patchPrefs, kind, urlState, setSp, onViewChange],
  );

  // ---- selected span (`tspan`), default the first wrong step
  const urlSpan = urlState ? sp.get("tspan") : null;
  const [localSpan, setLocalSpan] = useState(null);
  // No First wrong step marker (prompt runs): the first span that errored,
  // so `Why it failed: wrong or missing arguments` opens on that call.
  const firstErrorSpanId = useMemo(
    () =>
      flattenSpans(ma?.tree ?? [])
        .map(({ span }) => span)
        .find((sp, i) => i > 0 && sp?.status === "ERROR")?.id ?? null,
    [ma?.tree],
  );
  const selected =
    urlSpan ??
    localSpan ??
    ma?.marks.firstWrongSpanId ??
    firstErrorSpanId ??
    null;
  const selectSpan = useCallback(
    (id) => {
      setLocalSpan(id);
      if (urlState)
        setSp(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (id) next.set("tspan", id);
            else next.delete("tspan");
            return next;
          },
          { replace: true },
        );
    },
    [urlState, setSp],
  );
  const [closedDetail, setClosedDetail] = useState(false);
  const entryA = findEntry(ma?.tree, selected);
  const entryB = entryA ? null : findEntry(mb?.tree, selected);
  const entry = entryA ?? entryB;
  const entryTree = entryA ? ma?.tree : mb?.tree;
  // The default selection (First wrong step) opens the detail in Steps and
  // Timeline; the Graph keeps its room until the user picks a node. In the
  // compare graph a node opens both runs' spans (NodeSpansSplit) instead.
  const explicitSpan = Boolean(urlSpan ?? localSpan);
  const detailOpen =
    showDetail &&
    !compact &&
    Boolean(entry) &&
    !closedDetail &&
    !(compare && view === "graph") &&
    (view !== "graph" || explicitSpan);
  // IT-R3 G4 P2: in a narrow box (a laptop drawer) the span detail is an
  // accordion under the tree: a one-line bar until the user picks a step,
  // then half the room; the tree always keeps >= 240 px.
  const entrySide = entryA ? "a" : entryB ? "b" : null;
  const detailBar =
    narrow &&
    showDetail &&
    !compact &&
    Boolean(entry) &&
    !(compare && view === "graph") &&
    // One lane on screen: only its own step's detail.
    (!compare || !laneToggle || entrySide === side);
  const detailExpanded = detailBar && explicitSpan && !closedDetail;
  const barRef = useRef(null);
  // Opening a step brings its detail up under the pinned header.
  useEffect(() => {
    if (detailExpanded)
      barRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [detailExpanded, selected]);

  // ---- graph node split (compare mode)
  const [node, setNode] = useState(null);
  const [edgeLabels] = [Boolean(prefs.extras?.edgeLabels)];

  useShortcuts(
    surface,
    {
      graphView: () => setView(view === "graph" ? prevView.current : "graph"),
      timelineView: compact
        ? undefined
        : () => setView(view === "timeline" ? prevView.current : "timeline"),
    },
    { enabled: keys && Boolean(traceId), register: false },
  );

  const [detailPct, setDetailPct] = useState(45);
  const onDrag = (e) => {
    e.preventDefault();
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const move = (ev) => {
      const pct = ((rect.right - ev.clientX) / rect.width) * 100;
      setDetailPct(Math.min(70, Math.max(28, pct)));
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };

  const [observeOpen, setObserveOpen] = useState(false);
  const firstDiff = useMemo(() => {
    if (!compare || !A.data || !B.data) return null;
    try {
      const sa = normalizeSteps(flattenTrace(A.data), {
        harness: kind === "harness",
      });
      const sb = normalizeSteps(flattenTrace(B.data), {
        harness: kindOfRun(compareRun) === "harness",
      });
      const res = alignPairMilestones(sa, sb, {
        labelA: labels.a,
        labelB: labels.b,
        kindA: kind,
        kindB: kindOfRun(compareRun),
      });
      return res?.markers?.firstDifference
        ? firstDifferenceText(res.markers.firstDifference)
        : null;
    } catch {
      return null;
    }
  }, [compare, A.data, B.data, kind, compareRun, labels.a, labels.b]);

  // ------------------------------------------------------------ states
  const boxH = compact ? COMPACT_H : height;
  if (!traceId) return <NoTrace reason={emptyReason} compact={compact} />;
  if (A.isError)
    return (
      <ErrorState
        thing="this trace"
        error={A.error}
        onRetry={() => A.refetch()}
        requestId={A.error?.response?.headers?.["x-request-id"]}
        compact={compact}
      />
    );
  const loading = A.isLoading || (compare && B.isLoading);

  // ------------------------------------------------------------ body
  const tree = (side, m) => (
    <TraceTreeV2
      spans={m?.tree}
      selectedSpanId={selected}
      onSelectSpan={(id) => {
        setClosedDetail(false);
        selectSpan(id);
      }}
      annotations={m?.marks.annotations}
      highlightSpanIds={m?.marks.highlightSpanIds}
      contextRows={initialFocus === "first-error" ? 2 : undefined}
      neutralLatency
      // IT-R5 G4 S2: the drawer rows carry model, cost and a duration bar;
      // the root names the row and try.
      {...(inDrawerTabs
        ? {
            visibleMetrics: { latency: true, tokens: true, cost: true },
            rowExtras: {
              showModel: true,
              durationBar: true,
              rootLabel: side === "b" ? rootLabels?.b : rootLabels?.a,
              // W1-9 B3: the verdicts that judged each step, failing
              // first, and an LLM step's prompt / completion / cached split.
              renderExtra: renderStepBadges,
            },
          }
        : {})}
    />
  );
  const timeline = (m, axis) => (
    <SpanTreeTimeline
      spans={m?.tree}
      selectedSpanId={selected}
      onSelectSpan={(id) => {
        setClosedDetail(false);
        selectSpan(id);
      }}
      axisRangeMs={axis}
      neutralLatency
    />
  );
  const laneHead = (side, m) => (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{
        px: 1.25,
        py: 0.5,
        borderBottom: "1px solid",
        borderColor: "divider",
        borderTop: "3px solid",
        borderTopColor: runColors[side],
        minWidth: 0,
      }}
    >
      <Typography
        typography="s2"
        fontWeight="fontWeightSemiBold"
        noWrap
        sx={{ flexShrink: 0 }}
      >
        {labels[side]}
      </Typography>
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        sx={{ minWidth: 0, fontVariantNumeric: "tabular-nums" }}
      >
        {m?.summary ?? ""}
      </Typography>
    </Stack>
  );

  // Narrow compare: `[#2G …] [#2B …]` lane toggle (one tree at a time).
  const laneSwitch = (
    <Stack
      direction="row"
      role="tablist"
      aria-label="Which run's trajectory"
      sx={{ borderBottom: "1px solid", borderColor: "divider", minWidth: 0 }}
      data-testid="trajectory-lane-toggle"
    >
      {["a", "b"].map((k) => {
        const m = k === "a" ? ma : mb;
        const on = side === k;
        return (
          <Box
            key={k}
            role="tab"
            tabIndex={0}
            aria-selected={on}
            onClick={() => setSide(k)}
            onKeyDown={(e) =>
              (e.key === "Enter" || e.key === " ") && setSide(k)
            }
            sx={{
              flex: 1,
              minWidth: 0,
              px: 1.25,
              py: 0.5,
              cursor: "pointer",
              borderTop: "3px solid",
              borderTopColor: on ? runColors[k] : "transparent",
              bgcolor: on ? "background.paper" : "action.hover",
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
              },
            }}
          >
            <Typography
              typography="s2"
              fontWeight={on ? "fontWeightSemiBold" : "fontWeightRegular"}
              noWrap
            >
              {labels[k]}
            </Typography>
            <Typography
              typography="s3"
              color="text.secondary"
              noWrap
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {m?.summary ?? "No trace"}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );

  let body;
  if (loading) body = <TreeSkeleton compact={compact} />;
  else if (!ma)
    body = <NoTrace reason="The trace has no spans yet." compact={compact} />;
  else if (!compare) {
    if (view === "graph")
      body = (
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          <GraphLegend
            quality={ma.quality}
            capture={ma.capture}
            sx={{
              px: 1.25,
              py: 0.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          />
          <Box sx={{ flex: 1, minHeight: 0 }}>
            <SingleGraph
              tree={ma.tree}
              failedName={ma.failedName}
              selected={entry ? spanOf(entry).name : null}
              edgeLabels={edgeLabels}
              onNodeClick={(n) => {
                if (!n.spanIds.length) return;
                const i = n.spanIds.indexOf(selected);
                setClosedDetail(false);
                selectSpan(n.spanIds[(i + 1) % n.spanIds.length]);
              }}
            />
          </Box>
        </Stack>
      );
    else if (view === "timeline") body = timeline(ma);
    else body = tree("a", ma);
  } else if (view === "graph") {
    body = (
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Box sx={{ flex: 1, minHeight: 200 }}>
          <DiffGraph
            trees={{ a: ma.tree, b: mb?.tree }}
            qualities={{ a: ma.quality, b: mb?.quality }}
            labels={labels}
            colors={runColors}
            failed={{ a: ma.failedName, b: mb?.failedName }}
            selected={node?.id}
            edgeLabels={edgeLabels}
            onSelectNode={setNode}
          />
        </Box>
        {node ? (
          <NodeSpansSplit
            node={node}
            height={300}
            onClose={() => setNode(null)}
            sides={[
              {
                key: "a",
                label: labels.a,
                color: runColors.a,
                tree: ma.tree,
                spanIds: node.spanIds.a,
              },
              {
                key: "b",
                label: labels.b,
                color: runColors.b,
                tree: mb?.tree,
                spanIds: node.spanIds.b,
              },
            ]}
          />
        ) : null}
      </Stack>
    );
  } else {
    const axis =
      view === "timeline"
        ? { duration: Math.max(ma.duration || 0, mb?.duration || 0) || 1 }
        : undefined;
    const pairs = [
      ["a", ma],
      ["b", mb],
    ];
    body = (
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: laneToggle ? "1fr" : "1fr 1fr",
          height: "100%",
          minHeight: 0,
        }}
      >
        {(laneToggle ? pairs.filter(([k]) => k === side) : pairs).map(
          ([side, m], i) => (
            <Stack
              key={side}
              sx={{
                minWidth: 0,
                minHeight: 0,
                borderLeft: i ? "1px solid" : 0,
                borderColor: "divider",
              }}
            >
              {laneToggle ? laneSwitch : laneHead(side, m)}
              <Box
                sx={{
                  flex: 1,
                  minHeight: 0,
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                }}
              >
                {m ? (
                  view === "timeline" ? (
                    timeline(m, axis)
                  ) : (
                    tree(side, m)
                  )
                ) : (
                  <Box sx={{ p: 2 }}>
                    <NoTrace
                      reason={
                        side === "b"
                          ? `${labels.b} has no trace for this try.`
                          : emptyReason
                      }
                      compact
                    />
                  </Box>
                )}
              </Box>
            </Stack>
          ),
        )}
      </Box>
    );
  }

  const recText = recommendText(rec);
  const header = (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        px: 1.25,
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        minWidth: 0,
      }}
    >
      <ViewSwitch
        value={view}
        onChange={setView}
        options={options}
        recommended={recText}
        variant={inDrawerTabs ? "menu" : "tabs"}
      />
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        sx={{ minWidth: 0, flex: 1, fontVariantNumeric: "tabular-nums" }}
        data-testid="trajectory-summary"
      >
        {compare ? firstDiff ?? "" : ma?.summary ?? ""}
      </Typography>
      {toolbarEnd}
      {!compact ? (
        <CustomTooltip
          show
          title="Open this trace in Observe"
          size="small"
          type="black"
        >
          <Button
            size="small"
            color="inherit"
            onClick={() => setObserveOpen(true)}
            endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
            sx={{ typography: "s2", color: "text.secondary", flexShrink: 0 }}
          >
            Open in Observe
          </Button>
        </CustomTooltip>
      ) : null}
    </Stack>
  );

  const entryName = spanOf(entry).name ?? "this step";
  const accordionBar = detailBar ? (
    <Box
      ref={barRef}
      component="button"
      type="button"
      aria-expanded={detailExpanded}
      data-testid="trajectory-detail-bar"
      onClick={() => {
        if (detailExpanded) setClosedDetail(true);
        else {
          setClosedDetail(false);
          selectSpan(selected);
        }
      }}
      sx={{
        all: "unset",
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        width: "100%",
        px: 1.25,
        height: 34,
        flexShrink: 0,
        scrollMarginTop: "44px",
        cursor: "pointer",
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        typography: "s2",
        "&:hover": { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
      }}
    >
      <Iconify
        icon={detailExpanded ? "mdi:chevron-down" : "mdi:chevron-right"}
        width={16}
      />
      <Typography typography="s2" fontWeight="fontWeightSemiBold" noWrap>
        {entryName}
      </Typography>
      <Typography typography="s3" color="text.secondary" noWrap>
        {detailExpanded
          ? "Hide the step's input and output"
          : "Show the step's input and output"}
      </Typography>
    </Box>
  ) : null;
  const detail = (narrow ? detailExpanded : detailOpen) ? (
    <Box
      sx={{
        ...(narrow
          ? { flex: "0 0 auto", height: 360 }
          : { width: `${detailPct}%`, borderLeft: "1px solid", minHeight: 0 }),
        borderColor: "divider",
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        position: "relative",
        bgcolor: "background.paper",
      }}
      data-testid="trajectory-detail"
    >
      <SpanDetailPane
        entry={entry}
        allSpans={entryTree}
        traceStartTime={spanOf(entryTree?.[0]).start_time}
        isRootSpan={spanOf(entry).id === spanOf(entryTree?.[0]).id}
        traceTags={(entryA ? A.data : B.data)?.trace?.tags || []}
        onClose={() => setClosedDetail(true)}
        onSelectSpan={selectSpan}
        drawerOpen
        chipsCollapsed
      />
    </Box>
  ) : null;

  return (
    <Stack
      ref={rootRef}
      data-testid="run-trajectory"
      data-view={view}
      sx={{
        border: compact ? "1px solid" : 0,
        borderColor: "divider",
        borderRadius: compact ? 1 : 0,
        // IT-R3 G4 P2: a narrow box scrolls (header pinned) rather than
        // squeezing the tree to one row; the tree keeps >= 240 px.
        overflowX: "hidden",
        overflowY: narrow && !compact ? "auto" : "hidden",
        height: boxH ?? "100%",
        minHeight: compact ? COMPACT_H : 0,
        bgcolor: "background.paper",
      }}
    >
      {narrow ? (
        <Box
          sx={{
            position: "sticky",
            top: 0,
            zIndex: 2,
            bgcolor: "background.paper",
            flexShrink: 0,
          }}
        >
          {header}
        </Box>
      ) : (
        header
      )}
      {/* IT-R3 G4 P4: no `Graph recommended` banner; the Graph tab keeps
          its dot and tooltip (ViewSwitch `recommended`). */}
      <Box
        sx={{
          flex: narrow ? "1 0 auto" : 1,
          minHeight: 0,
          display: "flex",
          flexDirection: narrow ? "column" : "row",
        }}
      >
        <Box
          data-testid="trajectory-body"
          sx={{
            flex: narrow ? (detailExpanded ? "0 0 240px" : "1 0 240px") : 1,
            minWidth: 0,
            minHeight: narrow ? 240 : 0,
            overflow: view === "graph" ? "hidden" : "auto",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {body}
        </Box>
        {narrow ? accordionBar : null}
        {detailOpen && !narrow ? (
          <Box
            onMouseDown={onDrag}
            aria-label="Resize the span detail"
            sx={{
              width: 5,
              cursor: "col-resize",
              flexShrink: 0,
              "&:hover": { bgcolor: "action.hover" },
            }}
          />
        ) : null}
        {detail}
      </Box>
      {observeOpen ? (
        <TraceDetailDrawerV2
          traceId={entryB ? compareTraceId : traceId}
          open
          onClose={() => setObserveOpen(false)}
        />
      ) : null}
    </Stack>
  );
}

RunTrajectory.propTypes = {
  traceId: PropTypes.string,
  runId: PropTypes.string,
  rowId: PropTypes.string,
  trial: PropTypes.number,
  view: PropTypes.oneOf(["steps", "graph", "timeline"]),
  onViewChange: PropTypes.func,
  compareTraceId: PropTypes.string,
  compareRunId: PropTypes.string,
  // `{a?: {firstWrongStepN, failedChecks}, b?: {…}}` (or one side's marks)
  markers: PropTypes.object,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  showDetail: PropTypes.bool,
  compact: PropTypes.bool,
  // additive (lane T)
  run: PropTypes.object,
  compareRun: PropTypes.object,
  datasetId: PropTypes.string,
  label: PropTypes.string,
  compareLabel: PropTypes.string,
  colors: PropTypes.shape({ a: PropTypes.string, b: PropTypes.string }),
  surface: PropTypes.string,
  // IT-R5 G4 S2 (additive): the tree roots' names `{a, b}` (`Row T13 ·
  // try 3 of 3`).
  rootLabels: PropTypes.object,
  // IT-R5 G4 S2 (additive): inside the result drawer's tabs: the view
  // switch is one `Steps ▾` button and tree rows carry model, cost and a
  // duration bar.
  inDrawerTabs: PropTypes.bool,
  urlState: PropTypes.bool,
  keys: PropTypes.bool,
  emptyReason: PropTypes.string,
  toolbarEnd: PropTypes.node,
  // IT-R3 G4 P2 (additive): `first-error` selects the first wrong / errored
  // span and scrolls it into view with 2 steps of context above it.
  initialFocus: PropTypes.oneOf(["first-error"]),
};
