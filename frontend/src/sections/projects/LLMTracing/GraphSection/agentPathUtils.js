// Layout math + constants for the AgentPath Sankey view.

const TYPE_COLORS = {
  agent: {
    bar: "#c4b5fd",
    band: "#c4b5fd",
    text: "#7c3aed",
    icon: "mdi:robot-outline",
  },
  llm: { bar: "#93c5fd", band: "#93c5fd", text: "#2563eb", icon: "mdi:brain" },
  generation: {
    bar: "#93c5fd",
    band: "#93c5fd",
    text: "#2563eb",
    icon: "mdi:brain",
  },
  tool: {
    bar: "#86efac",
    band: "#86efac",
    text: "#16a34a",
    icon: "mdi:wrench-outline",
  },
  retriever: {
    bar: "#5eead4",
    band: "#5eead4",
    text: "#0d9488",
    icon: "mdi:magnify",
  },
  chain: {
    bar: "#f0abfc",
    band: "#f0abfc",
    text: "#c026d3",
    icon: "mdi:link-variant",
  },
  embedding: {
    bar: "#fdba74",
    band: "#fdba74",
    text: "#ea580c",
    icon: "mdi:vector-square",
  },
  guardrail: {
    bar: "#fca5a5",
    band: "#fca5a5",
    text: "#dc2626",
    icon: "mdi:shield-check-outline",
  },
  reranker: {
    bar: "#fca5a5",
    band: "#fca5a5",
    text: "#dc2626",
    icon: "mdi:sort-variant",
  },
  aggregate: {
    bar: "#a1a1aa",
    band: "#a1a1aa",
    text: "#71717a",
    icon: "mdi:dots-horizontal-circle-outline",
  },
  unknown: {
    bar: "#d1d5db",
    band: "#d1d5db",
    text: "#6b7280",
    icon: "mdi:help-circle-outline",
  },
};

export const getColor = (type) =>
  TYPE_COLORS[type?.toLowerCase()] || TYPE_COLORS.unknown;

const MIN_NODE_H = 24;
const MAX_NODE_H = 60;
export const NODE_GAP = 20;
export const COL_WIDTH = 172;
export const PAD = { top: 12, bottom: 12, left: 20, right: 20 };
export const BAR_WIDTH = 16;
export const LABEL_W = COL_WIDTH - BAR_WIDTH - 28;
export const CYCLE_GUTTER = 72;
const MAX_CYCLE_LANES = 10;

export const MIN_ZOOM = 0.3;
export const MAX_ZOOM = 2;
export const VIEWPORT_H = 260;
export const INITIAL_MIN_ZOOM = 0.8;

export const nodeHeightFor = (node, maxSpans) => {
  if (!maxSpans) return MIN_NODE_H;
  const ratio = Math.min(1, (node.span_count || 0) / maxSpans);
  return Math.round(MIN_NODE_H + ratio * (MAX_NODE_H - MIN_NODE_H));
};

/**
 * Allocate every Sankey band in a constant number of linear passes.
 *
 * The old renderer repeatedly filtered the complete flow list while mapping
 * each flow (and repeated those scans again for every prior flow), turning a
 * dense graph into cubic browser work. Totals and running offsets are scalar
 * maps, so the same deterministic geometry is O(E).
 */
export const computeFlowBands = (flows, nodePositions) => {
  const positioned = (flows || [])
    .map((flow, index) => ({
      flow,
      index,
      src: nodePositions.get(flow.source),
      tgt: nodePositions.get(flow.target),
      weight: Math.max(0, Number(flow.count) || 0),
    }))
    .filter(({ src, tgt }) => src && tgt);

  const sourceTotals = new Map();
  const targetTotals = new Map();
  positioned.forEach(({ flow, weight }) => {
    sourceTotals.set(
      flow.source,
      (sourceTotals.get(flow.source) || 0) + weight,
    );
    targetTotals.set(
      flow.target,
      (targetTotals.get(flow.target) || 0) + weight,
    );
  });

  // A hard minimum makes small transitions visible, but applying it without
  // renormalising lets a wide fork/join allocate hundreds of pixels inside a
  // 60px node. Keep the minimum as a visual hint, then scale every node's
  // provisional bands back to exactly that node's available height. This is
  // still linear in E and guarantees that ribbons cannot overflow labels or
  // overlap the following node in the column.
  const provisional = positioned.map((entry) => {
    const { flow, src, tgt, weight } = entry;
    const sourceTotal = sourceTotals.get(flow.source) || 1;
    const targetTotal = targetTotals.get(flow.target) || 1;
    return {
      ...entry,
      provisionalSourceHeight: Math.max(4, (weight / sourceTotal) * src.h),
      provisionalTargetHeight: Math.max(4, (weight / targetTotal) * tgt.h),
    };
  });

  const sourceBandTotals = new Map();
  const targetBandTotals = new Map();
  provisional.forEach((entry) => {
    const { flow, provisionalSourceHeight, provisionalTargetHeight } = entry;
    sourceBandTotals.set(
      flow.source,
      (sourceBandTotals.get(flow.source) || 0) + provisionalSourceHeight,
    );
    targetBandTotals.set(
      flow.target,
      (targetBandTotals.get(flow.target) || 0) + provisionalTargetHeight,
    );
  });

  const sourceOffsets = new Map();
  const targetOffsets = new Map();
  return provisional.map((entry) => {
    const { flow, src, tgt, provisionalSourceHeight, provisionalTargetHeight } =
      entry;
    const srcBandH =
      provisionalSourceHeight *
      (src.h / (sourceBandTotals.get(flow.source) || src.h || 1));
    const tgtBandH =
      provisionalTargetHeight *
      (tgt.h / (targetBandTotals.get(flow.target) || tgt.h || 1));
    const srcYOffset = sourceOffsets.get(flow.source) || 0;
    const tgtYOffset = targetOffsets.get(flow.target) || 0;

    sourceOffsets.set(flow.source, srcYOffset + srcBandH);
    targetOffsets.set(flow.target, tgtYOffset + tgtBandH);
    return { ...entry, srcBandH, tgtBandH, srcYOffset, tgtYOffset };
  });
};

/**
 * Build one closed Sankey ribbon.
 *
 * Normal DAG edges leave the source bar on the right. Edges inside a strongly
 * connected component occupy the same rank, so drawing them with the normal
 * formula reverses the ribbon through node labels. Same-column/backward edges
 * instead leave from the left and loop through the reserved cycle gutter.
 */
export const buildFlowBandPath = ({
  flow,
  index = 0,
  src,
  tgt,
  srcBandH,
  tgtBandH,
  srcYOffset,
  tgtYOffset,
  barWidth = BAR_WIDTH,
}) => {
  const isCycle = flow?.isCycle === true || tgt.x <= src.x;
  const y0 = src.y + srcYOffset;
  const y1 = tgt.y + tgtYOffset;

  if (isCycle) {
    const x0 = src.x;
    const x1 = tgt.x;
    const lane =
      Math.max(0, Number(flow?.cycleLane ?? index) || 0) % MAX_CYCLE_LANES;
    const loopX = Math.min(x0, x1) - 24 - lane * 7;
    return [
      `M ${x0} ${y0}`,
      `C ${loopX} ${y0}, ${loopX} ${y1}, ${x1} ${y1}`,
      `L ${x1} ${y1 + tgtBandH}`,
      `C ${loopX} ${y1 + tgtBandH}, ${loopX} ${y0 + srcBandH}, ${x0} ${y0 + srcBandH}`,
      "Z",
    ].join(" ");
  }

  const x0 = src.x + barWidth;
  const x1 = tgt.x;
  const cpx = (x0 + x1) / 2;
  return [
    `M ${x0} ${y0}`,
    `C ${cpx} ${y0}, ${cpx} ${y1}, ${x1} ${y1}`,
    `L ${x1} ${y1 + tgtBandH}`,
    `C ${cpx} ${y1 + tgtBandH}, ${cpx} ${y0 + srcBandH}, ${x0} ${y0 + srcBandH}`,
    "Z",
  ].join(" ");
};

export const computeNaturalSize = (layout) => {
  if (!layout?.columns?.length) return { width: 320, height: 160 };
  const { columns, maxSpans } = layout;
  const width =
    PAD.left +
    PAD.right +
    (layout.cycleGutter || 0) +
    columns.length * COL_WIDTH;
  let maxColHeight = 0;
  columns.forEach((col) => {
    const stacked =
      col.nodes.reduce((sum, n) => sum + nodeHeightFor(n, maxSpans), 0) +
      Math.max(0, col.nodes.length - 1) * NODE_GAP;
    maxColHeight = Math.max(maxColHeight, stacked);
  });
  return { width, height: PAD.top + PAD.bottom + maxColHeight };
};

export const computeSankeyLayout = (graphData) => {
  if (!graphData || typeof graphData !== "object") {
    throw new Error("Agent Path data is missing");
  }
  if (!Array.isArray(graphData.nodes)) {
    throw new Error("Agent Path data is missing nodes");
  }
  const graphEdges =
    Array.isArray(graphData.path_edges) && graphData.path_edges.length > 0
      ? graphData.path_edges
      : graphData.edges;
  if (!Array.isArray(graphEdges)) {
    throw new Error("Agent Path data is missing exact topology edges");
  }
  graphData.nodes.forEach((node, index) => {
    if (
      typeof node?.id !== "string" ||
      !node.id ||
      !Number.isFinite(node.span_count) ||
      node.span_count < 0
    ) {
      throw new Error(`Agent Path node #${index} is malformed`);
    }
  });
  graphEdges.forEach((edge, index) => {
    if (
      typeof edge?.source !== "string" ||
      !edge.source ||
      typeof edge.target !== "string" ||
      !edge.target ||
      !Number.isFinite(edge.transition_count) ||
      edge.transition_count < 0
    ) {
      throw new Error(`Agent Path edge #${index} is malformed`);
    }
  });
  if (!graphData.nodes.length) return null;

  const allNodeIds = new Set(graphData.nodes.map((node) => node.id));
  if (allNodeIds.size !== graphData.nodes.length) {
    throw new Error("Agent Path contains duplicate node ids");
  }
  graphEdges.forEach((edge) => {
    if (!allNodeIds.has(edge.source) || !allNodeIds.has(edge.target)) {
      throw new Error(
        `Agent Path edge references an unknown node: ${edge.source}->${edge.target}`,
      );
    }
  });

  // Agent Path is a path-style Sankey presentation. Prefer a producer-recorded
  // execution projection when one exists; current telemetry publishes exact
  // parent-child topology only, so retain the feature by rendering that exact
  // hierarchy without inventing sibling order or chronological transitions.

  const nodeMap = new Map();
  graphData.nodes.forEach((n) => {
    if (n.type !== "start" && n.type !== "end") nodeMap.set(n.id, { ...n });
  });

  const outEdges = new Map([...nodeMap.keys()].map((id) => [id, []]));
  const reverseEdges = new Map([...nodeMap.keys()].map((id) => [id, []]));
  const validEdges = [];
  graphEdges.forEach((e) => {
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;
    outEdges.get(e.source).push(e.target);
    reverseEdges.get(e.target).push(e.source);
    validEdges.push(e);
  });

  // Collapse cycles into strongly-connected components, then assign each
  // component its longest-path rank in the resulting DAG. This preserves
  // convergence (A -> C and A -> B -> C puts C after B), keeps cycles in one
  // column, and remains O(V + E). Both DFS passes are iterative so a very deep
  // agent cannot overflow the browser call stack.
  const visited = new Set();
  const finishOrder = [];
  nodeMap.forEach((_, startId) => {
    if (visited.has(startId)) return;
    visited.add(startId);
    const stack = [{ id: startId, next: 0 }];
    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      const neighbors = outEdges.get(frame.id) || [];
      if (frame.next < neighbors.length) {
        const target = neighbors[frame.next];
        frame.next += 1;
        if (!visited.has(target)) {
          visited.add(target);
          stack.push({ id: target, next: 0 });
        }
      } else {
        finishOrder.push(frame.id);
        stack.pop();
      }
    }
  });

  const componentByNode = new Map();
  let componentCount = 0;
  for (let index = finishOrder.length - 1; index >= 0; index -= 1) {
    const startId = finishOrder[index];
    if (componentByNode.has(startId)) continue;
    const stack = [startId];
    componentByNode.set(startId, componentCount);
    while (stack.length > 0) {
      const id = stack.pop();
      (reverseEdges.get(id) || []).forEach((source) => {
        if (!componentByNode.has(source)) {
          componentByNode.set(source, componentCount);
          stack.push(source);
        }
      });
    }
    componentCount += 1;
  }

  const componentEdges = Array.from(
    { length: componentCount },
    () => new Set(),
  );
  const componentIndegree = Array(componentCount).fill(0);
  validEdges.forEach((edge) => {
    const source = componentByNode.get(edge.source);
    const target = componentByNode.get(edge.target);
    if (source === target || componentEdges[source].has(target)) return;
    componentEdges[source].add(target);
    componentIndegree[target] += 1;
  });

  const componentRank = Array(componentCount).fill(0);
  const queue = [];
  componentIndegree.forEach((degree, component) => {
    if (degree === 0) queue.push(component);
  });
  for (let queueIndex = 0; queueIndex < queue.length; queueIndex += 1) {
    const source = queue[queueIndex];
    componentEdges[source].forEach((target) => {
      componentRank[target] = Math.max(
        componentRank[target],
        componentRank[source] + 1,
      );
      componentIndegree[target] -= 1;
      if (componentIndegree[target] === 0) queue.push(target);
    });
  }

  const rank = new Map();
  nodeMap.forEach((_, id) => {
    rank.set(id, componentRank[componentByNode.get(id)] || 0);
  });

  const columns = new Map();
  rank.forEach((r, id) => {
    if (!columns.has(r)) columns.set(r, []);
    columns.get(r).push(id);
  });

  const sortedRanks = [...columns.keys()].sort((a, b) => a - b);
  sortedRanks.forEach((r) => {
    columns
      .get(r)
      .sort(
        (a, b) =>
          (nodeMap.get(b)?.span_count || 0) - (nodeMap.get(a)?.span_count || 0),
      );
  });

  let maxSpans = 0;
  nodeMap.forEach((n) => {
    maxSpans = Math.max(maxSpans, n.span_count || 0);
  });

  const layoutColumns = sortedRanks.map((r) => ({
    rank: r,
    nodes: columns.get(r).map((id) => ({
      ...nodeMap.get(id),
      id,
      color: getColor(nodeMap.get(id)?.type),
    })),
  }));

  const flows = [];
  let cycleCount = 0;
  graphEdges.forEach((e) => {
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;
    const sourceRank = rank.get(e.source) || 0;
    const targetRank = rank.get(e.target) || 0;
    const isCycle = sourceRank >= targetRank;
    flows.push({
      source: e.source,
      target: e.target,
      count: e.transition_count,
      sourceColor: getColor(nodeMap.get(e.source)?.type),
      targetColor: getColor(nodeMap.get(e.target)?.type),
      sourceRank,
      targetRank,
      isCycle,
      cycleLane: isCycle ? cycleCount++ : 0,
    });
  });

  return {
    columns: layoutColumns,
    flows,
    maxSpans,
    cycleGutter:
      cycleCount > 0
        ? CYCLE_GUTTER + Math.min(cycleCount - 1, MAX_CYCLE_LANES - 1) * 7
        : 0,
  };
};
