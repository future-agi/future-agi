// Paths · the aggregate graph of a run (D14) on Observe's
// AgentGraph (node ring red by failing share, `↻ 2×` on self-loops, edge
// labels `18 · all fail` when on) or AgentPath (Sankey). In compare mode
// the two aggregate graphs overlay through runGraphDiff (PathsCompare).
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import AgentPath from "src/sections/projects/LLMTracing/GraphSection/AgentPath";
import { GraphBox } from "src/sections/common/Trajectory/DiffGraph";
import { fmtPct } from "src/sections/common/format/fmt";
import { failingText } from "./stepCompare";

const SENTINEL = new Set(["__start__", "__end__"]);

/** `18 · all fail` / `12 · 25% fail` / `40` (tries through the edge). */
export function edgeLabelOf(edge) {
  const n = edge.trace_count ?? 0;
  if (!n) return null;
  const e = edge.error_count ?? 0;
  if (!e) return String(n);
  if (e === n) return `${n} · all fail`;
  return `${n} · ${fmtPct(e / n, { dp: 0 })} fail`;
}

/** Canonical AgentGraph data + the Paths decorations (inert in Observe). */
export function pathsGraphData(agg, { edgeLabels = false, selected } = {}) {
  const g = agg?.graph;
  if (!g?.nodes?.length) return null;
  const loops = new Map();
  g.edges.forEach((e) => {
    if (e.is_self_loop) loops.set(e.source, e.transition_count);
  });
  return {
    nodes: g.nodes.map((n) => {
      if (SENTINEL.has(n.id)) return n;
      const heat = n.trace_count ? n.error_count / n.trace_count : 0;
      // ↻ = extra visits back to back, shown as the repeat count
      const loop = loops.get(n.id);
      // W1-9 B4: the node's failing checks (tries) under its name.
      const fails = failingText(n);
      return {
        ...n,
        ...(fails ? { _subtitle: fails } : {}),
        ...(heat ? { _heat: heat } : {}),
        ...(loop ? { _loops: loop + 1 } : {}),
        ...(selected && selected === n.id ? { _selected: true } : {}),
      };
    }),
    edges: g.edges
      .filter((e) => !e.is_self_loop)
      .map((e) => {
        const label = edgeLabels ? edgeLabelOf(e) : null;
        return label ? { ...e, _label: label } : e;
      }),
    path_edges: g.path_edges,
  };
}

export default function PathsGraph({
  agg,
  mode = "graph",
  edgeLabels = false,
  selected,
  onNodeClick,
  height = "100%",
}) {
  const data = useMemo(
    () => pathsGraphData(agg, { edgeLabels, selected }),
    [agg, edgeLabels, selected],
  );
  if (!data)
    return (
      <Box sx={{ p: 2 }}>
        <Typography typography="s2" color="text.secondary">
          No paths to draw for this run.
        </Typography>
      </Box>
    );
  return (
    <Box sx={{ height, minHeight: 240 }} data-testid={`paths-${mode}`}>
      {mode === "sankey" ? (
        <AgentPath
          data={agg.graph}
          isLoading={false}
          onNodeClick={(n) => onNodeClick?.(n?.id ?? n?.name)}
        />
      ) : (
        <GraphBox minHeight={240}>
          <AgentGraph
            data={data}
            isLoading={false}
            direction="TB"
            onNodeClick={(n) => onNodeClick?.(n?.id ?? n?.name)}
          />
        </GraphBox>
      )}
    </Box>
  );
}

PathsGraph.propTypes = {
  agg: PropTypes.object,
  mode: PropTypes.oneOf(["graph", "sankey"]),
  edgeLabels: PropTypes.bool,
  selected: PropTypes.string,
  onNodeClick: PropTypes.func,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};
