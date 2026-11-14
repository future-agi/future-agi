import { NODE_TYPES } from "../../utils/constants";

const NODE_TYPE_CONFIG = {
  [NODE_TYPES.LLM_PROMPT]: {
    iconSrc: "/assets/icons/ic_chat_single.svg",
    color: "status.warning.fg",
  },
  agent: {
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
    color: "text.secondary",
  },
  eval: {
    iconSrc: "/assets/icons/ic_rounded_square.svg",
    color: "status.success.fg",
  },
  default: {
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
    color: "text.secondary",
  },
};

export const getNodeConfig = (type) => {
  return NODE_TYPE_CONFIG[type] || NODE_TYPE_CONFIG.default;
};
