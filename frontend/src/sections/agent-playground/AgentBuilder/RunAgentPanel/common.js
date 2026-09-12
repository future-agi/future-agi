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

const getExecution = (node) => node?.nodeExecution || node?.node_execution;

export const buildNodeOutputTree = (nodes = [], parentId = null) =>
  nodes.reduce((result, node) => {
    const execution = getExecution(node);
    const id = parentId ? `${parentId}__${node.id}` : node.id;
    const children = buildNodeOutputTree(node?.subGraph?.nodes || [], id);

    if (!execution && children.length === 0) return result;

    result.push({
      id,
      name: execution?.node_name || node.name || "Unnamed node",
      type: execution?.node_type || (node?.subGraph ? "agent" : node.type),
      ...(execution?.duration_seconds != null && {
        duration: execution.duration_seconds * 1000,
      }),
      ...(execution?.cost != null && { cost: execution.cost }),
      ...(execution?.tokens != null && { tokens: execution.tokens }),
      children,
    });
    return result;
  }, []);
