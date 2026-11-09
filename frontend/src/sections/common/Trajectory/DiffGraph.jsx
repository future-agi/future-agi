// One run's graph, or the two-run overlay (R4 lane T, D13): Observe's
// AgentGraph over buildTraceGraph (runGraphDiff for the overlay, offered only
// when both runs have a full graph; else side by side with the reason).
import React, {
  Children,
  cloneElement,
  isValidElement,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import { graphOfTree, isStepFlow } from "./flowGraph";
import { runGraphDiff } from "./runGraphDiff";
import { retriesOf } from "./recommendView";
import GraphLegend, { QualityLine } from "./GraphLegend";
import { failingByNode } from "./stepBadges";

// Node labels stay readable at laptop heights (12 px × 0.92 ≥ 11 px); the
// graph pans on scroll from its top (AgentGraph readableMinZoom). IT-R1 G4.
export const READABLE_ZOOM = 0.92;

/** `2.6 s` / `840 ms` for a node's time on that step. */
const fmtDur = (ms) => {
  if (!Number.isFinite(ms) || ms <= 0) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
};

/**
 * IT-R5 G4 P1: a step node's one-line numbers, `×7 · 2.6 s · 1,081 tok`
 * (`×N` = visits; time and tokens summed over the visits).
 */
export function nodeMetaOf(n, { visits: showVisits = true } = {}) {
  if (!n || n.type === "start" || n.type === "end") return null;
  const visits = n.span_count ?? 0;
  const parts = [];
  if (visits > 1 && showVisits) parts.push(`×${visits}`);
  const dur = fmtDur((n.avg_latency_ms ?? 0) * Math.max(1, visits));
  if (dur) parts.push(dur);
  if (n.total_tokens > 0) parts.push(`${n.total_tokens.toLocaleString()} tok`);
  return parts.length ? parts.join(" · ") : null;
}

export const OVERLAY_NEEDS_FULL =
  "Diff overlay needs a full graph on both runs · showing them side by side";

const safeGraph = (tree) => {
  try {
    return Array.isArray(tree) ? graphOfTree(tree) : null;
  } catch {
    return null;
  }
};

/**
 * AgentGraph fits its view once, on mount. When the box it sits in changes
 * size (detail sheet opens, drawer resizes, fullscreen exits) remount it per
 * 48 px size step so the graph fits again instead of showing a cut-off part.
 */
export function GraphBox({ children, minHeight = 160, sx, readable = true }) {
  // IT-R5 G4 P1: every graph in a GraphBox (task compare, drawer, Paths)
  // keeps labels ≥ 11 px: unless the graph sets its own zoom, it fits the
  // bounding-box width at the reading zoom and pans.
  const kids = readable
    ? Children.map(children, (c) =>
        isValidElement(c) &&
        c.props.readableMinZoom === undefined &&
        c.props.fitAll === undefined &&
        typeof c.type !== "string"
          ? cloneElement(c, { readableMinZoom: READABLE_ZOOM, fitWidth: true })
          : c,
      )
    : children;
  const ref = useRef(null);
  const [size, setSize] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      const next = `${Math.round(width / 48)}x${Math.round(height / 48)}`;
      setSize((prev) => (prev === next ? prev : next));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <Box
      ref={ref}
      sx={{ height: "100%", minHeight, position: "relative", ...sx }}
    >
      {size || typeof ResizeObserver === "undefined" ? (
        <Box key={size ?? "static"} sx={{ position: "absolute", inset: 0 }}>
          {kids}
        </Box>
      ) : null}
    </Box>
  );
}
GraphBox.propTypes = {
  children: PropTypes.node,
  minHeight: PropTypes.number,
  sx: PropTypes.object,
  readable: PropTypes.bool,
};

/** Mark selected / failing nodes, edge labels; W1-9: a node's failing checks. */
function decorate(
  graph,
  { selected, failedName, edgeLabels, retries, meta = false, badges = null },
) {
  if (!graph) return graph;
  const loops = new Map((retries ?? []).map((r) => [r.name, r.times]));
  const fails = badges ? failingByNode(badges, graph.nodeToSpanIds) : {};
  return {
    ...graph,
    nodes: graph.nodes.map((n) => ({
      ...n,
      // IT-R5 G4 P1: status ring + `×7 · 2.6 s · 1,081 tok` on every step.
      ...(meta && n.type !== "start" && n.type !== "end"
        ? {
            _meta: nodeMetaOf(n, { visits: !n._visits }) ?? undefined,
            _status: n.error_count > 0 ? "error" : "ok",
          }
        : {}),
      ...(fails[n.id] && !n._subtitle ? { _subtitle: fails[n.id] } : {}),
      ...(loops.get(n.name) > 1 && !n._loops
        ? { _loops: loops.get(n.name) }
        : {}),
      ...(selected && (n.id === selected || n.name === selected)
        ? { _selected: true }
        : {}),
      ...(failedName && n.name === failedName && !("_isFailurePoint" in n)
        ? { _isFailurePoint: true }
        : {}),
    })),
    // IT-R2 G4 P5: `×3` on an edge taken more than once; Edge labels: all.
    edges: graph.edges.map((e) =>
      e.source.startsWith("__") || e.target.startsWith("__")
        ? e
        : edgeLabels || (e.transition_count ?? 1) > 1
          ? { ...e, _label: `×${e.transition_count ?? 1}` }
          : e,
    ),
  };
}

/** One run's graph (Steps · Graph · Timeline → Graph). */
export function SingleGraph({
  tree,
  failedName,
  selected,
  onNodeClick,
  edgeLabels = false,
  height = "100%",
  fitAll = false,
}) {
  const graph = useMemo(() => safeGraph(tree), [tree]);
  const retries = useMemo(() => (tree ? retriesOf(tree) : []), [tree]);
  const data = useMemo(
    () =>
      decorate(graph, {
        selected,
        failedName,
        edgeLabels,
        retries,
        meta: true,
        badges: tree,
      }),
    [graph, selected, failedName, edgeLabels, retries, tree],
  );
  if (!graph?.nodes?.length)
    return (
      <Box sx={{ p: 2 }}>
        <Typography typography="s2" color="text.secondary">
          No steps to draw for this try.
        </Typography>
      </Box>
    );
  return (
    <Box sx={{ height, minHeight: 160 }} data-testid="trajectory-graph">
      <GraphBox>
        <AgentGraph
          data={data}
          isLoading={false}
          direction="TB"
          readableMinZoom={READABLE_ZOOM}
          fitWidth={!fitAll}
          wrapLabels
          fitAll={fitAll}
          onNodeClick={(node) =>
            onNodeClick?.({
              id: node.id,
              name: node.name,
              spanIds: graph.nodeToSpanIds?.[node.id] ?? [],
            })
          }
        />
      </GraphBox>
    </Box>
  );
}
SingleGraph.propTypes = {
  tree: PropTypes.array,
  failedName: PropTypes.string,
  selected: PropTypes.string,
  onNodeClick: PropTypes.func,
  edgeLabels: PropTypes.bool,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  fitAll: PropTypes.bool,
};

/**
 * @param {{trees: {a, b}, qualities: {a, b}, labels: {a, b},
 *   colors: {a, b}, failed?: {a?: string, b?: string}, overlay?: boolean,
 *   selected?: string, onSelectNode?: (node) => void, edgeLabels?: boolean,
 *   height?: number|string, legend?: boolean}} props  `failed` = node name
 *   of each run's First wrong step; onSelectNode gets `{id, name, spanIds:
 *   {a: string[], b: string[]}}`
 */
export default function DiffGraph({
  trees,
  qualities,
  labels,
  colors,
  failed = {},
  overlay = true,
  selected,
  onSelectNode,
  edgeLabels = false,
  height = "100%",
  legend = true,
  fitAll = false,
}) {
  const ga = useMemo(() => safeGraph(trees?.a), [trees?.a]);
  const gb = useMemo(() => safeGraph(trees?.b), [trees?.b]);
  // Step-flow graphs (harness transcripts, calls) overlay too: their nodes
  // are step names, the same in both runs (IT-R1 G4).
  const full = (side) =>
    qualities?.[side]?.quality === "full" || isStepFlow(trees?.[side]);
  const canOverlay = full("a") && full("b");
  const showOverlay = overlay && canOverlay && ga && gb;
  const diff = useMemo(
    () =>
      showOverlay
        ? runGraphDiff(ga, gb, {
            markers: { a: failed.a ?? null, b: failed.b ?? null },
            colors,
          })
        : null,
    [showOverlay, ga, gb, failed.a, failed.b, colors],
  );
  const data = useMemo(
    () => (diff ? decorate(diff, { selected, edgeLabels, meta: true }) : null),
    [diff, selected, edgeLabels],
  );
  const spansOf = (name) => {
    const find = (g) => g?.nodes?.find((n) => n.name === name || n.id === name);
    return {
      a: ga?.nodeToSpanIds?.[find(ga)?.id] ?? [],
      b: gb?.nodeToSpanIds?.[find(gb)?.id] ?? [],
    };
  };

  if (showOverlay)
    return (
      <Stack sx={{ height, minHeight: 0 }} data-testid="diff-graph">
        {legend ? (
          <GraphLegend
            sx={{
              px: 1.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
            quality={qualities.a}
            compareQuality={qualities.b}
            diff={{
              labels,
              colors,
              failed: diff.summary.failed.length > 0,
              retried: diff.summary.loops.length > 0,
            }}
          />
        ) : null}
        <Box sx={{ flex: 1, minHeight: 160 }}>
          <GraphBox>
            <AgentGraph
              data={data}
              isLoading={false}
              direction="TB"
              readableMinZoom={READABLE_ZOOM}
              fitWidth={!fitAll}
              wrapLabels
              fitAll={fitAll}
              onNodeClick={(node) => {
                const key = node._originalId ?? node.id;
                onSelectNode?.({
                  id: key,
                  name: node.name,
                  spanIds: {
                    a: diff.nodeToSpanIds.a[node.id] ?? spansOf(node.name).a,
                    b: diff.nodeToSpanIds.b[node.id] ?? spansOf(node.name).b,
                  },
                });
              }}
            />
          </GraphBox>
        </Box>
      </Stack>
    );

  return (
    <Stack sx={{ height, minHeight: 0 }} data-testid="graph-multiples">
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{
          px: 1.5,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {overlay && !canOverlay
          ? OVERLAY_NEEDS_FULL
          : "Diff overlay off · each run's own graph"}
      </Typography>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
        }}
      >
        {["a", "b"].map((side, i) => (
          <Stack
            key={side}
            sx={{
              minWidth: 0,
              minHeight: 0,
              borderLeft: i ? "1px solid" : 0,
              borderColor: "divider",
            }}
          >
            <Box
              sx={{
                px: 1.5,
                py: 0.5,
                borderBottom: "1px solid",
                borderColor: "divider",
                borderTop: "3px solid",
                borderTopColor: colors?.[side],
              }}
            >
              <QualityLine
                quality={qualities?.[side]}
                prefix={labels?.[side]}
              />
            </Box>
            <Box sx={{ flex: 1, minHeight: 0 }}>
              <SingleGraph
                tree={trees?.[side]}
                failedName={failed[side]}
                selected={selected}
                edgeLabels={edgeLabels}
                fitAll={fitAll}
                onNodeClick={(node) =>
                  onSelectNode?.({
                    id: node.name,
                    name: node.name,
                    spanIds: spansOf(node.name),
                  })
                }
              />
            </Box>
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}
DiffGraph.propTypes = {
  trees: PropTypes.shape({ a: PropTypes.array, b: PropTypes.array }),
  qualities: PropTypes.object,
  labels: PropTypes.object,
  colors: PropTypes.object,
  failed: PropTypes.object,
  overlay: PropTypes.bool,
  selected: PropTypes.string,
  onSelectNode: PropTypes.func,
  edgeLabels: PropTypes.bool,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  legend: PropTypes.bool,
  fitAll: PropTypes.bool,
};
