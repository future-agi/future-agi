// Rows shaped like GET /simulate/agent-definitions/ (agent pages contract,
// section 1), for tests. Covers code agents, hosted agents, an agent from
// before the new fields existed, and values the UI has never seen.

const HAND_WRITTEN = [
  {
    id: "ca-01",
    agent_name: "Customer support line",
    agent_type: "voice",
    inbound: true,
    description: "Inbound phone support for an online storefront",
    created_at: "2026-09-14T09:12:00Z",
    latest_version: 3,
    latest_version_id: "ca-01-v3",
    agent_kind: null,
    source: "vapi",
    version_count: 5,
    environment_count: 2,
    environments: [
      { id: "env-01", name: "Customer support line" },
      { id: "env-02", name: "Refund edge cases" },
    ],
  },
  {
    id: "ca-02",
    agent_name: "Uber Voice Agent",
    agent_type: "voice",
    inbound: false,
    description: "Books rides for a guest on the account holder's behalf",
    created_at: "2026-10-11T14:40:00Z",
    latest_version: 2,
    latest_version_id: "ca-02-v2",
    agent_kind: "source",
    source: "github",
    version_count: 2,
    environment_count: 1,
    environments: [{ id: "env-03", name: "ride-voice-agent" }],
  },
  {
    id: "ca-03",
    agent_name: "Clinic receptionist",
    agent_type: "voice",
    inbound: true,
    description: null,
    created_at: "2026-10-15T08:05:00Z",
    latest_version: 1,
    latest_version_id: "ca-03-v1",
    agent_kind: null,
    source: "livekit",
    version_count: 1,
    environment_count: 0,
    environments: [],
  },
  {
    id: "ca-04",
    agent_name: "Page Turner Books",
    agent_type: "text",
    inbound: true,
    description: "Support assistant for an online bookshop",
    created_at: "2026-08-30T11:20:00Z",
    latest_version: 4,
    latest_version_id: "ca-04-v4",
    agent_kind: "source",
    source: "archive",
    version_count: 4,
    environment_count: 7,
    environments: [
      { id: "env-04", name: "langgraph-otel-agent" },
      { id: "env-05", name: "Citation audit" },
      { id: "env-06", name: "Long-form answers" },
      { id: "env-07", name: "Refunds" },
      { id: "env-08", name: "Returns" },
    ],
  },
  {
    id: "ca-05",
    agent_name: "Billing agent",
    agent_type: "voice",
    inbound: true,
    description: "Answers billing questions",
    created_at: "2026-07-02T16:45:00Z",
    latest_version: 1,
    latest_version_id: "ca-05-v1",
    agent_kind: null,
    source: "vapi",
    version_count: 1,
    environment_count: 0,
    environments: [],
  },
  {
    id: "ca-06",
    agent_name: "Collections reminder",
    agent_type: "voice",
    inbound: false,
    description: null,
    created_at: "2026-09-21T07:30:00Z",
    latest_version: 2,
    latest_version_id: "ca-06-v2",
    agent_kind: "endpoint",
    source: "vonage_sip",
    version_count: 3,
    environment_count: 1,
    environments: [{ id: "env-09", name: "Late payments" }],
  },
  {
    id: "ca-07",
    agent_name: "Video intake",
    agent_type: "video_call",
    inbound: true,
    description: "Collects claim details over a video call",
    created_at: "2026-10-12T12:00:00Z",
    latest_version: null,
    latest_version_id: null,
    agent_kind: null,
    source: null,
    version_count: 0,
    environment_count: 0,
    environments: [],
  },
];

const SOURCES = ["vapi", "retell", "livekit", "github", "archive"];

// Thirty more plain rows so the list spans two pages at the default size.
const GENERATED = Array.from({ length: 30 }, (_, i) => {
  const n = i + 8;
  const voice = i % 3 !== 0;
  const versions = (i % 4) + 1;
  const envs = i % 5;
  const source = SOURCES[i % SOURCES.length];
  return {
    id: `ca-${String(n).padStart(2, "0")}`,
    agent_name: `${voice ? "Voice" : "Chat"} agent ${n}`,
    agent_type: voice ? "voice" : "text",
    inbound: i % 2 === 0,
    description: i % 6 === 5 ? null : `Handles support requests for team ${n}`,
    created_at: new Date(Date.UTC(2026, 7, 1 + i, 10, 0)).toISOString(),
    latest_version: versions,
    latest_version_id: `ca-${n}-v${versions}`,
    agent_kind: source === "github" || source === "archive" ? "source" : null,
    source,
    version_count: versions,
    environment_count: envs,
    environments: Array.from({ length: envs }, (__, k) => ({
      id: `env-${n}-${k}`,
      name: `Environment ${n}.${k + 1}`,
    })),
  };
});

export const CONNECTED_AGENTS_FIXTURE = [...HAND_WRITTEN, ...GENERATED];

// Serves the fixture like the endpoint: 1-based page, `limit`, `search` over
// name and description, and the paginated envelope.
export const connectedAgentsPage = ({ page = 1, limit = 10, search = "" }) => {
  const q = search.trim().toLowerCase();
  const matching = CONNECTED_AGENTS_FIXTURE.filter(
    (a) =>
      !q || `${a.agent_name} ${a.description || ""}`.toLowerCase().includes(q),
  );
  const start = (page - 1) * limit;
  return {
    count: matching.length,
    next: null,
    previous: null,
    total_pages: Math.max(1, Math.ceil(matching.length / limit)),
    current_page: page,
    results: structuredClone(matching.slice(start, start + limit)),
  };
};
