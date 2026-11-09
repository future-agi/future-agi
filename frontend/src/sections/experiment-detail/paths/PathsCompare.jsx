// Paths · compare overlay (R4 lane T, D13/D14): the two runs' aggregate
// graphs on one AgentGraph through runGraphDiff (A = `Compare with`, drawn
// as the baseline; B = the run on screen). Ring colours are the run
// colours; A-only steps are dashed ghosts; nodes show `a× · b×` visits.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import { GraphBox } from "src/sections/common/Trajectory/DiffGraph";
import GraphLegend from "src/sections/common/Trajectory/GraphLegend";
import { runGraphDiff } from "src/sections/common/Trajectory/runGraphDiff";
import { aggregateGraph } from "src/sections/common/Trajectory/pathModel";
import { edgeLabelOf } from "./PathsGraph";

/** Overlay data: A = compare run (baseline side), B = this run. */
export function pathsOverlay(agg, compareAgg, { colors, edgeLabels } = {}) {
  if (!agg?.graph?.nodes?.length || !compareAgg?.graph?.nodes?.length)
    return null;
  const strip = (g) => ({
    ...g,
    edges: g.edges.filter((e) => !e.is_self_loop),
  });
  try {
    const diff = runGraphDiff(
      strip(aggregateGraph(compareAgg)),
      strip(aggregateGraph(agg)),
      { colors: { a: colors?.b, b: colors?.a } },
    );
    if (!edgeLabels) return diff;
    const byKey = new Map(
      (agg.graph.edges ?? []).map((e) => [`${e.source}->${e.target}`, e]),
    );
    return {
      ...diff,
      edges: diff.edges.map((e) => {
        const src = byKey.get(
          `${e._originalSource ?? e.source}->${e._originalTarget ?? e.target}`,
        );
        const label = src ? edgeLabelOf(src) : null;
        return label ? { ...e, _label: label } : e;
      }),
    };
  } catch {
    return null;
  }
}

export default function PathsCompare({
  agg,
  compareAgg,
  labels,
  colors,
  edgeLabels = false,
  onNodeClick,
  names,
}) {
  const data = useMemo(
    () => pathsOverlay(agg, compareAgg, { colors, edgeLabels }),
    [agg, compareAgg, colors, edgeLabels],
  );
  if (!data) return null;
  return (
    <Stack sx={{ height: "100%", minHeight: 0 }} data-testid="paths-overlay">
      <GraphLegend
        sx={{
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
        diff={{
          labels: {
            a: names?.b ?? labels.b,
            b: names?.a ?? labels.a,
          },
          colors: { a: colors.b, b: colors.a },
          failed: false,
          retried: data.summary?.loops?.length > 0,
        }}
      />
      <Box sx={{ flex: 1, minHeight: 240 }}>
        <GraphBox minHeight={240}>
          <AgentGraph
            data={data}
            isLoading={false}
            direction="TB"
            onNodeClick={(n) => onNodeClick?.(n?._originalId ?? n?.id)}
          />
        </GraphBox>
      </Box>
    </Stack>
  );
}

PathsCompare.propTypes = {
  agg: PropTypes.object,
  compareAgg: PropTypes.object,
  labels: PropTypes.object,
  colors: PropTypes.object,
  edgeLabels: PropTypes.bool,
  onNodeClick: PropTypes.func,
  /** iterate r1 G3 (P4): `{a, b}` names for the legend (id + name). */
  names: PropTypes.object,
};
