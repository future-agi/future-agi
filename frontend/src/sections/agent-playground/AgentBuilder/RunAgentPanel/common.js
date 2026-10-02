import { NODE_TYPES } from "../../utils/constants";

const API_TYPE_MAP = {
  atomic: NODE_TYPES.LLM_PROMPT,
  subgraph: "agent",
};

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
  return (
    NODE_TYPE_CONFIG[type] ||
    NODE_TYPE_CONFIG[API_TYPE_MAP[type]] ||
    NODE_TYPE_CONFIG.default
  );
};

/**
 * Extracts duration in milliseconds from a node's execution payload.
 */
export const getNodeDurationMs = (node) => {
  const exec = node?.nodeExecution || node?.node_execution;
  if (exec?.duration_seconds != null) {
    return Number(exec.duration_seconds) * 1000;
  }
  if (exec?.duration != null) {
    return Number(exec.duration);
  }
  if (node?.duration != null) {
    return Number(node.duration);
  }
  if (exec?.started_at && exec?.completed_at) {
    const start = new Date(exec.started_at).getTime();
    const end = new Date(exec.completed_at).getTime();
    if (!isNaN(start) && !isNaN(end) && end >= start) {
      return end - start;
    }
  }
  return 0;
};

/**
 * Transforms executionData.nodes into TreeNodeData hierarchy for NodeOutputListView.
 */
export const mapExecutionNodesToTree = (executionData) => {
  const rawNodes = executionData?.nodes;
  if (!rawNodes?.length) return [];

  return rawNodes
    .filter((node) => node.id !== "__start__" && node.id !== "__end__")
    .map((node) => {
      const duration = getNodeDurationMs(node);
      const subNodes = (node.subGraph || node.sub_graph)?.nodes;
      const children = subNodes?.length
        ? subNodes
            .filter((c) => c.id !== "__start__" && c.id !== "__end__")
            .map((child) => ({
              id: `${node.id}__${child.id}`,
              name: child.name,
              type: child.type,
              duration: getNodeDurationMs(child),
              cost: child.cost ?? 0,
              tokens: child.tokens ?? 0,
            }))
        : undefined;

      return {
        id: node.id,
        name: node.name,
        type: node.type,
        duration,
        cost: node.cost ?? 0,
        tokens: node.tokens ?? 0,
        children,
      };
    });
};

