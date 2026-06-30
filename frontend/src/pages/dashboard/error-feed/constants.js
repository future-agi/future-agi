// Shared constants for the Error Feed cluster-analysis UI.
//
// Centralises the enums and tunables that were previously inlined as raw
// strings / magic numbers across the socket engine, the Analyze tab, the
// headline card and the Overview tab. Keep wire-facing values (message
// `type`, step `status`) in sync with the cluster-RCA agent's frame schema.

// ── Thread run states (store + socket) ───────────────────────────────────────
export const RUN_STATE = Object.freeze({
  IDLE: "idle",
  STREAMING: "streaming",
  DONE: "done",
});

// ── Inbound socket frame types (raw WS — snake_case from the agent) ──────────
export const RCA_FRAME = Object.freeze({
  STATUS: "rca_status",
  REASONING: "rca_reasoning",
  STEP_START: "rca_step_start",
  STEP_RESULT: "rca_step_result",
  SYNTHESIS: "rca_synthesis",
  DONE: "done",
  ERROR: "error",
});

// Follow-up (plain Falcon chat) frame types.
export const CHAT_FRAME = Object.freeze({
  TOOL_CALL_START: "tool_call_start",
  TOOL_CALL_RESULT: "tool_call_result",
  TEXT_DELTA: "text_delta",
  DONE: "done",
  ERROR: "error",
});

// Persisted replay-trail frame types (rca trace cached on the cluster).
export const TRAIL_FRAME = Object.freeze({
  REASONING: "reasoning",
  STEP_START: "step_start",
  STEP_RESULT: "step_result",
  SYNTHESIS: "synthesis",
});

// ── Thread message types (what the renderers switch on) ──────────────────────
export const MESSAGE_TYPE = Object.freeze({
  REASONING: "reasoning",
  STEP: "step",
  SYNTHESIS: "synthesis",
  SUGGESTIONS: "suggestions",
  RUN_HEADER: "run_header",
  USER_QUESTION: "user_question",
  ASSISTANT_INTRO: "assistant_intro",
  SUBAGENT: "subagent",
});

// ── Step status (Analyze tab step cards + headline step chips) ───────────────
export const STEP_STATUS = Object.freeze({
  QUEUED: "queued",
  RUNNING: "running",
  DONE: "done",
});

// ── Sub-agent / streaming status ─────────────────────────────────────────────
export const STREAM_STATUS = Object.freeze({
  STREAMING: "streaming",
  DONE: "done",
});

// ── Trace pass/fail status (Overview tab) ────────────────────────────────────
export const TRACE_STATUS = Object.freeze({
  PASS: "pass",
  FAIL: "fail",
});

// ── Confidence buckets (agent synthesis) ─────────────────────────────────────
export const CONFIDENCE = Object.freeze({
  HIGH: "H",
  MEDIUM: "M",
  LOW: "L",
});

// Confidence-badge label + accent colour, keyed by the agent's H/M/L code.
// Colours match success.main / a warning amber / a neutral grey.
export const CONF_META = Object.freeze({
  [CONFIDENCE.HIGH]: { label: "High confidence", color: "#5ACE6D" },
  [CONFIDENCE.MEDIUM]: { label: "Medium confidence", color: "#E8A13A" },
  [CONFIDENCE.LOW]: { label: "Low confidence", color: "#8A8A8A" },
});

// ── Token pricing — used to estimate per-trace cost in the Overview tab. ─────
// Per-token USD rates; duplicated inline before this extraction.
export const TOKEN_PRICE_USD = Object.freeze({
  INPUT: 0.000003,
  OUTPUT: 0.000015,
});

// ── Streaming-typewriter tunables (Analyze tab) ──────────────────────────────
export const STREAM_CHARS_PER_TICK = 3;
export const STREAM_TICK_MS = 16;
