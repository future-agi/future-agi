/* eslint-disable no-restricted-syntax -- ds: data colours (span-type encodings) */
/**
 * Shared span type configuration — icons, colors, labels.
 * Used across TraceTreeV2, SpanDetailPane, SpanTreeTimeline, LogViewRow.
 */

export const TYPE_CONFIG = {
  agent: {
    color: "#7857FC",
    icon: "/icons/tracedetails/agent_icon.svg",
    label: "Agent",
    mdiIcon: "solar:bot-linear",
  },
  chain: {
    color: "#EC4899",
    icon: "/icons/tracedetails/chain_icon.svg",
    label: "Chain",
    mdiIcon: "solar:link-linear",
  },
  llm: {
    color: "#2563EB",
    icon: "/icons/tracedetails/llm_icon.svg",
    label: "LLM",
    mdiIcon: "solar:brain-linear",
  },
  generation: {
    color: "#2563EB",
    icon: "/icons/tracedetails/llm_icon.svg",
    label: "Generation",
    mdiIcon: "solar:brain-linear",
  },
  tool: {
    color: "#F59E0B",
    icon: "/icons/tracedetails/tool_icon.svg",
    label: "Tool",
    mdiIcon: "solar:toolbox-linear",
  },
  retriever: {
    color: "#0D9488",
    icon: "/icons/tracedetails/retriever_icon.svg",
    label: "Retriever",
    mdiIcon: "solar:database-linear",
  },
  embedding: {
    color: "#6366F1",
    icon: "/icons/tracedetails/embedding_icon.svg",
    label: "Embedding",
    mdiIcon: "solar:graph-linear",
  },
  guardrail: {
    color: "#EF4444",
    icon: "/icons/tracedetails/guardrail_icon.svg",
    label: "Guardrail",
    mdiIcon: "solar:shield-warning-linear",
  },
  evaluator: {
    color: "#6366F1",
    icon: "/icons/tracedetails/evaluator_icon.svg",
    label: "Evaluator",
    mdiIcon: "solar:check-circle-linear",
  },
  conversation: {
    color: "#7857FC",
    icon: "/icons/tracedetails/conversation_icon.svg",
    label: "Conversation",
    mdiIcon: "solar:chat-round-line-linear",
  },
  reranker: {
    color: "#65A30D",
    icon: "/icons/tracedetails/reranker_icon.svg",
    label: "Reranker",
    mdiIcon: "solar:sort-from-top-to-bottom-linear",
  },
  internal: {
    color: "#94A3B8",
    icon: "/icons/tracedetails/unknown_icon.svg",
    label: "Internal",
    mdiIcon: "solar:clock-circle-linear",
  },
  unknown: {
    color: "#94A3B8",
    icon: "/icons/tracedetails/unknown_icon.svg",
    label: "Unknown",
    mdiIcon: "solar:question-circle-linear",
  },
};

export const getTypeConfig = (type) =>
  TYPE_CONFIG[(type || "").toLowerCase()] || TYPE_CONFIG.unknown;

// Skill and context loads (SKILL-TRACES §3.1, §7.2) are ordinary spans with
// no observation type of their own. They get a glyph by span name, never a
// new type, so type filters and counts are unchanged.
export const SPAN_NAME_CONFIG = {
  "skill.load": {
    color: "#0D9488",
    label: "Skill load",
    mdiIcon: "solar:book-bookmark-linear",
  },
  "context.load": {
    color: "#0D9488",
    label: "Context load",
    mdiIcon: "solar:book-2-linear",
  },
};

/**
 * Type config for one span: the name-based override for `skill.load` and
 * `context.load`, else the observation type's. The label stays the type's,
 * so anything that groups by type still reads the real type.
 */
export const getSpanTypeConfig = (type, name) => {
  const base = getTypeConfig(type);
  const override = SPAN_NAME_CONFIG[name];
  return override ? { ...base, color: override.color, mdiIcon: override.mdiIcon, nameLabel: override.label } : base;
};

/**
 * LLM provider icons — shown next to model name.
 */
export const PROVIDER_ICONS = {
  openai: { icon: "simple-icons:openai", color: "text.primary" },
  anthropic: { icon: "simple-icons:anthropic", color: "#D97706" },
  google: { icon: "mdi:google", color: "#4285F4" },
  azure: { icon: "mdi:microsoft-azure", color: "#0078D4" },
  cohere: { icon: "mdi:alpha-c-circle", color: "#39594D" },
  mistral: { icon: "mdi:alpha-m-circle", color: "#F54E42" },
  groq: { icon: "solar:bolt-linear", color: "#F55036" },
  bedrock: { icon: "mdi:aws", color: "#FF9900" },
  meta: { icon: "simple-icons:meta", color: "#0668E1" },
  deepseek: { icon: "solar:brain-linear", color: "#0A84FF" },
};

export const getProviderIcon = (provider) => {
  if (!provider) return null;
  const key = provider.toLowerCase().replace(/[^a-z]/g, "");
  return PROVIDER_ICONS[key] || null;
};
