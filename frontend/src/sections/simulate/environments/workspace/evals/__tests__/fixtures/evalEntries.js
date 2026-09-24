// frontend/src/sections/simulate/environments/workspace/evals/__tests__/fixtures/evalEntries.js
//
// The contract's own example entry (api_contracts/harness/eval-offer-backend-frontend.md
// v1.8 §1 — §1 is unchanged since v1.7, which only added §13), copied
// verbatim, so a shape drift shows up as a failing test rather than a
// silently blank row. Not collected by vitest: the include glob is
// src/**/*.{test,spec}.{js,jsx,ts,tsx}.
//
// `agent_type` is the backend's own value and only ever "voice" or "text"
// (AgentDefinition.AgentTypeChoices, simulate/models/agent_definition.py:17-18)
// — never "chat". That is the exact string `buildRunStats` and the KPI view key
// their modality branches on, so a wrong value here would be the opposite of
// what this file is for (round-3 L5).

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

// P1: `required_keys` is the template's stored order and is NOT aligned with
// `inputs` (which is sorted by key). Same eval, stored order reversed — any code
// that paired the two by position renders the wrong arrows against this one.
export const NO_MISSELLING_UNALIGNED = {
  ...NO_MISSELLING,
  required_keys: ["conversation", "agent_prompt"],
};

// The organisation's own eval (P4: source "custom"), on a text environment.
export const CUSTOM_EVAL = {
  name: "refund_wording",
  description: "Our own wording check",
  source: "custom",
  tags: ["Agents", "Chatbot behaviors"],
  required_keys: ["conversation"],
  agent_type: "text",
  modality: "text",
  credits_per_run: 0.5,
  charges_judge_tokens: true,
  inputs: [{ key: "conversation", source: "transcript", label: "Transcript" }],
};

// A code eval — 0.5 credits per run, no judge tokens (P4: charges_judge_tokens
// is false for a code template; nothing is ever free).
export const CODE_EVAL = {
  name: "json_is_valid",
  description: "A code check — no judge, no extra charge",
  source: "system",
  tags: ["Agents"],
  required_keys: ["input"],
  agent_type: "text",
  modality: "text",
  credits_per_run: 0.5,
  charges_judge_tokens: false,
  inputs: [
    { key: "input", source: "scenario_columns.situation.value", label: "Scenario situation" },
  ],
};

// A built-in, judge-charging eval on a TEXT environment. It exists so the
// Library/Custom + cost-line test can use three entries of one `agent_type`:
// P3 says every entry in ONE `available` response carries the same
// `agent_type`, and the earlier fixture set forced that test to mix
// NO_MISSELLING (voice) with CUSTOM_EVAL/CODE_EVAL (text) — a response the
// server cannot produce (round-3 L5).
export const LIBRARY_TEXT_EVAL = {
  name: "no_pii_leak",
  description: "Checks the agent never repeats a customer's personal data back",
  source: "system",
  tags: ["Agents", "Conversation", "Chatbot behaviors"],
  required_keys: ["conversation"],
  agent_type: "text",
  modality: "text",
  credits_per_run: 0.5,
  charges_judge_tokens: true,
  inputs: [{ key: "conversation", source: "transcript", label: "Transcript" }],
};

// A built-in, judge-charging eval on a VOICE environment, distinct from
// NO_MISSELLING. Minor-6 (fix round 2): the P27 v1.7 bound-group tests paired
// a voice `BOUND` (NO_MISSELLING) with a text `available` offer (CUSTOM_EVAL)
// — the same P3 impossibility L5 fixed one level down (one `available`
// response cannot mix modalities), just one level up: an environment cannot
// hold a voice eval in `selected[]` and be offered a text one, because it has
// one `agent_type`. This fixture lets those tests offer something voice
// alongside a voice `BOUND` instead.
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

// `evaluations.selected[]` = the same entry plus `id` and `runnable` (P16).
export const selectedEntry = (entry, id) => ({ ...entry, id, runnable: true });
