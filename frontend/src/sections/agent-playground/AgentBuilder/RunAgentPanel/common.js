import { NODE_TYPES } from "../../utils/constants";

const NODE_TYPE_CONFIG = {
  [NODE_TYPES.LLM_PROMPT]: {
    iconSrc: "/assets/icons/ic_chat_single.svg",
    color: "orange.500",
  },
  [NODE_TYPES.AGENT]: {
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
    color: "purple.500",
  },
  [NODE_TYPES.EVAL]: {
    iconSrc: "/assets/icons/ic_check.svg",
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
