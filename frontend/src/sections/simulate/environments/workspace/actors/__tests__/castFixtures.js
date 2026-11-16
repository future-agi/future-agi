// The cast as `GET .../actors/` returns it, every contract field present.

export const SILENT = {
  kind: "silent",
  value: "silent",
  label: "Nothing happens",
};

/** `permissions` follow `permitted_actions` unless given. */
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
    cap_field: "max_turns",
  },
  {
    channel: "audio_mix",
    description: "a voice near the person",
    loudness: true,
    trigger_kinds: SPEAKING_KINDS,
    required_fields: ["role"],
    cap_field: "max_lines",
  },
  {
    channel: "audio_handoff",
    description: "takes the phone",
    loudness: false,
    trigger_kinds: SPEAKING_KINDS,
    required_fields: ["role"],
    cap_field: "max_lines",
  },
];

const limit = (key, kind, value = null, label = null) => ({
  key,
  kind,
  default: value,
  default_label: label,
});

export const LIMIT_FIELDS = [
  limit("max_active", "whole", 2, "Default: 2"),
  limit("caller_takes_back_after_turns", "whole"),
  limit("actor_seconds_per_call", "seconds", 60, "Default: 60 s"),
  limit("max_messages", "whole", 20, "Default: 20"),
  limit("live_turns_per_actor", "whole", 10, "Default: 10"),
  limit("live_turns_per_trial", "whole", 20, "Default: 20"),
  limit("model", "model"),
];

const option = (value, label) => ({ value, label });

export const FIELD_OPTIONS = {
  kind: [
    option("person", "A person"),
    option("approver", "An approver"),
    option("agent", "Another agent"),
    option("system", "A system"),
  ],
  play: [
    option("mocked", "Only the actions each scenario gives them"),
    option("live", "Decide their own actions when reached"),
    option("both", "Both"),
  ],
  fallback_word: [
    option("silent", "Nothing happens"),
    option("decline", "They decline"),
  ],
  gender: [option("male", "Male"), option("female", "Female")],
  age_group: [option("18-25", "18-25"), option("60+", "60+")],
  accent: [option("American", "American"), option("Indian", "Indian")],
  response_timing: [
    option("immediate", "Straight away"),
    option("delayed", "After a delay"),
  ],
};

export const cast = (actors, extra = {}) => ({
  actors,
  source: "draft",
  revision: 3,
  actions: ACTIONS,
  channel_options: CHANNEL_OPTIONS,
  field_options: FIELD_OPTIONS,
  loudness: {
    channels: ["audio_mix"],
    automatic: [0.333, 0.667],
    min: 0.05,
    max: 1,
    step: 0.05,
    default: 0.5,
  },
  upload: {
    formats: [".md", ".txt", ".json", ".csv"],
    max_bytes: 1048576,
    max_label: "1 MB",
  },
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

export const CLEAN = {
  problems: [],
  conflicts: [],
  suggested_id: null,
  over_by: 0,
};
