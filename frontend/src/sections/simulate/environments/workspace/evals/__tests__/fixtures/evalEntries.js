// The contract's own example entry, copied verbatim, so a shape drift
// shows up as a failing test rather than a silently blank row. Not
// collected by vitest: the include glob is
// src/**/*.{test,spec}.{js,jsx,ts,tsx}.
//
// An entry's own `agent_type` is "voice" or "chat", the two values the
// evaluation definitions declare. The run KPIs carry a field of the same
// name on a different endpoint, whose values are the ones `buildRunStats`
// and the KPI view key branch on — a separate vocabulary that happens to
// share a name. Keep these entries on the definitions' enum: a value from
// the other vocabulary would be exactly the drift this file exists to
// catch.

export const NO_MISSELLING = {
  name: "no_misselling",
  description: "Evaluates whether an agent mis-sells …",
  source: "system",
  tags: ["Agents", "Conversation", "Voice", "Chatbot behaviors", "Insurance"],
  required_keys: ["agent_prompt", "conversation"],
  agent_type: "voice",
  modality: "voice",
  credits_per_run: 0.5,
  charges_judge_tokens: true,
  inputs: [
    { key: "agent_prompt", source: "agent_prompt", label: "Agent instructions" },
    { key: "conversation", source: "voice_recording", label: "Call recording" },
  ],
};

// `required_keys` is the template's stored order and is NOT aligned with
// `inputs` (which is sorted by key). Same eval, stored order reversed — any
// code that paired the two by position renders the wrong arrows against
// this one.
export const NO_MISSELLING_UNALIGNED = {
  ...NO_MISSELLING,
  required_keys: ["conversation", "agent_prompt"],
};

// The organisation's own eval (source "custom"), on a text environment.
export const CUSTOM_EVAL = {
  name: "refund_wording",
  description: "Our own wording check",
  source: "custom",
  tags: ["Agents", "Chatbot behaviors"],
  required_keys: ["conversation"],
  agent_type: "chat",
  modality: "text",
  credits_per_run: 0.5,
  charges_judge_tokens: true,
  inputs: [{ key: "conversation", source: "transcript", label: "Transcript" }],
};

// A code eval — 0.5 credits per run, no judge tokens (charges_judge_tokens
// is false for a code template; nothing is ever free).
export const CODE_EVAL = {
  name: "json_is_valid",
  description: "A code check — no judge, no extra charge",
  source: "system",
  tags: ["Agents"],
  required_keys: ["input"],
  agent_type: "chat",
  modality: "text",
  credits_per_run: 0.5,
  charges_judge_tokens: false,
  inputs: [
    { key: "input", source: "scenario_columns.situation.value", label: "Scenario situation" },
  ],
};

// A built-in, judge-charging eval on a TEXT environment. It exists so the
// Library/Custom + cost-line test can use three entries of one
// `agent_type`: every entry in ONE `available` response carries the same
// `agent_type`, and mixing NO_MISSELLING (voice) with CUSTOM_EVAL/CODE_EVAL
// (chat) is a response the server cannot produce.
export const LIBRARY_TEXT_EVAL = {
  name: "no_pii_leak",
  description: "Checks the agent never repeats a customer's personal data back",
  source: "system",
  tags: ["Agents", "Conversation", "Chatbot behaviors"],
  required_keys: ["conversation"],
  agent_type: "chat",
  modality: "text",
  credits_per_run: 0.5,
  charges_judge_tokens: true,
  inputs: [{ key: "conversation", source: "transcript", label: "Transcript" }],
};

// A built-in, judge-charging eval on a VOICE environment, distinct from
// NO_MISSELLING. The bound-group tests need something voice to offer
// alongside a voice `BOUND` entry: an environment cannot hold a voice eval
// in `selected[]` and be offered a text one, since it has one `agent_type`.
export const LIBRARY_VOICE_EVAL = {
  name: "off_topic_detection",
  description: "Checks the agent stays on the call's own topic",
  source: "system",
  tags: ["Agents", "Conversation"],
  required_keys: ["conversation"],
  agent_type: "voice",
  modality: "voice",
  credits_per_run: 0.5,
  charges_judge_tokens: true,
  inputs: [{ key: "conversation", source: "voice_recording", label: "Call recording" }],
};

// `evaluations.selected[]` = the same entry plus `id` and `runnable`.
export const selectedEntry = (entry, id) => ({ ...entry, id, runnable: true });
