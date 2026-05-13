import Dagre from "@dagrejs/dagre";
import { EXECUTION_STATUS } from "src/sections/agent-playground/utils/workflowExecution";

const NODE_WIDTH = 220;
const NODE_HEIGHT = 50;
const START_END_WIDTH = 80;
const START_END_HEIGHT = 36;

// Padding around inner nodes inside a subgraph group
const SUBGRAPH_PADDING_X = 30;
const SUBGRAPH_PADDING_TOP = 50; // Extra space for the label header
const SUBGRAPH_PADDING_BOTTOM = 25;

// Dagre layout spacing
const SUBGRAPH_RANK_SEP = 60;
const SUBGRAPH_NODE_SEP = 40;
const MAIN_RANK_SEP = 80;
const MAIN_NODE_SEP = 60;

const START_ID = "__start__";
const END_ID = "__end__";

// Map API node type → frontend node type for icon/color config lookup
const API_TYPE_MAP = {
  atomic: "llm_prompt",
  subgraph: "agent",
};

function getFrontendNodeType(node) {
  const templateName = node.nodeTemplateName || node.node_template_name;
  if (node.type === "atomic" && templateName === "evaluation") {
    return "evaluation";
  }
  return API_TYPE_MAP[node.type] || "llm_prompt";
}

const GREEN_COLOR = "var(--green-500, #22c55e)";

const defaultEdgeStyle = {
  stroke: "var(--border-hover)",
  strokeWidth: 2,
  strokeDasharray: "8 6",
};

/**
 * Compute edge style based on connected nodes' execution statuses.
 */
function getEdgeExecutionStyle(sourceNode, targetNode) {
  const srcStatus = (
    sourceNode?.nodeExecution || sourceNode?.node_execution
  )?.status?.toLowerCase();
  const tgtStatus = (
    targetNode?.nodeExecution || targetNode?.node_execution
  )?.status?.toLowerCase();

  const srcDone = srcStatus === EXECUTION_STATUS.SUCCESS;
  const tgtRunning = tgtStatus === EXECUTION_STATUS.RUNNING;
  const tgtDone = tgtStatus === EXECUTION_STATUS.SUCCESS;

  if (srcDone && tgtRunning) {
    return {
      stroke: GREEN_COLOR,
      strokeWidth: 2,
      strokeDasharray: "8 6",
      animation: "dash-flow 1.5s linear infinite",
    };
  }
  if (srcDone && tgtDone) {
    return {
      stroke: GREEN_COLOR,
      strokeWidth: 2,
      strokeDasharray: "8 6",
    };
  }
  if (srcDone && !tgtStatus) {
    return {
      ...defaultEdgeStyle,
      animation: "dash-flow 1.5s linear infinite",
    };
  }
  return defaultEdgeStyle;
}

/**
 * Convert node_connections to React Flow edges.
 */
function convertNodeConnections(nodeConnections) {
  return (nodeConnections || [])
    .filter(
      (c) =>
        (c.source_node_id || c.sourceNodeId) &&
        (c.target_node_id || c.targetNodeId),
    )
    .map((c) => {
      const source = c.source_node_id || c.sourceNodeId;
      const target = c.target_node_id || c.targetNodeId;
      return {
        id: `${source}-${target}`,
        source,
        target,
        type: "smoothstep",
      };
    });
}

/**
 * Layout a subgraph's inner nodes and return RF-ready nodes, edges, and bounding box.
 *
 * @param {Object} subGraphData - The subGraph object from the API ({ nodes, node_connections })
 * @param {string} parentNodeId - The id of the parent subgraph node
 * @returns {{ innerNodes: Array, innerEdges: Array, width: number, height: number }}
 */
function layoutSubgraph(subGraphData, parentNodeId) {
  const innerApiNodes = subGraphData.nodes || [];
  if (innerApiNodes.length === 0) {
    return {
      innerNodes: [],
      innerEdges: [],
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    };
  }

  const rfEdges = convertNodeConnections(
    subGraphData.node_connections || subGraphData.nodeConnections,
  );

  // Dagre layout for inner nodes
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: "TB",
    ranksep: SUBGRAPH_RANK_SEP,
    nodesep: SUBGRAPH_NODE_SEP,
  });

  for (const node of innerApiNodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of rfEdges) {
    if (edge.source && edge.target) {
      g.setEdge(edge.source, edge.target);
    }
  }

  Dagre.layout(g);

  // Calculate bounding box from dagre positions (dagre gives center coordinates)
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  const rawPositions = innerApiNodes.map((node) => {
    const pos = g.node(node.id);
    const x = pos.x - NODE_WIDTH / 2;
    const y = pos.y - NODE_HEIGHT / 2;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + NODE_WIDTH);
    maxY = Math.max(maxY, y + NODE_HEIGHT);
    return { node, x, y };
  });

  const contentWidth = maxX - minX;
  const contentHeight = maxY - minY;
  const groupWidth = contentWidth + 2 * SUBGRAPH_PADDING_X;
  const groupHeight =
    contentHeight + SUBGRAPH_PADDING_TOP + SUBGRAPH_PADDING_BOTTOM;

  // Build RF nodes with positions relative to group origin.
  // Prefix IDs with parentNodeId to avoid collisions when multiple subgraphs
  // reference the same agent (and thus share the same inner node UUIDs).
  const innerNodes = rawPositions.map(({ node, x, y }) => {
    const exec = node.nodeExecution || node.node_execution;
    return {
      id: `${parentNodeId}__${node.id}`,
      type: "executionNode",
      position: {
        x: x - minX + SUBGRAPH_PADDING_X,
        y: y - minY + SUBGRAPH_PADDING_TOP,
      },
      parentId: parentNodeId,
      extent: "parent",
      data: {
        label: node.name,
        originalId: node.id,
        apiNodeType: node.type,
        frontendNodeType: getFrontendNodeType(node),
        nodeExecution: exec,
        ports: node.ports || [],
        hasSubGraph: !!node.subGraph || !!node.sub_graph,
      },
    };
  });

  const innerEdges = rfEdges.map((edge) => ({
    ...edge,
    id: `${parentNodeId}__${edge.id}`,
    source: `${parentNodeId}__${edge.source}`,
    target: `${parentNodeId}__${edge.target}`,
    style: defaultEdgeStyle,
  }));

  return { innerNodes, innerEdges, width: groupWidth, height: groupHeight };
}

/**
 * Pre-compute subgraph layouts for nodes that have nested subgraphs.
 */
function precomputeSubgraphLayouts(apiNodes) {
  const subgraphLayouts = new Map();
  for (const node of apiNodes) {
    const subGraph = node.subGraph || node.sub_graph;
    if (subGraph?.nodes?.length) {
      subgraphLayouts.set(node.id, layoutSubgraph(subGraph, node.id));
    }
  }
  return subgraphLayouts;
}

/**
 * Run Dagre layout on the main graph and return the graph instance
 * along with the combined edge list (start + inter-node + end).
 */
function layoutMainGraph(executionData, subgraphLayouts) {
  const rfEdges = convertNodeConnections(
    executionData.node_connections || executionData.nodeConnections,
  );

  const targetNodeIds = new Set(rfEdges.map((e) => e.target));
  const sourceNodeIds = new Set(rfEdges.map((e) => e.source));
  const rootNodeIds = executionData.nodes
    .filter((n) => !targetNodeIds.has(n.id))
    .map((n) => n.id);
  const leafNodeIds = executionData.nodes
    .filter((n) => !sourceNodeIds.has(n.id))
    .map((n) => n.id);

  const startEdges = rootNodeIds.map((id) => ({
    id: `start-${id}`,
    source: START_ID,
    target: id,
    type: "smoothstep",
  }));

  const endEdges = leafNodeIds.map((id) => ({
    id: `${id}-end`,
    source: id,
    target: END_ID,
    type: "smoothstep",
  }));

  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", ranksep: MAIN_RANK_SEP, nodesep: MAIN_NODE_SEP });

  g.setNode(START_ID, { width: START_END_WIDTH, height: START_END_HEIGHT });
  g.setNode(END_ID, { width: START_END_WIDTH, height: START_END_HEIGHT });

  for (const node of executionData.nodes) {
    const layout = subgraphLayouts.get(node.id);
    if (layout && layout.innerNodes.length > 0) {
      g.setNode(node.id, { width: layout.width, height: layout.height });
    } else {
      g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }
  }

  const allMainEdges = [...startEdges, ...rfEdges, ...endEdges];
  for (const edge of allMainEdges) {
    if (edge.source && edge.target) {
      g.setEdge(edge.source, edge.target);
    }
  }

  Dagre.layout(g);

  return { g, allMainEdges };
}

/**
 * Assemble React Flow nodes and edges from dagre positions and subgraph layouts.
 */
function assembleRFGraph(executionData, g, allMainEdges, subgraphLayouts) {
  const rfNodes = [];
  const allEdges = [...allMainEdges];

  for (const node of executionData.nodes) {
    const pos = g.node(node.id);
    const exec = node.nodeExecution || node.node_execution;
    const layout = subgraphLayouts.get(node.id);

    if (layout && layout.innerNodes.length > 0) {
      const groupWidth = layout.width;
      const groupHeight = layout.height;

      rfNodes.push({
        id: node.id,
        type: "subgraphGroup",
        position: { x: pos.x - groupWidth / 2, y: pos.y - groupHeight / 2 },
        data: {
          label: node.name,
          apiNodeType: node.type,
          frontendNodeType: getFrontendNodeType(node),
          nodeExecution: exec,
        },
        style: { width: groupWidth, height: groupHeight },
      });

      rfNodes.push(...layout.innerNodes);
      allEdges.push(...layout.innerEdges);
    } else {
      rfNodes.push({
        id: node.id,
        type: "executionNode",
        position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
        data: {
          label: node.name,
          apiNodeType: node.type,
          frontendNodeType: getFrontendNodeType(node),
          nodeExecution: exec,
          ports: node.ports || [],
          hasSubGraph: false,
        },
      });
    }
  }

  // Add start node
  const startPos = g.node(START_ID);
  rfNodes.unshift({
    id: START_ID,
    type: "startEndNode",
    position: {
      x: startPos.x - START_END_WIDTH / 2,
      y: startPos.y - START_END_HEIGHT / 2,
    },
    data: { variant: "start" },
  });

  // Add end node
  const endPos = g.node(END_ID);
  rfNodes.push({
    id: END_ID,
    type: "startEndNode",
    position: {
      x: endPos.x - START_END_WIDTH / 2,
      y: endPos.y - START_END_HEIGHT / 2,
    },
    data: { variant: "end" },
  });

  // Build node lookup for edge state computation
  const nodeMap = new Map(executionData.nodes.map((n) => [n.id, n]));

  const styledEdges = allEdges.map((edge) => {
    // Subgraph inner edges already have their own style
    if (edge.style) return edge;

    const srcNode = nodeMap.get(edge.source);
    const tgtNode = nodeMap.get(edge.target);

    // Start/End are virtual nodes with no execution data — derive state from neighbor
    if (edge.source === START_ID) {
      const tgtStatus = (
        tgtNode?.nodeExecution || tgtNode?.node_execution
      )?.status?.toLowerCase();
      if (tgtStatus === EXECUTION_STATUS.RUNNING) {
        return {
          ...edge,
          style: {
            stroke: GREEN_COLOR,
            strokeWidth: 2,
            strokeDasharray: "8 6",
            animation: "dash-flow 1.5s linear infinite",
          },
        };
      }
      if (
        tgtStatus === EXECUTION_STATUS.SUCCESS ||
        tgtStatus === EXECUTION_STATUS.ERROR ||
        tgtStatus === EXECUTION_STATUS.FAILED
      ) {
        return {
          ...edge,
          style: {
            stroke: GREEN_COLOR,
            strokeWidth: 2,
            strokeDasharray: "8 6",
          },
        };
      }
      return { ...edge, style: defaultEdgeStyle };
    }

    if (edge.target === END_ID) {
      const srcStatus = (
        srcNode?.nodeExecution || srcNode?.node_execution
      )?.status?.toLowerCase();
      if (srcStatus === EXECUTION_STATUS.SUCCESS) {
        return {
          ...edge,
          style: {
            stroke: GREEN_COLOR,
            strokeWidth: 2,
            strokeDasharray: "8 6",
          },
        };
      }
      return { ...edge, style: defaultEdgeStyle };
    }

    return { ...edge, style: getEdgeExecutionStyle(srcNode, tgtNode) };
  });

  return { nodes: rfNodes, edges: styledEdges };
}

/**
 * Build a React Flow graph from execution detail API response.
 * Supports nested subgraph nodes rendered as group containers.
 *
 * @param {Object} executionData - The `result` object from GET /executions/:id/
 * @returns {{ nodes: Array, edges: Array }}
 */
export function buildExecutionGraph(executionData) {
  if (!executionData?.nodes?.length) return { nodes: [], edges: [] };

  const subgraphLayouts = precomputeSubgraphLayouts(executionData.nodes);
  const { g, allMainEdges } = layoutMainGraph(executionData, subgraphLayouts);
  return assembleRFGraph(executionData, g, allMainEdges, subgraphLayouts);
}

export { START_ID, END_ID };
