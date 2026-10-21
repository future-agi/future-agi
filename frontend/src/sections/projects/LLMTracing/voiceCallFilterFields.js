/**
 * Voice-call list rows expose friendly top-level keys, while ClickHouse
 * filters use canonical span/system metric ids. Keep that mapping in one
 * place so tracing and eval-task filters cannot silently diverge.
 */
export const VOICE_CALL_STATUS_CHOICES = [
  "completed",
  "in-progress",
  "failed",
  "dropped",
  "not-connected",
];

export const VOICE_CALL_FILTER_FIELDS = [
  {
    value: "call_id",
    responseKey: "call_id",
    label: "Call ID",
    // Provider call ids are high-cardinality exact values.  Render a direct
    // text input instead of issuing a broad distinct-values query.
    type: "text",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    searchAliases: ["provider_call_id", "voice_call_id"],
  },
  {
    value: "call_status",
    responseKey: "status",
    label: "Status",
    type: "string",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    // Status is a closed canonical vocabulary in the list API. Supplying it
    // locally keeps this critical filter usable even when the optional recent-
    // values query is unavailable on a very large project.
    choices: VOICE_CALL_STATUS_CHOICES,
    // This is a closed list response vocabulary.  Letting the generic picker
    // add arbitrary text would make Tracing send provider values while Tasks
    // canonicalizes them, recreating the cross-surface drift this registry is
    // intended to prevent.
    allowCustomValue: false,
    // The voice-list alias matches the normalized status rendered in Live
    // Preview (for example provider `ended` becomes `completed`). Generic
    // call.status remains a raw span attribute everywhere else.
    legacyWireValues: ["call.status"],
    searchAliases: ["status", "call.status"],
  },
  {
    value: "duration",
    responseKey: "duration_seconds",
    // Filters consume seconds even though the grid cell formats that raw
    // value for readability.
    label: "Duration (seconds)",
    columnLabel: "Duration",
    filterUnit: "seconds",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    searchAliases: ["duration_seconds"],
    // Saved simulator views used the list response key before the canonical
    // system-metric alias was introduced.  The units are identical, so this
    // migration is unambiguous.
    savedViewAliases: ["duration_seconds"],
  },
  {
    value: "avg_agent_latency_ms",
    responseKey: "avg_agent_latency_ms",
    label: "Avg Agent Latency (ms)",
    columnLabel: "Avg Latency",
    filterUnit: "milliseconds",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    // Dashboard metrics still publishes the older simulator alias.  Suppress
    // it from the voice picker so there is one field for the visible column.
    dynamicAliases: ["agent_latency"],
    savedViewAliases: ["agent_latency"],
  },
  {
    value: "turn_count",
    responseKey: "turn_count",
    label: "Turn Count",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
  {
    value: "talk_ratio",
    responseKey: "talk_ratio",
    // The API expression is rounded agent talk percentage; the grid renders
    // the companion user:agent split and therefore uses a different header.
    label: "Agent Talk (%) — rounded",
    columnLabel: "Talk Ratio",
    filterUnit: "rounded-percent",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
  {
    value: "gen_ai.usage.total_tokens",
    responseKey: "gen_ai.usage.total_tokens",
    label: "Tokens",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    searchAliases: ["tokens", "total_tokens"],
    dynamicAliases: ["tokens", "total_tokens"],
    savedViewAliases: ["tokens", "total_tokens"],
  },
  {
    value: "cost_cents",
    responseKey: "cost_cents",
    // VoiceCostCell formats cents as dollars, but numeric filters use the raw
    // normalized cents value.
    label: "Cost (cents)",
    columnLabel: "Cost",
    filterUnit: "cents",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    // Older task drafts used total_cost (VAPI currency units). The backend's
    // voice-list-only cost_cents alias now normalizes providers to the exact
    // top-level value rendered by Live Preview.
    legacyWireValues: ["total_cost"],
    legacyApiValueScale: 0.01,
    searchAliases: ["cost", "total_cost"],
    dynamicAliases: ["total_cost"],
    // Older Task drafts used VAPI total_cost currency units, so the Task-only
    // legacy wire conversion above remains supported. Saved tracing filters
    // do not carry provider context (Retell's cost is already cents), making
    // automatic total_cost migration unsafe.
  },
  {
    value: "user_interruption_count",
    responseKey: "user_interruption_count",
    label: "User Interrupts",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    dynamicAliases: ["user_interruptions"],
    savedViewAliases: ["user_interruptions"],
  },
  {
    value: "ai_interruption_count",
    responseKey: "ai_interruption_count",
    label: "Agent Interrupts",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
    dynamicAliases: ["ai_interruptions"],
    savedViewAliases: ["ai_interruptions"],
  },
  {
    value: "ended_reason",
    responseKey: "ended_reason",
    label: "Ended Reason",
    type: "string",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
  {
    value: "call_type",
    responseKey: "call_type",
    label: "Type",
    type: "string",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
  {
    value: "user_wpm",
    responseKey: "user_wpm",
    label: "User WPM",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
  {
    value: "bot_wpm",
    responseKey: "bot_wpm",
    label: "Agent WPM",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
  {
    value: "agent_talk_percentage",
    responseKey: "agent_talk_percentage",
    label: "Agent Talk Percentage",
    columnLabel: "Agent Talk (%)",
    filterUnit: "percent",
    type: "number",
    category: "system",
    apiColType: "SYSTEM_METRIC",
  },
];

const VOICE_FIELD_BY_ID = new Map(
  VOICE_CALL_FILTER_FIELDS.flatMap((field) =>
    [
      ...new Set([
        field.value,
        field.responseKey,
        ...(field.legacyWireValues || []),
        ...(field.dynamicAliases || []),
        ...(field.savedViewAliases || []),
      ]),
    ].map((id) => [id, field]),
  ),
);

const VOICE_FIELD_BY_CANONICAL_ID = new Map(
  VOICE_CALL_FILTER_FIELDS.map((field) => [field.value, field]),
);

// Saved-view aliases are intentionally narrower than Task aliases. In
// particular, `status` is the OTel trace status and `call.status` is a raw
// provider attribute, so neither may be guessed to mean the normalized voice
// lifecycle status merely because the current project renders CallLogsGrid.
const VOICE_FIELD_BY_SAVED_VIEW_ALIAS = new Map(
  VOICE_CALL_FILTER_FIELDS.flatMap((field) =>
    (field.savedViewAliases || []).map((id) => [id, field]),
  ),
);

export const getVoiceCallFilterField = (fieldId) =>
  VOICE_FIELD_BY_ID.get(fieldId);

const scaleValue = (value, scale) => {
  if (value === "" || value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map((item) => scaleValue(item, scale));
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  return Number((numeric * scale).toPrecision(15));
};

const COMPLETED_STATUS_ALIASES = new Set([
  "ended",
  "done",
  "complete",
  "completed",
  "success",
  "succeeded",
  "ok",
]);
const IN_PROGRESS_STATUS_ALIASES = new Set([
  "in-progress",
  "in_progress",
  "ongoing",
  "started",
  "initiated",
  "processing",
  "scheduled",
  "created",
  "dialing",
  "connecting",
  "ringing",
  "queued",
  "pending",
]);
const FAILED_STATUS_ALIASES = new Set([
  "failed",
  "failure",
  "error",
  "errored",
]);
const DROPPED_STATUS_ALIASES = new Set([
  "dropped",
  "cancelled",
  "canceled",
  "aborted",
  "hung-up",
  "hung_up",
]);
const NOT_CONNECTED_STATUS_ALIASES = new Set([
  "not-connected",
  "not_connected",
  "no-answer",
  "no_answer",
  "unanswered",
  "busy",
]);

export const normalizeVoiceCallStatus = (value) => {
  if (Array.isArray(value)) {
    return [...new Set(value.map(normalizeVoiceCallStatus))];
  }
  if (typeof value !== "string") return value;
  const normalized = value.trim().toLowerCase();
  if (COMPLETED_STATUS_ALIASES.has(normalized)) return "completed";
  if (IN_PROGRESS_STATUS_ALIASES.has(normalized)) return "in-progress";
  if (FAILED_STATUS_ALIASES.has(normalized)) return "failed";
  if (DROPPED_STATUS_ALIASES.has(normalized)) return "dropped";
  if (NOT_CONNECTED_STATUS_ALIASES.has(normalized)) return "not-connected";
  // The voice-list response is a closed five-value vocabulary. New provider
  // transition tokens must remain filterable before the frontend knows their
  // spelling, so every other non-empty status uses the backend's explicit
  // in-progress fallback instead of leaking a sixth picker value.
  return normalized ? "in-progress" : normalized;
};

export const toVoiceCallApiValue = (fieldId, value) => {
  const field = getVoiceCallFilterField(fieldId);
  if (field?.value === "call_status") return normalizeVoiceCallStatus(value);
  const scale =
    field?.apiValueScale ||
    (field?.legacyWireValues?.includes(fieldId)
      ? field.legacyApiValueScale
      : undefined);
  return scale ? scaleValue(value, scale) : value;
};

export const fromVoiceCallApiValue = (fieldId, value) => {
  const field = getVoiceCallFilterField(fieldId);
  if (field?.value === "call_status") return normalizeVoiceCallStatus(value);
  const scale =
    field?.apiValueScale ||
    (field?.legacyWireValues?.includes(fieldId)
      ? field.legacyApiValueScale
      : undefined);
  return scale ? scaleValue(value, 1 / scale) : value;
};

const canonicalFilterType = (field) =>
  field?.type === "number" ? "number" : "text";

/**
 * Canonicalize one already-hydrated saved-view filter for CallLogsGrid.
 *
 * Only canonical voice ids and explicitly safe saved-view aliases participate.
 * Raw SPAN_ATTRIBUTE filters are left alone even when their key happens to
 * match a list response key.
 */
export const normalizeVoiceCallSavedFilter = (filter) => {
  if (!filter?.column_id || !filter?.filter_config) return filter;
  const config = filter.filter_config;
  if (config.col_type === "SPAN_ATTRIBUTE") return filter;

  const sourceFieldId = filter.column_id;
  const field =
    VOICE_FIELD_BY_CANONICAL_ID.get(sourceFieldId) ||
    VOICE_FIELD_BY_SAVED_VIEW_ALIAS.get(sourceFieldId);
  if (!field) return filter;

  const hasValue = Object.prototype.hasOwnProperty.call(config, "filter_value");
  let filterValue = config.filter_value;
  if (hasValue) {
    filterValue =
      field.value === "call_status"
        ? normalizeVoiceCallStatus(filterValue)
        : sourceFieldId === field.value
          ? filterValue
          : fromVoiceCallApiValue(sourceFieldId, filterValue);
  }

  return {
    ...filter,
    column_id: field.value,
    display_name: field.label,
    filter_config: {
      ...config,
      filter_type: canonicalFilterType(field),
      col_type: field.apiColType,
      ...(hasValue ? { filter_value: filterValue } : {}),
    },
  };
};

export const normalizeVoiceCallSavedFilters = (filters) =>
  (filters || []).map(normalizeVoiceCallSavedFilter);
