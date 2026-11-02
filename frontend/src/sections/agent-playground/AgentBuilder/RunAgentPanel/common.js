import { NODE_TYPES } from "../../utils/constants";

const NODE_TYPE_CONFIG = {
  [NODE_TYPES.LLM_PROMPT]: {
    iconSrc: "/assets/icons/ic_chat_single.svg",
    color: "orange.500",
  },
  agent: {
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
    color: "purple.500",
  },
  eval: {
    iconSrc: "/assets/icons/ic_rounded_square.svg",
    color: "green.600",
  },
  default: {
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
    color: "text.secondary",
  },
};

export const getNodeConfig = (type) => {
  return NODE_TYPE_CONFIG[type] || NODE_TYPE_CONFIG.default;
};

const getNodeExecution = (node) => node?.nodeExecution || node?.node_execution;

const getSubGraph = (node) => node?.subGraph || node?.sub_graph;

const toFiniteNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

export const getNodeDurationMs = (node) => {
  const execution = getNodeExecution(node);
  const durationSeconds = toFiniteNumber(execution?.duration_seconds);
  if (durationSeconds !== null) return durationSeconds * 1000;

  const durationMs = toFiniteNumber(execution?.duration_ms);
  if (durationMs !== null) return durationMs;

  const startedAt = Date.parse(execution?.started_at);
  const completedAt = Date.parse(execution?.completed_at);
  if (Number.isFinite(startedAt) && Number.isFinite(completedAt)) {
    return Math.max(0, completedAt - startedAt);
  }

  return null;
};

const getTreeNodeType = (node) => {
  if (node?.type === "atomic") return NODE_TYPES.LLM_PROMPT;
  if (node?.type === "subgraph") return NODE_TYPES.AGENT;
  return node?.type || NODE_TYPES.LLM_PROMPT;
};

const mapNodeToTreeNode = (node, parentId = null) => {
  const id = parentId ? `${parentId}__${node.id}` : node.id;
  const subGraph = getSubGraph(node);
  const children = (subGraph?.nodes || [])
    .filter((child) => getNodeExecution(child))
    .map((child) => mapNodeToTreeNode(child, id));

  return {
    id,
    type: getTreeNodeType(node),
    name: node?.name || node?.id || "Unnamed node",
    duration: getNodeDurationMs(node),
    ...(getNodeExecution(node)?.total_tokens != null && {
      tokens: getNodeExecution(node).total_tokens,
    }),
    ...(getNodeExecution(node)?.cost != null && {
      cost: toFiniteNumber(getNodeExecution(node).cost),
    }),
    ...(children.length > 0 && { children }),
  };
};

/**
 * Adapt graph execution records to the tree data consumed by NodeOutputListView.
 * Pending graph nodes are intentionally omitted because this is an execution
 * history, not a second representation of the configured graph.
 */
export const mapExecutionNodesToTree = (nodes) =>
  (nodes || [])
    .filter((node) => getNodeExecution(node))
    .map((node) => mapNodeToTreeNode(node));
