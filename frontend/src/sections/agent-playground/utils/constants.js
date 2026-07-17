/** Horizontal px offset when placing a new node to the right of an existing one. */
export const NODE_X_OFFSET = 450;

export const NODE_TYPES = {
  LLM_PROMPT: "llm_prompt",
  CODE_EXECUTION: "code_execution",
  AGENT: "agent",
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
  [NODE_TYPES.CODE_EXECUTION]: {
    id: NODE_TYPES.CODE_EXECUTION,
    title: "Code Execution",
    description: "Run code in an isolated sandbox",
    iconSrc: "/assets/icons/components/ic_code.svg",
    color: "success.main",
  },
};

export const CODE_EXECUTION_LANGUAGES = [
  { value: "python", label: "Python" },
  { value: "javascript", label: "JavaScript" },
  { value: "typescript", label: "TypeScript" },
];

export const CODE_EXECUTION_DEFAULT_CONFIG = {
  language: "python",
  code: 'result = {"message": "hello from code_execution", "inputs": inputs}',
  timeout_ms: 5000,
  memory_mb: 128,
};

export const CODE_EXECUTION_RESULT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: [
    "ok",
    "value",
    "stdout",
    "stderr",
    "exit_code",
    "duration_ms",
    "error",
    "metadata",
  ],
  properties: {
    ok: { type: "boolean" },
    value: {},
    stdout: { type: "string" },
    stderr: { type: "string" },
    exit_code: { anyOf: [{ type: "integer" }, { type: "null" }] },
    duration_ms: { type: "integer", minimum: 0 },
    error: { anyOf: [{ type: "string" }, { type: "null" }] },
    metadata: {
      type: "object",
      additionalProperties: false,
      required: [
        "language",
        "runner",
        "timed_out",
        "memory_mb",
        "stdout_truncated",
        "stderr_truncated",
        "output_limit_bytes",
      ],
      properties: {
        language: {
          type: "string",
          enum: ["python", "javascript", "typescript"],
        },
        runner: { anyOf: [{ type: "string" }, { type: "null" }] },
        timed_out: { type: "boolean" },
        memory_mb: { anyOf: [{ type: "integer" }, { type: "null" }] },
        stdout_truncated: { type: "boolean" },
        stderr_truncated: { type: "boolean" },
        output_limit_bytes: { anyOf: [{ type: "integer" }, { type: "null" }] },
      },
    },
  },
};

export const CODE_EXECUTION_PORTS = [
  {
    key: "inputs",
    display_name: "inputs",
    direction: "input",
    data_schema: { type: "object" },
    required: false,
  },
  {
    key: "result",
    display_name: "result",
    direction: "output",
    data_schema: CODE_EXECUTION_RESULT_SCHEMA,
    required: true,
  },
];

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
