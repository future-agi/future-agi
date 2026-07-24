import Dagre from "@dagrejs/dagre";

export const AGENT_GRAPH_NODE_SIZE = {
  default: { width: 140, height: 44 },
  sentinel: { width: 50, height: 32 },
};

const isSentinelNode = (node) =>
  node.data?.type === "start" || node.data?.type === "end";

export const layoutGraph = (nodes, edges, direction = "LR") => {
  const graph = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: direction,
    ranksep: direction === "LR" ? 80 : 50,
    nodesep: 60,
  });

  nodes.forEach((node) => {
    const size = isSentinelNode(node)
      ? AGENT_GRAPH_NODE_SIZE.sentinel
      : AGENT_GRAPH_NODE_SIZE.default;
    graph.setNode(node.id, { ...size });
  });
  edges.forEach((edge) => {
    graph.setEdge(edge.source, edge.target);
  });

  Dagre.layout(graph);

  return nodes.map((node) => {
    const { x, y, width, height } = graph.node(node.id);
    return {
      ...node,
      position: { x: x - width / 2, y: y - height / 2 },
    };
  });
};
