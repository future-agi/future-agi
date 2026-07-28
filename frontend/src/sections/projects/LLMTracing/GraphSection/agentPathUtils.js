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

export const MIN_ZOOM = 0.3;
export const MAX_ZOOM = 2;
export const VIEWPORT_H = 260;
export const INITIAL_MIN_ZOOM = 0.8;

export const nodeHeightFor = (node, maxSpans) => {
  if (!maxSpans) return MIN_NODE_H;
  const ratio = Math.min(1, (node.span_count || 0) / maxSpans);
  return Math.round(MIN_NODE_H + ratio * (MAX_NODE_H - MIN_NODE_H));
};

export const computeNaturalSize = (layout) => {
  if (!layout?.columns?.length) return { width: 320, height: 160 };
  const { columns, maxSpans } = layout;
  const width = PAD.left + PAD.right + columns.length * COL_WIDTH;
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
  if (!graphData?.nodes?.length || !graphData?.edges?.length) return null;

  const nodeMap = new Map();
  graphData.nodes.forEach((n) => {
    if (n.type !== "start" && n.type !== "end") nodeMap.set(n.id, { ...n });
  });

  const outEdges = new Map();
  const targetSet = new Set();
  graphData.edges.forEach((e) => {
    if (e.isSelfLoop) return;
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;
    if (!outEdges.has(e.source)) outEdges.set(e.source, []);
    outEdges.get(e.source).push({ target: e.target });
    targetSet.add(e.target);
  });

  // Rank nodes by BFS depth from roots (nodes with no incoming edge).
  const roots = [...nodeMap.keys()].filter((id) => !targetSet.has(id));
  if (roots.length === 0) {
    const sorted = [...nodeMap.values()].sort(
      (a, b) => (b.span_count || 0) - (a.span_count || 0),
    );
    if (sorted.length > 0) roots.push(sorted[0].id);
  }

  const rank = new Map();
  const queue = roots.map((id) => ({ id, r: 0 }));
  const visited = new Set();
  while (queue.length > 0) {
    const { id, r } = queue.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    rank.set(id, Math.max(rank.get(id) || 0, r));
    for (const { target } of outEdges.get(id) || []) {
      if (!visited.has(target)) queue.push({ id: target, r: r + 1 });
    }
  }
  nodeMap.forEach((_, id) => {
    if (!rank.has(id)) rank.set(id, 0);
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
  graphData.edges.forEach((e) => {
    if (e.isSelfLoop) return;
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;
    flows.push({
      source: e.source,
      target: e.target,
      count: e.transitionCount || 1,
      sourceColor: getColor(nodeMap.get(e.source)?.type),
      targetColor: getColor(nodeMap.get(e.target)?.type),
    });
  });

  return { columns: layoutColumns, flows, maxSpans };
};
