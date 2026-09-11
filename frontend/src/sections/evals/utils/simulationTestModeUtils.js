// Voice-only fields per DB matrix (chat 0%, prompt 0%); hide on text sims.
export const VOICE_ONLY_METRICS = [
  "user_wpm",
  "bot_wpm",
  "user_interruption_count",
  "user_interruption_rate",
  "ai_interruption_count",
  "ai_interruption_rate",
  "avg_stop_time_after_interruption",
  "avg_stop_time_after_interruption_ms",
  "cost_cents",
  "customer_cost_cents",
  "customer_cost_breakdown",
  "customer_latency_metrics",
  "llm_cost_cents",
  "storage_cost_cents",
  "stt_cost_cents",
  "tts_cost_cents",
  "vapi_cost_cents",
  "customer_number",
  "message_count",
  "customer_log_url",
  "customer_logs_summary",
  "monitor_call_data",
  "logs_ingested_at",
];

// Hidden at picker vocabulary + render (bypasses raw pass-through SKIP too).
export const NEVER_PICKABLE_TOPLEVEL = [
  "eval_outputs",
  "eval_metrics",
  "customer_name",
  "customer_call_id",
];

// simulation_call_type (modality) wins over call_type (direction).
export const isTextCallDetail = (d) =>
  ["text", "chat", "prompt"].includes(
    String(
      d?.simulation?.call_type || d?.simulation_call_type || d?.call_type || "",
    ).toLowerCase(),
  );

const startsWithAnyRoot = (key, roots) =>
  roots.some((root) => key === root || key.startsWith(`${root}.`));

export const isHiddenPickerPath = (key, isTextCall) =>
  startsWithAnyRoot(key, NEVER_PICKABLE_TOPLEVEL) ||
  (isTextCall && startsWithAnyRoot(key, VOICE_ONLY_METRICS));

// `scenario.columns.<name>.<subpath>` -> `scenario_columns.<name>.value.<subpath>` for the BE walker.
const DEEP_SCENARIO_COLUMN_RE = /^scenario\.columns\.([^.]+)\.(.+)$/;
export const translateDeepScenarioColumn = (field) => {
  const m = DEEP_SCENARIO_COLUMN_RE.exec(field);
  return m ? `scenario_columns.${m[1]}.value.${m[2]}` : null;
};
