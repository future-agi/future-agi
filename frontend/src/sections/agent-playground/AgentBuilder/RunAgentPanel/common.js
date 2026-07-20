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
  [NODE_TYPES.HTTP_REQUEST]: {
    iconSrc: "/assets/icons/ic_agent_flow.svg",
    color: "blue.500",
  },
  default: {
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
    color: "text.secondary",
  },
};

export const getNodeConfig = (type) => {
  return NODE_TYPE_CONFIG[type] || NODE_TYPE_CONFIG.default;
};
