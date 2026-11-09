import { PromptRoles } from "src/utils/constants";
import { getRandomId } from "src/utils/utils";

export const PROMPT_CONFIG_TYPE = {
  PROMPT: "prompt",
  AGENT: "agent",
  EXTERNAL: "external",
  HTTP: "http",
  // Round 2 (R2-SPEC P3)
  CODE: "code",
  TRACES: "traces",
  HARNESS: "harness",
  // Round 4 (lane Ra): voice runs from the run-source picker.
  VOICE: "voice",
  // options-truth L1 G5: Already running › A2A agent card / MCP server tool.
  A2A: "a2a",
  MCP_TOOL: "mcp_tool",
};

// Run targets offered by `+ Add run` (PRODUCT-SPEC §3.8, R2-SPEC §5.3.1,
// exact copy). Prompt and Agent open their pickers; the rest add a card
// directly. `group`: `future_agi` = Future AGI runs it, `your_side` = it
// runs on your side. Array order = the default menu order.
export const PROMPT_CONFIG_TYPES = [
  {
    label: "Prompt",
    value: PROMPT_CONFIG_TYPE.PROMPT,
    icon: "/assets/icons/navbar/ic_prompt.svg",
    description: "A prompt version × model, tools included.",
    group: "future_agi",
  },
  {
    label: "Agent",
    value: PROMPT_CONFIG_TYPE.AGENT,
    icon: "/assets/icons/navbar/ic_agents.svg",
    description: "A saved Agent Playground graph.",
    group: "future_agi",
  },
  {
    label: "Endpoint",
    value: PROMPT_CONFIG_TYPE.HTTP,
    iconify: "mdi:api",
    description: "We call your endpoint per row.",
    group: "your_side",
  },
];

/** Group headers of the `+ Add run` menu, in order. */
export const RUN_TYPE_GROUPS = [
  { id: "future_agi", label: "Run in Future AGI" },
  { id: "your_side", label: "Run on your side" },
];

// On task suites the agent graph comes first (R2-SPEC §5.3.1). Your code,
// Agent harness, Traces I already have and Upload results are absent (Q12:
// their only real path is the coming SDK/CLI); their type ids stay for the
// seeded runs that used them.
const TASK_SUITE_ORDER = [
  PROMPT_CONFIG_TYPE.AGENT,
  PROMPT_CONFIG_TYPE.PROMPT,
  PROMPT_CONFIG_TYPE.HTTP,
];

/**
 * The `+ Add run` menu as groups: `[{id, label, items:[option]}]`. Task
 * suites (`datasetKind === "task_suite"`) use the task-suite order inside
 * each group. Pure.
 */
export function runTypeGroups(datasetKind) {
  const order =
    datasetKind === "task_suite"
      ? TASK_SUITE_ORDER
      : PROMPT_CONFIG_TYPES.map((o) => o.value);
  const rank = (o) => order.indexOf(o.value);
  return RUN_TYPE_GROUPS.map((g) => ({
    ...g,
    items: PROMPT_CONFIG_TYPES.filter((o) => o.group === g.id).sort(
      (a, b) => rank(a) - rank(b),
    ),
  }));
}

/** Card types whose fields pass through to the API as-is (R2 row 42c). */
export const R2_RUN_TYPES = [
  PROMPT_CONFIG_TYPE.CODE,
  PROMPT_CONFIG_TYPE.TRACES,
  PROMPT_CONFIG_TYPE.HARNESS,
  PROMPT_CONFIG_TYPE.VOICE,
  PROMPT_CONFIG_TYPE.A2A,
  PROMPT_CONFIG_TYPE.MCP_TOOL,
];

export const MAX_RUNS_PER_EXPERIMENT = 12;
export const TRIALS_RANGE = { min: 1, max: 10 };
export const CONCURRENCY_RANGE = { min: 1, max: 50 };
export const DEFAULT_CONCURRENCY = 10;

export const experimentCreationSteps = [
  { id: "configuration", title: "Configuration" },
  { id: "data", title: "Data" },
  { id: "evaluations", title: "Evaluations" },
  { id: "review", title: "Review & run" },
];

export const EXPERIMENT_STEP = {
  CONFIGURATION: 0,
  DATA: 1,
  EVALUATIONS: 2,
  REVIEW: 3,
};

export const experimentTypeOptions = [
  {
    label: "LLM",
    value: "llm",
    subtitle: "Process and generate natural language text",
    icon: "/assets/icons/ic_icon_llm.svg",
  },
  {
    label: "TTS",
    value: "tts",
    subtitle: "Converting text into natural sounding audio",
    icon: "/assets/icons/ic_icon_tts.svg",
  },
  {
    label: "STT",
    value: "stt",
    subtitle: "Transcribe spoken audio into written text",
    icon: "/assets/icons/ic_icon_stt.svg",
  },
  // v2.1 (type:image HIDE): no eval scores images yet, so Image is not a
  // type a new experiment can pick.
];

export const getOutputOptions = {
  llm: [
    { label: "Text", value: "string" },
    { label: "json", value: "json" },
  ],
  stt: [
    { label: "Text", value: "string" },
    { label: "json", value: "json" },
  ],
  tts: [{ label: "Audio", value: "audio" }],
  image: [{ label: "Image", value: "image" }],
};

export const DEFAULT_MESSAGES = [
  {
    id: getRandomId(),
    role: PromptRoles.SYSTEM,
    content: [
      {
        type: "text",
        text: "",
      },
    ],
  },
  {
    id: getRandomId(),
    role: PromptRoles.USER,
    content: [
      {
        type: "text",
        text: "",
      },
    ],
  },
];
