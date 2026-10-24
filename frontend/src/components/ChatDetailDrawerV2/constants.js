// Shared constants for the revamped chat detail drawer.

// Placeholder bubble widths for the transcript skeleton loader.
export const SKELETON_BUBBLE_WIDTHS = [220, 160, 260, 180, 240];

// System turns are filtered out of the rendered chat transcript.
export const SYSTEM_SPEAKER_ROLE = "system";

// Chat-tailored Imagine prompts — same shape as `VOICE_IMAGINE_PROMPTS` in
// VoiceDetailDrawerV2. Surfaced as suggested-prompt chips in an unsaved
// Imagine tab.
export const CHAT_IMAGINE_PROMPTS = [
  { label: "Summarize this chat", icon: "solar:notes-linear" },
  { label: "Show the conversation flow", icon: "solar:chat-round-line-linear" },
  {
    label: "Where did the user get frustrated?",
    icon: "solar:sad-circle-linear",
  },
  {
    label: "What's the cost breakdown?",
    icon: "solar:dollar-minimalistic-linear",
  },
  { label: "Compare tone across turns", icon: "solar:graph-up-linear" },
  { label: "Evaluate chat quality", icon: "solar:check-circle-linear" },
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
