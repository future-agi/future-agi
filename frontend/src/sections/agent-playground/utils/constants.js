/** Horizontal px offset when placing a new node to the right of an existing one. */
export const NODE_X_OFFSET = 450;

export const NODE_TYPES = {
  LLM_PROMPT: "llm_prompt",
  AGENT: "agent",
  CONDITIONAL: "conditional",
};

export const AGENT_NODE = {
  id: NODE_TYPES.AGENT,
  title: "Agent Node",
  description: "Run an agent through LLM",
  iconSrc: "/assets/icons/navbar/ic_agents.svg",
  color: "blue.600",
};

export const NODE_TYPE_CONFIG = {
  [NODE_TYPES.LLM_PROMPT]: {
    id: NODE_TYPES.LLM_PROMPT,
    title: "LLM Prompt",
    description: "Run a prompt against an LLM",
    iconSrc: "/assets/icons/ic_chat_single.svg",
    color: "orange.500",
  },
  [NODE_TYPES.AGENT]: AGENT_NODE,
  [NODE_TYPES.CONDITIONAL]: {
    id: NODE_TYPES.CONDITIONAL,
    title: "Conditional",
    description: "Route workflow based on a condition",
    iconSrc: "/assets/icons/ic_branch.svg",
    color: "purple.500",
  },
};

export const AGENT_PLAYGROUND_TABS = [
  {
    id: "build",
    label: "Agent Builder",
    title: "Agent Builder",
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
  },
  {
    id: "changelog",
    label: "Changelog",
    title: "Changelog",
    iconSrc: "/assets/icons/ic_history.svg",
  },
  {
    id: "executions",
    label: "Executions",
    title: "Executions",
    icon: "material-symbols:rocket-launch-outline",
  },
];

// API node types (backend schema)
export const API_NODE_TYPES = {
  ATOMIC: "atomic",
  SUBGRAPH: "subgraph",
};

// Port directions
export const PORT_DIRECTION = {
  INPUT: "input",
  OUTPUT: "output",
};

// Port keys
export const PORT_KEYS = {
  RESPONSE: "response",
  CUSTOM: "custom",
  INPUT: "input",
};

// Edge execution states
export const EDGE_STATE = {
  IDLE: "idle",
  ACTIVE: "active", // Data is flowing through this edge (green animated)
  WAITING: "waiting", // Source done, target not started (gray animated)
  COMPLETED: "completed",
};

// Version statuses
export const VERSION_STATUS = {
  DRAFT: "draft",
  ACTIVE: "active",
  INACTIVE: "inactive",
};

// Prompt Node Form Constants
export const MODEL_CONFIG_DEFAULTS = {
  model: "",
  modelDetail: {
    modelName: "",
    logoUrl: "",
    providers: "",
    isAvailable: false,
  },
  responseFormat: "",
  responseSchema: null,
  toolChoice: "auto",
  tools: [],
};

export const DEFAULT_RESPONSE_FORMAT_OPTIONS = [
  { value: "text", label: "Text" },
  { value: "json", label: "JSON" },
];

export const MODEL_PARAMS_TOOLTIPS = {
  Temperature:
    "Controls randomness: lowering results in less random completions.",
  "Max Tokens": "The maximum number of tokens to generate.",
  "Top P":
    "Controls diversity via nucleus sampling: 0.5 means half of all likelihood-weighted options are considered.",
  "Presence Penalty":
    "How much to penalize new tokens based on whether they appear in the text so far.",
  "Frequency Penalty":
    "How much to penalize new tokens based on their existing frequency in the text so far.",
};
