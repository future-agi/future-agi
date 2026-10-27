// Shared constants for the revamped chat detail drawer.

// Placeholder bubble widths for the transcript skeleton loader.
export const SKELETON_BUBBLE_WIDTHS = [220, 160, 260, 180, 240];

// System turns are filtered out of the rendered chat transcript.
export const SYSTEM_SPEAKER_ROLE = "system";

// Chat-tailored Imagine prompts — same shape as `VOICE_IMAGINE_PROMPTS` in
// VoiceDetailDrawerV2. Surfaced as suggested-prompt chips in an unsaved
// Imagine tab.
export const CHAT_IMAGINE_PROMPTS = [
  { label: "Summarize this chat", icon: "mdi:text-box-outline" },
  { label: "Show the conversation flow", icon: "mdi:message-text-outline" },
  {
    label: "Where did the user get frustrated?",
    icon: "mdi:emoticon-sad-outline",
  },
  { label: "What's the cost breakdown?", icon: "mdi:currency-usd" },
  { label: "Compare tone across turns", icon: "mdi:chart-line" },
  { label: "Evaluate chat quality", icon: "mdi:checkbox-marked-circle-outline" },
];

// Drawer module identifiers (which surface the drawer is rendered in).
export const DRAWER_MODULE = { OBSERVE: "project", SIMULATE: "simulate" };

// Query keys invalidated after a tag edit so every detail view refetches.
export const TAG_INVALIDATION_QUERY_KEYS = [
  ["chatCallDetail"],
  ["voiceCallDetail"],
  ["trace-detail"],
];

// User-facing fields included in the chat JSON download. Intentionally a
// curated allowlist so we don't leak internal trace/span internals from the
// full `data` object.
export const CHAT_EXPORT_FIELDS = [
  "id",
  "trace_id",
  "conversation_id",
  "scenario",
  "scenario_columns",
  "status",
  "simulation_call_type",
  "transcript",
  "eval_metrics",
  "call_summary",
  "customer_latency_metrics",
  "customer_cost_breakdown",
];
