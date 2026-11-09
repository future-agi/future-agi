// Responses shaped like the agent pages contract (sections 2-6), for tests.

export const HOSTED_AGENT_DETAIL = {
  id: "bf8f1fb9-426b-48cc-a21d-be09bbf2e802",
  agent_name: "Northwind Pharmacy",
  agent_type: "voice",
  contact_number: "+14155550100",
  inbound: true,
  target_speaks_first: false,
  description: "Callers ring you to refill a prescription.",
  assistant_id: "8ea1e33d-c4b7-4288-8e3c-50fa2cb539fa",
  provider: "vapi",
  api_key: "",
  created_at: "2026-10-13T11:36:32.404250Z",
  versions: [
    {
      id: "v1-id",
      version_number: 1,
      version_name_display: "v1",
      status: "inactive",
      commit_message: "First version",
      is_active: false,
      is_latest: false,
      created_at: "2026-10-13T11:36:32Z",
    },
    {
      id: "v2-id",
      version_number: 2,
      version_name_display: "v2",
      status: "active",
      commit_message: "Shorter greeting",
      is_active: true,
      is_latest: true,
      created_at: "2026-10-15T09:00:00Z",
    },
  ],
  active_version: {
    id: "v2-id",
    version_number: 2,
    version_name_display: "v2",
    status: "active",
    is_active: true,
    is_latest: true,
    created_at: "2026-10-15T09:00:00Z",
  },
  version_count: 2,
  agent_kind: null,
  source: "vapi",
  environment_count: 0,
  observability_enabled: false,
};

export const CODE_AGENT_DETAIL = {
  id: "a48511b2-4179-4b1f-85ca-020d0ffe37a9",
  agent_name: "Uber Voice Agent",
  agent_type: "voice",
  inbound: true,
  description: "# ROLE\nYou are the Uber phone-booking assistant.",
  assistant_id: null,
  provider: null,
  created_at: "2026-10-14T11:50:22.093543Z",
  versions: [
    {
      id: "93956ae6-b738-4942-aa80-10eb95f608d3",
      version_number: 1,
      version_name_display: "v1",
      status: "active",
      commit_message: "hosted harness target agent prompt",
      is_active: true,
      is_latest: true,
      created_at: "2026-10-14T11:50:22.198107Z",
    },
  ],
  active_version: {
    id: "93956ae6-b738-4942-aa80-10eb95f608d3",
    version_number: 1,
    version_name_display: "v1",
    status: "active",
    is_active: true,
    is_latest: true,
    created_at: "2026-10-14T11:50:22.198107Z",
  },
  version_count: 1,
  agent_kind: "source",
  source: "github",
  environment_count: 1,
  observability_enabled: false,
};

export const ENVIRONMENT_RUN = {
  kind: "environment",
  test_execution_id: "68dedb99-42b6-46e7-8e94-b511041fa2cb",
  run_test_id: "b741b14e-91c0-403d-81e5-5dd7e40d09c7",
  run_test_name: "Uber Voice Agent",
  run_number: 2,
  status: "completed",
  environment: {
    id: "e5391b8a-156e-46dd-b5a4-4761971f2ccd",
    name: "ride-voice-agent",
  },
  agent_version: { id: "93956ae6", version_number: 3 },
  outcome_passed: 3,
  outcome_failed: 7,
  outcome_skipped: 0,
  started_at: "2026-10-14T11:58:53.368130+00:00",
  completed_at: "2026-10-14T12:27:36.569945+00:00",
};

export const SIMULATE_RUN = {
  kind: "simulate",
  test_execution_id: "c766900d-00e6-41c7-b922-c4e8eed1e7ec",
  run_test_id: "c8cc6400-c677-4efd-9ad0-d177c912dad4",
  run_test_name: "Old refill check",
  run_number: 1,
  status: "running",
  environment: null,
  agent_version: null,
  outcome_passed: null,
  outcome_failed: null,
  outcome_skipped: null,
  started_at: "2026-10-16T10:24:09.452801+00:00",
  completed_at: null,
};

// GET .../versions/{v}/agent/ for a code agent version that stored one.
export const STORED_AGENT = {
  agent: {
    schema_version: "1.0",
    type: "voice",
    kind: "source",
    name: "uber_voice_agent",
    provider_ref: null,
    behaviour: [
      {
        id: "main",
        shape: "external",
        instructions: "# ROLE\nYou are the Uber phone-booking assistant.",
        tools: ["create_guest_rider", "get_saved_places"],
        transitions: [],
      },
    ],
    tools: [
      {
        name: "create_guest_rider",
        description: "Create a local guest rider.",
        kind: "builtin",
        parameters: null,
        endpoint: null,
      },
      {
        name: "get_saved_places",
        description: "Get saved place labels.",
        kind: "builtin",
        parameters: null,
        endpoint: null,
      },
    ],
    model: {
      provider: "openai",
      name: "gpt-4.1",
      temperature: 0.3,
      max_tokens: 1024,
    },
    voice: {
      provider: "cartesia",
      voice_id: "sonic-en",
      language: "en",
      speed: 1,
    },
    telephony: [
      {
        number: "+14155550199",
        provider: "twilio",
        direction_supported: ["inbound"],
      },
    ],
    supports: [],
    direction_answer: null,
    gaps: ["flow_not_described"],
    instrumentation: "none",
    setup_hash:
      "sh1:d5eaaae5421fd4bf8e7dc30693ba366740987ae71a3224d5a7422a64e1b91593",
  },
  sdk_version: "0.2.3",
  stored_at: "2026-10-14T11:50:22.212003Z",
  setup_stale: false,
};

export const CODE_VERSION_DETAIL = {
  id: "93956ae6-b738-4942-aa80-10eb95f608d3",
  version_number: 1,
  version_name_display: "v1",
  status: "active",
  configuration_snapshot: {
    inbound: true,
    languages: [],
    agent_name: "Uber Voice Agent",
    agent_type: "voice",
    observability_enabled: false,
  },
  is_active: true,
  is_latest: true,
  agent_kind: "source",
  api_key: null,
  created_at: "2026-10-14T11:50:22.198107Z",
};

export const HOSTED_VERSION_DETAIL = {
  id: "v2-id",
  version_number: 2,
  version_name_display: "v2",
  status: "active",
  description: "Callers ring you to refill a prescription.",
  configuration_snapshot: {
    inbound: true,
    provider: "vapi",
    languages: ["en", "es"],
    agent_name: "Northwind Pharmacy",
    agent_type: "voice",
    description: "Callers ring you to refill a prescription.",
    assistant_id: "8ea1e33d-c4b7-4288-8e3c-50fa2cb539fa",
    contact_number: "+14155550100",
    model: "gpt-4o",
    target_speaks_first: false,
    observability_enabled: true,
  },
  is_active: true,
  is_latest: true,
  agent_kind: null,
  api_key: "sk-a...9f2c",
  created_at: "2026-10-15T09:00:00Z",
};

export const AGENT_ENVIRONMENTS = {
  count: 2,
  next: null,
  previous: null,
  total_pages: 1,
  current_page: 1,
  results: [
    {
      id: "e5391b8a",
      name: "ride-voice-agent",
      stage: "completed",
      status: "completed",
      last_run: ENVIRONMENT_RUN,
    },
    {
      id: "e9999999",
      name: "Refund edge cases",
      stage: "failed",
      status: "failed",
      last_run: null,
    },
  ],
};

export const OBSERVABILITY_REFUSED = {
  status: false,
  type: "validation_error",
  code: "observability_prerequisite_missing",
  message:
    "Observability cannot be turned on: The agent's code carries no OpenTelemetry or traceAI instrumentation.",
  attr: "instrumentation",
  details: {
    instrumentation: ["The agent's code carries no instrumentation."],
  },
};
