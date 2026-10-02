/**
 * Adapts the GraphExecutionDetailResponse.nodes array into the
 * `TreeNodeData` shape consumed by `NodeOutputListView` (and the
 * underlying `TreeView` / `CustomTreeNode`).
 *
 * The execution-detail API contract is loose on the node shape, and the
 * run-step list (#2508) needs to be rendered today. We keep this mapping
 * in one place so the panel does not have to know about every quirk of
 * the backend payload.
 *
 * Shape produced:
 *   {
 *     id: string,
 *     type: 'agent' | 'tool' | 'prompt' | 'eval' | string,
 *     name: string,
 *     duration: number,    // milliseconds; 0 when unknown
 *     cost: number,        // USD; 0 when unknown
 *     tokens: number,      // 0 when unknown
 *     children?: TreeNodeData[],   // present for subgraphs
 *   }
 */

const NODE_TYPE_MAP = {
  atomic: "prompt",
  subgraph: "agent",
  prompt: "prompt",
  agent: "agent",
  eval: "eval",
  tool: "tool",
};

/**
 * Read a duration in milliseconds from any of the fields the backend
 * has historically used. We accept the value in milliseconds first, then
 * fall back to a `*_seconds` field. Anything missing or non-numeric
 * resolves to 0 so the row renders `0ms` instead of `NaN`.
 */
function readDurationMs(node) {
  if (!node || typeof node !== "object") return 0;
  const exec = node.nodeExecution || node.node_execution || {};

  const candidates = [
    node.duration_ms,
    node.durationMs,
    node.duration,
    exec.duration_ms,
    exec.durationMs,
    exec.duration,
    node.duration_seconds != null ? node.duration_seconds * 1000 : null,
    exec.duration_seconds != null ? exec.duration_seconds * 1000 : null,
  ];

  for (const value of candidates) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return 0;
}

function readNumber(...candidates) {
  for (const value of candidates) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return 0;
}

function readName(node, id) {
  if (typeof node?.name === "string" && node.name.length > 0) return node.name;
  return id;
}

function mapType(node) {
  const raw = node?.type;
  if (typeof raw === "string" && raw.length > 0) {
    return NODE_TYPE_MAP[raw] || raw;
  }
  // Nodes without an explicit type are treated as the default agent node
  // so the panel still renders an icon and a stable color.
  return "agent";
}

/**
 * Map a single API node to the TreeNodeData shape. Recurses into
 * `subGraph.nodes` when present so subgraphs render as expandable rows
 * whose children are the inner nodes.
 */
function mapNode(node) {
  if (!node || typeof node !== "object") return null;
  const id = typeof node.id === "string" ? node.id : null;
  if (!id) return null;

  const mapped = {
    id,
    type: mapType(node),
    name: readName(node, id),
    duration: readDurationMs(node),
    cost: readNumber(node.cost, node.nodeExecution?.cost, node.node_execution?.cost),
    tokens: readNumber(
      node.tokens,
      node.token_count,
      node.nodeExecution?.tokens,
      node.nodeExecution?.total_tokens,
      node.node_execution?.tokens,
      node.node_execution?.total_tokens,
    ),
  };

  const subGraph = node.subGraph || node.sub_graph;
  if (subGraph && Array.isArray(subGraph.nodes) && subGraph.nodes.length > 0) {
    mapped.children = subGraph.nodes.map(mapNode).filter(Boolean);
  }

  return mapped;
}

/**
 * Convert the raw `executionData.nodes` array from the run-detail
 * response into the shape `NodeOutputListView` expects. Returns an
 * empty array when the input is missing so the list shows its
 * "No nodes to display" empty state rather than crashing.
 *
 * @param {Array|undefined|null} apiNodes
 * @returns {Array}
 */
export function mapExecutionNodesToTreeNodes(apiNodes) {
  if (!Array.isArray(apiNodes)) return [];
  return apiNodes.map(mapNode).filter(Boolean);
}

export const __test__ = { readDurationMs, mapNode, mapExecutionNodesToTreeNodes };