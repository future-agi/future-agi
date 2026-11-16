// The cast as `GET .../actors/` returns it: each actor typed, with what the server works out for it, and what an actor
// can be given here. Every field the contract names is present, so a test states only what it is about.

export const SILENT = {
  kind: "silent",
  value: "silent",
  label: "Nothing happens",
};

/**
 * One typed actor. `trigger` and `fallback` go where the cast keeps them; `permissions` follow `permitted_actions`
 * (each still in the world) unless given.
 */
export const typedActor = ({
  trigger = { kind: "always" },
  fallback = SILENT,
  timeout = 0,
  fallbackArguments = null,
  ...rest
} = {}) => {
  const permitted = rest.permitted_actions || [];
  return {
    id: "actor",
    name: "Actor",
    role: "",
    kind: "person",
    goal: "",
    play: "mocked",
    model: null,
    side: "world",
    channel: null,
    parent: null,
    persona: null,
    loudness_ratio: null,
    constraints: {
      may_agree: [],
      private: [],
      escalate_when: [],
      stop_when: [],
    },
    facts: [],
    files: [],
    records: [],
    behaviour: {
      style: "",
      response_timing: { kind: "immediate", seconds: 0 },
    },
    permitted_actions: permitted,
    activation: {
      trigger,
      timeout_seconds: timeout,
      fallback: fallback.value,
      fallback_arguments: fallbackArguments,
    },
    max_turns: null,
    max_lines: null,
    scenarios: 0,
    removable: true,
    removal_blocked_by: [],
    locked_fields: [],
    reaches: [],
    fallback,
    permissions: permitted.map((action) => ({ action, in_world: true })),
    editable_trigger: true,
    when_label: "From the start",
    timeout_label: null,
    ...rest,
  };
};

export const ACTIONS = [
  {
    action: "crm.approve_discount",
    tool: "approve_discount",
    description: "Approve a pending discount",
  },
  {
    action: "crm.request_approval",
    tool: "request_approval",
    description: "Ask a manager to approve",
  },
];

const WORLD_KINDS = [
  "always",
  "on_tool",
  "contacted",
  "after_seconds",
  "conditional",
];
const SPEAKING_KINDS = [
  "always",
  "on_tool",
  "on_keyword",
  "mid_journey",
  "conditional",
];

export const CHANNEL_OPTIONS = [
  {
    channel: null,
    description: "They act only through the world's actions.",
    loudness: false,
    trigger_kinds: WORLD_KINDS,
    required_fields: [],
  },
  {
    channel: "audio_mix",
    description: "a voice near the person",
    loudness: true,
    trigger_kinds: SPEAKING_KINDS,
    required_fields: ["role"],
  },
  {
    channel: "audio_handoff",
    description: "takes the phone",
    loudness: false,
    trigger_kinds: SPEAKING_KINDS,
    required_fields: ["role"],
  },
];

export const LIMIT_FIELDS = [
  { key: "max_active", kind: "whole" },
  { key: "caller_takes_back_after_turns", kind: "whole" },
  { key: "actor_seconds_per_call", kind: "seconds" },
  { key: "max_messages", kind: "whole" },
  { key: "live_turns_per_actor", kind: "whole" },
  { key: "live_turns_per_trial", kind: "whole" },
  { key: "model", kind: "model" },
];

export const cast = (actors, extra = {}) => ({
  actors,
  source: "draft",
  revision: 3,
  actions: ACTIONS,
  channel_options: CHANNEL_OPTIONS,
  loudness: { automatic: [0.333, 0.667] },
  id_hint: "1 to 64 lowercase letters, digits or underscores.",
  knowledge_sources: { files: [], records: [] },
  actor_models: [],
  has_caller: actors.some((one) => one.side === "caller"),
  side_defaults: { caller: { channel: "audio_mix" }, world: { channel: null } },
  record_max_chars: 65536,
  limit_fields: LIMIT_FIELDS,
  limits: {},
  unresolved: [],
  ...extra,
});

// The server's verdict on an edit with nothing to fix.
export const CLEAN = {
  problems: [],
  conflicts: [],
  suggested_id: null,
  over_by: 0,
};
