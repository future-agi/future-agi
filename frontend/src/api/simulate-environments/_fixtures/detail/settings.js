// Vel's sample Settings (prototype f0b6d8d8c), shown only with
// ?uiSample=settings. None of it describes a real environment: secret
// references, the simulated user's model, the stuck-after limit, the replay
// header and the production-scenarios switch are not in our API yet.

export const SAMPLE_ENV_VARS = [
  {
    key: "OPENAI_API_KEY",
    ref: "vault://acme/sim/openai_api_key",
    kind: "Secret",
    masked: true,
  },
  { key: "SEED_DATASET", value: "orders-v4", kind: "Config", masked: false },
  { key: "LOG_LEVEL", value: "info", kind: "Config", masked: false },
];

export const SAMPLE_EGRESS_DOMAINS = [
  "api.stripe.com",
  "global.turn.twilio.com",
];

export const SAMPLE_SIMULATOR_MODELS = [
  {
    id: "gpt-4o-mini",
    label: "GPT-4o mini",
    note: "Fast and cheap. The default.",
  },
  {
    id: "gpt-4.1",
    label: "GPT-4.1",
    note: "Follows long caller briefs more closely.",
  },
  {
    id: "claude-sonnet-4-5",
    label: "Claude Sonnet 4.5",
    note: "Holds a persona over long conversations.",
  },
  {
    id: "gemini-2.5-flash",
    label: "Gemini 2.5 Flash",
    note: "Lowest latency for voice.",
  },
];

export const SAMPLE_SIMULATOR_MODEL = "gpt-4o-mini";

// Minutes without activity before a conversation counts as stuck.
export const SAMPLE_STUCK_AFTER_MIN = 5;

export const SAMPLE_REPLAY_HEADER = "x-fagi-replay: 1";
export const SAMPLE_REPLAY_SDK = "replay=true";

// Vel's default: tools whose name says they change something are marked.
const SIDE_EFFECT =
  /refund|cancel|book|charge|transfer|send|update|delete|create/i;
export const sampleSideEffectTools = (tools = []) =>
  tools.map((t) => t.name).filter((name) => SIDE_EFFECT.test(name || ""));

export const SAMPLE_PRODUCTION_SCENARIOS_ON = false;
