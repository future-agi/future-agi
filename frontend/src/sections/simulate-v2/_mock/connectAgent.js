/**
 * What the New agent flow reads before anything is saved — the account's
 * assistants on a hosted platform, a repository's branches and folders,
 * what a folder holds, what a framework adapter describes — and the pieces
 * a Built-in agent is assembled from.
 * Deterministic, so the demo lands on the same answers every time.
 */
import { DEFAULT_LIMITS } from "./optimizationRuns";
import { FRAMEWORK_META, frameworkIdOf } from "./agentRegistry";
import { FRAMEWORK_TUNING } from "./agentLifecycle";

const hash = (s) => {
  let h = 0;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1)
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
};

/* ── hosted platform: the account's assistants ───────────────────────────── */

/*
  What listing assistants with the key returns. A voice assistant has the
  number it answers on; the id is the one the platform's dashboard shows.
*/
const ASSISTANTS = {
  vapi: [
    {
      id: "asst_7c1e9a42-returns",
      name: "Returns line",
      updated: "2 hours ago",
      phone: "+1 415 555 0142",
      model: "gpt-4o",
    },
    /* A squad: the entry assistant hands the caller to the others. */
    {
      id: "asst_2b8f0d17-billing",
      name: "Billing support",
      updated: "yesterday",
      phone: "+1 415 555 0177",
      model: "gpt-4.1",
      squad: "Billing squad",
      members: [
        {
          id: "billing-greeter",
          name: "Billing greeter",
          role: "Entry · routes the caller",
          model: "gpt-4.1",
          promptRef: "Billing greeter · system prompt",
        },
        {
          id: "payments",
          name: "Payments specialist",
          role: "Takes failed payments and refunds",
          model: "gpt-4.1",
          promptRef: "Payments specialist · system prompt",
        },
        {
          id: "disputes",
          name: "Disputes specialist",
          role: "Opens and tracks chargebacks",
          model: "gpt-4o",
          promptRef: "Disputes specialist · system prompt",
        },
      ],
    },
    {
      id: "asst_91aa3c05-booking",
      name: "Appointment booking",
      updated: "4 days ago",
      phone: "+1 628 555 0110",
      model: "gpt-4o",
    },
    {
      id: "asst_0d44e6b8-outbound",
      name: "Renewal reminders (outbound)",
      updated: "2 weeks ago",
      phone: null,
      model: "gpt-4o-mini",
    },
  ],
  retell: [
    /* Retell agents are one of two kinds — a single prompt, or a
       conversation flow of nodes, each with its own instructions. Voice
       and chat agents are separate agents on Retell. */
    {
      id: "agent_5f20c1d9e7",
      name: "Front desk",
      updated: "3 hours ago",
      phone: "+1 650 555 0131",
      model: "gpt-4o",
      retellKind: "single_prompt",
      channels: ["voice"],
    },
    {
      id: "agent_chat_3b7e10a4",
      name: "Website chat",
      updated: "yesterday",
      phone: null,
      model: "gpt-4.1",
      retellKind: "single_prompt",
      channels: ["chat"],
    },
    {
      id: "agent_chat_9d02c6f1",
      name: "Claims chat",
      updated: "4 days ago",
      phone: null,
      model: "claude-sonnet-4",
      channels: ["chat"],
      retellKind: "conversation_flow",
      members: [
        {
          id: "welcome",
          name: "Welcome",
          role: "Start node · finds the policy",
          model: "claude-sonnet-4",
          promptRef: "Node: Welcome",
        },
        {
          id: "status",
          name: "Claim status",
          role: "Calls get_claim, explains where it stands",
          model: "claude-sonnet-4",
          promptRef: "Node: Claim status",
        },
      ],
    },
    {
      id: "agent_a81b44f302",
      name: "Claims intake",
      updated: "6 days ago",
      phone: "+1 650 555 0199",
      model: "claude-sonnet-4",
      channels: ["voice"],
      retellKind: "conversation_flow",
      members: [
        {
          id: "welcome",
          name: "Welcome",
          role: "Start node · confirms the policy holder",
          model: "claude-sonnet-4",
          promptRef: "Node: Welcome",
        },
        {
          id: "incident",
          name: "Incident details",
          role: "Collects what happened and when",
          model: "claude-sonnet-4",
          promptRef: "Node: Incident details",
        },
        {
          id: "coverage",
          name: "Coverage check",
          role: "Calls check_coverage, explains the outcome",
          model: "claude-sonnet-4",
          promptRef: "Node: Coverage check",
        },
        {
          id: "handoff",
          name: "Adjuster handoff",
          role: "Transfers to a human adjuster",
          model: "claude-sonnet-4",
          promptRef: "Node: Adjuster handoff",
        },
      ],
    },
  ],
  bland: [
    /* Bland's new agent model — ids are agent ids, not pathway ids. */
    {
      id: "agent_3e9d71",
      name: "Lead qualification",
      updated: "yesterday",
      phone: "+1 312 555 0164",
      model: "gpt-4o",
      channels: ["voice"],
    },
    {
      id: "agent_c04a28",
      name: "Delivery rescheduling",
      updated: "last week",
      phone: "+1 312 555 0108",
      model: "gpt-4o",
    },
    /* A messaging agent — texts rather than calls. */
    {
      id: "agent_m7f15b",
      name: "Appointment reminders (SMS)",
      updated: "2 days ago",
      phone: "+1 312 555 0152",
      model: "gpt-4o-mini",
      channels: ["chat"],
    },
  ],
  elevenlabs: [
    /* A multi-node workflow: each node its own instructions. */
    {
      id: "agent_01jx8k2m4n",
      name: "Concierge",
      updated: "5 hours ago",
      phone: "+44 20 7946 0811",
      model: "gemini-2.5-pro",
      members: [
        {
          id: "concierge",
          name: "Concierge",
          role: "Start node · greets and routes",
          model: "gemini-2.5-pro",
          promptRef: "Workflow node: Concierge",
        },
        {
          id: "reservations",
          name: "Reservations",
          role: "Books and changes tables",
          model: "gemini-2.5-pro",
          promptRef: "Workflow node: Reservations",
        },
        {
          id: "events",
          name: "Private events",
          role: "Takes event enquiries",
          model: "gpt-4o",
          promptRef: "Workflow node: Private events",
        },
      ],
    },
    {
      id: "agent_01jw3p9q7r",
      name: "Order status",
      updated: "3 days ago",
      phone: null,
      model: "gpt-4o",
    },
    /* Chat only — reached on WhatsApp as well as web chat. */
    {
      id: "agent_01jy5t2w8x",
      name: "WhatsApp concierge",
      updated: "6 hours ago",
      phone: "+44 7700 900412",
      model: "gpt-4.1",
      channels: ["chat"],
    },
  ],
  livekit: [
    {
      id: "returns-line-agent",
      name: "returns-line-agent",
      updated: "1 hour ago",
      phone: "+1 415 555 0190",
      model: "gpt-4o",
    },
    {
      id: "triage-agent",
      name: "triage-agent",
      updated: "2 days ago",
      phone: null,
      model: "gpt-4.1",
    },
  ],
  /* Platforms with their full configuration behind an API; a change ships
     as a draft version (rsiModel PLATFORM_TESTING). */
  telnyx: [
    {
      id: "assistant-7c1e9a42-ba0d",
      name: "Clinic scheduling",
      updated: "1 hour ago",
      phone: "+1 512 555 0123",
      model: "gpt-4o",
      channels: ["voice"],
    },
    {
      id: "assistant-1f0e33c8-77aa",
      name: "Order status line",
      updated: "3 days ago",
      phone: "+1 512 555 0186",
      model: "gpt-4o-mini",
      channels: ["voice"],
    },
  ],
  google_cx: [
    {
      id: "projects/acme-support/locations/global/agents/5f2c1d9e",
      name: "Account support",
      updated: "yesterday",
      phone: "+1 650 555 0144",
      model: "gemini-2.5-flash",
      members: [
        {
          id: "start",
          name: "Default Start Flow",
          role: "Start · greets and routes",
          model: "gemini-2.5-flash",
          promptRef: "Flow: Default Start Flow",
        },
        {
          id: "billing",
          name: "Billing",
          role: "Explains charges, takes disputes",
          model: "gemini-2.5-flash",
          promptRef: "Flow: Billing",
        },
        {
          id: "password",
          name: "Password reset",
          role: "Verifies and resets",
          model: "gemini-2.5-flash",
          promptRef: "Flow: Password reset",
        },
      ],
    },
    {
      id: "projects/acme-support/locations/global/agents/a81b44f3",
      name: "Store locator",
      updated: "last week",
      phone: null,
      model: "gemini-2.5-flash",
      channels: ["chat"],
    },
  ],
  voiceflow: [
    {
      id: "66f2c1d9e7a0b1c2",
      name: "Booking assistant",
      updated: "2 hours ago",
      phone: "+1 312 555 0119",
      model: "gpt-4o",
      members: [
        {
          id: "book",
          name: "Book a table",
          role: "Workflow · takes the booking",
          model: "gpt-4o",
          promptRef: "Workflow: Book a table",
        },
        {
          id: "change",
          name: "Change a booking",
          role: "Workflow · moves or cancels",
          model: "gpt-4o",
          promptRef: "Workflow: Change a booking",
        },
      ],
    },
    {
      id: "66e0aa13b2c4d5e6",
      name: "Help-centre widget",
      updated: "5 days ago",
      phone: null,
      model: "claude-sonnet-4",
      channels: ["chat"],
    },
  ],
  agentforce: [
    {
      id: "Service_Agent",
      name: "Service Agent",
      updated: "4 hours ago",
      phone: null,
      model: "gpt-4o",
      channels: ["chat"],
      members: [
        {
          id: "orders",
          name: "Order Inquiries",
          role: "Topic · order status and changes",
          model: "gpt-4o",
          promptRef: "Topic: Order Inquiries",
        },
        {
          id: "cases",
          name: "Case Management",
          role: "Topic · opens and updates cases",
          model: "gpt-4o",
          promptRef: "Topic: Case Management",
        },
        {
          id: "escalation",
          name: "Escalation",
          role: "Topic · hands over to a rep",
          model: "gpt-4o",
          promptRef: "Topic: Escalation",
        },
      ],
    },
    {
      id: "Returns_Agent",
      name: "Returns Agent",
      updated: "last week",
      phone: null,
      model: "gpt-4o",
      channels: ["chat"],
    },
  ],
  copilot_studio: [
    {
      id: "cr4f2_itHelpDesk",
      name: "IT help desk",
      updated: "yesterday",
      phone: null,
      model: "gpt-4o",
      channels: ["chat"],
      members: [
        {
          id: "reset",
          name: "Reset password",
          role: "Topic · verifies and resets",
          model: "gpt-4o",
          promptRef: "Topic: Reset password",
        },
        {
          id: "device",
          name: "Device request",
          role: "Topic · orders a laptop or phone",
          model: "gpt-4o",
          promptRef: "Topic: Device request",
        },
      ],
    },
    {
      id: "cr4f2_hrPolicies",
      name: "HR policies",
      updated: "2 weeks ago",
      phone: null,
      model: "gpt-4o",
      channels: ["chat"],
    },
  ],
};

/** The assistants the account can see with the key it gave us — for one
    agent type, the ones that serve it (an untagged one serves both). */
export const assistantsFor = (provider, agentType = null) =>
  (ASSISTANTS[provider] || []).filter(
    (a) => !agentType || !a.channels || a.channels.includes(agentType),
  );

/*
  What importing the picked assistant brings with it beyond its config:
    members    — a Vapi squad, a Retell conversation flow or an ElevenLabs
                 workflow is one agent made of several (name, role, model,
                 prompt ref) → definition.members
    retellKind — Retell only: "single_prompt" | "conversation_flow"
    structure  — the line said under the table
*/
export const importedStructureOf = (provider, assistant) => {
  if (!assistant) return { members: [], retellKind: null, structure: null };
  const members = (assistant.members || []).map((m) => ({
    ...m,
    instructions: m.promptRef,
  }));
  const retellKind =
    provider === "retell" ? assistant.retellKind || "single_prompt" : null;
  /* What each platform calls the parts of one agent. */
  const parts = {
    google_cx: "Flows",
    voiceflow: "Workflows",
    agentforce: "Topics",
    copilot_studio: "Topics",
  }[provider];
  const structure =
    provider === "vapi" && assistant.squad
      ? `Squad · ${members.length} assistants`
      : provider === "retell"
        ? retellKind === "conversation_flow"
          ? `Conversation flow · ${members.length} nodes`
          : "Single prompt agent"
        : members.length
          ? parts
            ? `${parts} · ${members.length}`
            : `Workflow · ${members.length} nodes`
          : null;
  return { members, retellKind, structure };
};

/* Hosted platforms whose agents can be imported as a sealed copy: the
   assistant's config into our sandbox, with the tool code uploaded. */
export const SANDBOX_IMPORT_PROVIDERS = ["vapi", "retell"];
/* RSI PRD: "Vapi, Retell and Bland agents created with call logs switched on
   are linked already" — their call logs come through the same key. Any other
   platform's Observe project is chosen in Finish setting up. */
export const CALL_LOG_PROVIDERS = ["vapi", "retell", "bland"];

/* ── Observe projects (where an agent's traces land) ─────────────────────── */

/*
  The workspace's Observe projects — the same ids the Error Feed's issues
  carry (`project_id`), plus a couple nothing is linked to yet. Linking a
  project to an agent at connect time is what lets Fix find the agent for
  every issue from it.
*/
/*
  `traced` — how each project's production data arrives, read from the
  traces themselves (RSI PRD, Agents: "Vapi, Retell and Bland agents created
  with call logs switched on are linked already … code agents that send
  traces through traceAI are not"). An unlinked project uses it to connect
  the right agent without the full catalogue:
    traceai   — spans from the agent's code: framework, language, model, tools
    call-logs — a platform's call logs, pulled by Observe's own integration:
                provider, the assistant's ID and name, and the key that
                integration holds
*/
export const OBSERVE_PROJECTS = [
  {
    id: "returns-refunds-line-agent",
    name: "Returns & refunds line",
    traces: "2,140 traces · last 12 min ago",
    issues: 3,
  },
  {
    id: "customer-support-line-agent",
    name: "Customer support line",
    traces: "5,812 traces · last 3 min ago",
    issues: 6,
  },
  {
    id: "airline-rebooking",
    name: "Airline rebooking",
    traces: "1,204 traces · last 20 min ago",
    issues: 2,
  },
  {
    id: "guest-booking-line",
    name: "Guest booking line",
    traces: "3,377 traces · last 6 min ago",
    issues: 3,
  },
  {
    id: "clinic-appointment-line",
    name: "Clinic appointment line",
    traces: "1,486 traces · last 9 min ago",
    issues: 2,
  },
  {
    id: "pickup-dispatch",
    name: "Pickup dispatch",
    traces: "918 traces · last 1 hour ago",
    issues: 3,
    traced: {
      via: "traceai",
      framework: "langgraph",
      language: "Python",
      model: "gpt-4.1",
      channel: "chat",
      tools: ["book_pickup", "update_pickup_address", "quote_fare"],
      service: "pickup-dispatch",
    },
  },
  {
    id: "billing-copilot",
    name: "Billing copilot",
    traces: "406 traces · last yesterday",
    issues: 1,
  },
  {
    id: "rebooking-assistant",
    name: "Rebooking assistant",
    traces: "233 traces · last 26 min ago",
    issues: 1,
  },
  {
    id: "travel-rebooking-chat",
    name: "Travel rebooking chat",
    traces: "77 traces · last 48 min ago",
    issues: 1,
    traced: {
      via: "call-logs",
      provider: "vapi",
      agentType: "chat",
      agentId: "asst_trc_51q8",
      agentName: "Travel rebooking assistant",
      integration: "Observe → Vapi integration",
      keyRef: "vault://acme/observe/vapi_api_key",
    },
  },
  {
    id: "storefront-chat",
    name: "Storefront chat",
    traces: "412 traces · last 44 min ago",
    issues: 1,
    traced: {
      via: "call-logs",
      provider: "vapi",
      agentType: "chat",
      agentId: "asst_sf_2m8x",
      agentName: "Storefront assistant",
      integration: "Observe → Vapi integration",
      keyRef: "vault://acme/observe/vapi_api_key",
    },
  },
];
/*
  Real Tracing projects (the same list the Tracing page reads), remembered by
  ID so a project picked from it keeps its name everywhere — Connect traces,
  the agent's link, Observe's Create environment test. Kept in this browser.
*/
const TRACING_KEY = "fagi.simulate.tracingProjects";
const readTracing = () => {
  try {
    return JSON.parse(window.localStorage.getItem(TRACING_KEY) || "{}") || {};
  } catch {
    return {};
  }
};
let tracingProjects = readTracing();
export const rememberTracingProjects = (rows = []) => {
  const next = { ...tracingProjects };
  rows.forEach((r) => {
    if (r?.id == null || !r?.name) return;
    next[String(r.id)] = { name: r.name, lastActive: r.last_active || null };
  });
  tracingProjects = next;
  try {
    window.localStorage.setItem(TRACING_KEY, JSON.stringify(next));
  } catch {
    /* storage blocked: names last for this visit */
  }
};
export const tracingProjectIds = () => Object.keys(tracingProjects);
export const observeProjectById = (id) => {
  const sample = OBSERVE_PROJECTS.find((p) => p.id === id);
  if (sample) return sample;
  const real = tracingProjects[String(id)];
  return real
    ? {
        id: String(id),
        name: real.name,
        traces: "In Tracing",
        issues: null,
        tracing: true,
      }
    : null;
};
/** The project a new agent gets when none is picked — named after it. */
export const observeSlugOf = (name) =>
  String(name || "agent")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "") || "agent";

/**
 * The Observe project a source ends up linked to, or null:
 *   · what the Observe step picked ({ mode: "existing" | "create", id })
 *   · a Vapi, Retell or Bland agent watched through its call logs: the
 *     project it came from (Fix → Connect a new agent), else one named after it
 */
export const observeChoiceOf = (source, prefillProject) => {
  if (source?.observeProject?.id) return source.observeProject;
  if (
    source?.kind === "platform" &&
    source?.agentMode !== "prompt" &&
    source?.observability &&
    CALL_LOG_PROVIDERS.includes(source?.provider)
  ) {
    return prefillProject
      ? { mode: "existing", id: prefillProject }
      : { mode: "create", id: observeSlugOf(source.name || source.agentId) };
  }
  return null;
};

/* ── who it is and how it's reached (the confirm step) ───────────────────── */

export const LANGUAGE_OPTIONS = [
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "pt", label: "Portuguese" },
  { code: "hi", label: "Hindi" },
  { code: "ja", label: "Japanese" },
  { code: "ar", label: "Arabic" },
];

/* Reach defaults: an inbound line the caller opens, four calls at once; a
   chat the user opens, capped at 30 turns. */
export const DEFAULT_REACH = {
  direction: "inbound",
  whoSpeaksFirst: "agent",
  maxConcurrent: 4,
  whoWritesFirst: "user",
  turnLimit: 30,
};

export const emptyProfile = (owner = "") => ({
  owner,
  languages: ["en"],
  description: "",
  channels: null,
  reach: { ...DEFAULT_REACH },
});

/**
 * The Observe step's answer, settled: what was picked, else the project the
 * "Connect a new agent" link (in Fix) came from, else a new one named after the
 * agent. A new project's id follows the agent's name until saved.
 */
export const resolveObserve = (observe, prefillProject, agentName) => {
  if (observe?.mode === "existing" && observe.id) return observe;
  if (observe?.mode === "create")
    return { mode: "create", id: observeSlugOf(observe.id || agentName) };
  if (prefillProject) return { mode: "existing", id: prefillProject };
  return { mode: "create", id: observeSlugOf(agentName) };
};

/**
 * The confirm step's answers as source fields — only the reach keys that
 * belong to the chosen channels. `flowReach` is what the flow itself asked
 * (a hosted voice agent's direction and who speaks first) and wins.
 */
export const profileSourceOf = (
  profile,
  { defaultChannel = "chat", owner, flowReach } = {},
) => {
  const p = profile || emptyProfile();
  const channels = p.channels?.length ? p.channels : [defaultChannel];
  const r = { ...DEFAULT_REACH, ...(p.reach || {}) };
  const num = (x, d) => (Number(x) > 0 ? Math.round(Number(x)) : d);
  const reach = {
    ...(channels.includes("voice") && {
      direction: r.direction,
      whoSpeaksFirst: r.whoSpeaksFirst,
      maxConcurrent: num(r.maxConcurrent, DEFAULT_REACH.maxConcurrent),
    }),
    ...(channels.includes("chat") && {
      whoWritesFirst: r.whoWritesFirst,
      turnLimit: num(r.turnLimit, DEFAULT_REACH.turnLimit),
    }),
    ...(channels.includes("voice") ? flowReach || {} : {}),
  };
  return {
    owner: (p.owner || "").trim() || owner || "",
    languages: p.languages?.length ? p.languages : ["en"],
    ...((p.description || "").trim() && { description: p.description.trim() }),
    channels,
    reach,
  };
};

/* ── repository: branches, folders, what a folder holds ──────────────────── */

const dirOf = (path) =>
  String(path || "")
    .split(":")[0]
    .split("/")
    .slice(0, -1)
    .join("/") || ".";

export const repoBranches = (repo) => {
  const extra =
    hash(repo?.name) % 2 ? ["feat/handoff-rules"] : ["fix/refund-wording"];
  return ["main", "develop", "release/2026.10", ...extra];
};

/**
 * The folders worth pointing at: the ones the scan found an agent in first,
 * then the rest of the top-level code folders, which hold none.
 */
export const repoFolders = (repo) => {
  /* One row per folder — a folder can hold several entry points. */
  const byPath = new Map();
  (repo?.agents || []).forEach((a) => {
    const path = dirOf(a.entry);
    byPath.set(path, [...(byPath.get(path) || []), a]);
  });
  const found = [...byPath.entries()].map(([path, agents]) => ({
    path,
    agent: agents[0],
    agents,
  }));
  const seen = new Set(found.map((f) => f.path));
  const rest = (
    repo?.language === "TypeScript"
      ? ["src", "scripts"]
      : ["app", "scripts", "tests"]
  )
    .filter((p) => !seen.has(p))
    .map((path) => ({ path, agent: null }));
  return [...found, ...rest];
};

const TOOL_POOLS = {
  dispatch: ["book_pickup", "update_pickup_address", "quote_fare"],
  returns: [
    "lookup_order",
    "shipment_status",
    "issue_refund",
    "escalate_to_human",
    "check_return_eligibility",
    "generate_return_label",
    "cancel_order",
    "update_shipping_address",
  ],
  billing: [
    "verify_identity",
    "list_transactions",
    "freeze_card",
    "raise_dispute",
    "issue_refund",
    "escalate_to_human",
  ],
  travel: [
    "lookup_booking",
    "search_flights",
    "rebook_segment",
    "check_compensation",
    "issue_voucher",
  ],
  research: ["web_search", "fetch_page", "find_company", "cite"],
};
const poolFor = (text) => {
  const t = String(text || "").toLowerCase();
  if (/pickup|dispatch/.test(t)) return TOOL_POOLS.dispatch;
  if (/bill|disput|card|payment/.test(t)) return TOOL_POOLS.billing;
  if (/travel|rebook|flight/.test(t)) return TOOL_POOLS.travel;
  if (/research|notebook/.test(t)) return TOOL_POOLS.research;
  return TOOL_POOLS.returns;
};

const entryFile = (entry) => String(entry).split(":")[0];
const pyOrTs = (entry) =>
  entryFile(entry).endsWith(".ts")
    ? `npx tsx ${entryFile(entry)}`
    : `python ${entryFile(entry)}`;
const RUNNERS = {
  "LiveKit Agents": (entry) => `python ${entryFile(entry)} start`,
  LangGraph: () => "langgraph up",
  "OpenAI Agents SDK": pyOrTs,
  /* No framework: the code calls the model API itself. */
  "Direct API calls": pyOrTs,
  Pipecat: (entry) => `python ${entryFile(entry)} --transport webrtc`,
  "Google ADK": (entry) =>
    `adk api_server ${entryFile(entry).split("/").slice(0, -1).join("/") || "."}`,
  Mastra: () => "npx mastra dev",
  "Vercel AI SDK": () => "npm run dev",
  CrewAI: () => "crewai run",
  "Claude Agent SDK": pyOrTs,
};

/**
 * What reading one folder of a repository at a branch finds — the facts the
 * person confirms or corrects before anything is saved. A folder the scan
 * found no agent in still gets a best guess; every guess says it's one.
 */
export const readAgentFolder = ({ repo, folder, branch, entryId }) => {
  const here = (repo?.agents || []).filter((a) => dirOf(a.entry) === folder);
  const agent = here.find((a) => a.id === entryId) || here[0] || null;
  const seed = hash(`${repo?.name || repo}:${folder}:${branch}`);
  const ts = repo?.language === "TypeScript";
  const entry = agent?.entry || `${folder}/${ts ? "index.ts" : "agent.py"}`;
  /* A folder the scan found no framework in: the generic path — code that
     calls the model API directly, started where the person says. */
  const framework = agent?.framework || "Direct API calls";
  const tools = poolFor(`${repo?.name || ""} ${folder}`).slice(
    0,
    agent?.tools || 3 + (seed % 2),
  );
  return {
    name:
      agent?.name ||
      folder
        .split("/")
        .pop()
        .replace(/[-_]/g, " ")
        .replace(/^./, (c) => c.toUpperCase()),
    detected: !!agent,
    framework,
    facts: {
      entry: {
        value: entry,
        from: agent ? "Agent constructor" : "Guessed from the folder",
      },
      prompts: {
        value: agent?.prompts || [`${folder}/prompts/system.md`],
        from: agent ? "Loaded by the entry file" : "Guessed from the folder",
      },
      tools: {
        value: tools,
        from: `${tools.length} functions registered as tools`,
      },
      model: {
        value:
          agent?.model || ["gpt-4o", "gpt-4.1", "claude-sonnet-4"][seed % 3],
        from: "Model argument in the entry file",
      },
      run: {
        value: (RUNNERS[framework] || pyOrTs)(entry),
        from: `${framework} · from ${ts ? "package.json" : "pyproject"} and the README`,
      },
    },
  };
};

/* ── detected vs confirmed: the rest of what reading the code finds ──────── */

const KEY_POOLS = {
  returns: ["SHOPIFY_ADMIN_TOKEN", "STRIPE_KEY"],
  billing: ["STRIPE_KEY", "PLAID_SECRET"],
  travel: ["AMADEUS_API_KEY"],
  research: ["SERPAPI_KEY"],
};
const keyPoolFor = (text) => {
  const t = String(text || "").toLowerCase();
  if (/bill|disput|card|payment/.test(t)) return KEY_POOLS.billing;
  if (/travel|rebook|flight/.test(t)) return KEY_POOLS.travel;
  if (/research|notebook/.test(t)) return KEY_POOLS.research;
  return KEY_POOLS.returns;
};
const promptSlugFor = (text) => {
  const t = String(text || "").toLowerCase();
  if (/bill|disput|card|payment/.test(t)) return "billing-tone";
  if (/travel|rebook|flight/.test(t)) return "rebooking-rules";
  if (/research|notebook/.test(t)) return "research-brief";
  if (/triage/.test(t)) return "triage-routing";
  return "returns-system";
};

/**
 * What else reading the folder found, each with where it came from:
 *   language, install      — how it's built
 *   entryPoints            — every agent in the folder; several → pick one
 *   runtimePrompts         — prompts built at run time or loaded from outside
 *                            the repository; each needs "point us at it"
 *   modelAtRuntime         — the model comes from config or env, not code
 *   credentialKeys         — the key NAMES it reads (never values), each to be
 *                            mapped to a secret-manager reference
 *   fagiPrompts            — Future AGI prompts it loads by id
 *   members                — agents it hands off between (OpenAI Agents SDK)
 */
export const repoDetectionOf = ({ repo, folder, branch, found }) => {
  const ts = repo?.language === "TypeScript";
  const seed = hash(`${repo?.name || repo}:${folder}:${branch}`);
  const framework = found?.framework || "Direct API calls";
  const here = (repo?.agents || []).filter((a) => dirOf(a.entry) === folder);
  const entry = found?.facts?.entry?.value || `${folder}/agent.py`;
  const livekit = /livekit/i.test(framework);
  const model = found?.facts?.model?.value || "gpt-4o";
  const keys = [
    /claude|anthropic/i.test(`${framework} ${model}`)
      ? "ANTHROPIC_API_KEY"
      : /gemini|adk/i.test(`${framework} ${model}`)
        ? "GOOGLE_API_KEY"
        : ts
          ? "OPENAI_API_KEY"
          : framework === "LangGraph" && seed % 2
            ? "ANTHROPIC_API_KEY"
            : "OPENAI_API_KEY",
    ...(livekit
      ? ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DEEPGRAM_API_KEY"]
      : []),
    ...keyPoolFor(`${repo?.name || ""} ${folder}`),
  ];
  const slug = promptSlugFor(`${repo?.name || ""} ${folder}`);
  /* A LangGraph app usually takes its model from config. */
  const runtimeModel = framework === "LangGraph";
  const file = entry.split(":")[0];
  const handoffs = /openai agents/i.test(framework);
  return {
    language: {
      value: ts ? "TypeScript · Node 20" : "Python 3.11",
      from: ts ? "package.json engines" : "pyproject.toml requires-python",
    },
    install: {
      value: ts
        ? "npm ci"
        : livekit
          ? "uv sync"
          : "pip install -r requirements.txt",
      from: ts ? "package-lock.json" : livekit ? "uv.lock" : "requirements.txt",
    },
    entryPoints: here.map((a) => ({
      id: a.id,
      name: a.name,
      entry: a.entry,
      framework: a.framework,
    })),
    runtimePrompts: [
      {
        id: "built",
        kind: "built",
        what: "Greeting assembled per call from the customer's account tier",
        where: `${file} · build_greeting()`,
      },
      ...(seed % 2 === 0
        ? [
            {
              id: "external",
              kind: "external",
              what: "Policy text fetched from your CMS when the call starts",
              where: `${file} · load_policy()`,
            },
          ]
        : []),
    ],
    modelAtRuntime: {
      value: runtimeModel,
      from: runtimeModel
        ? `model=os.environ.get("AGENT_MODEL", "${model}") in ${file}`
        : `Model hard-coded in ${file}`,
    },
    credentialKeys: {
      value: [...new Set(keys)],
      from: `${ts ? ".env.example" : ".env.example and os.environ reads"} · names only, we never read the values`,
    },
    fagiPrompts: {
      value: [
        {
          promptId: `pr_${slug.replace(/-/g, "_")}`,
          name: slug,
          version: `v${3 + (seed % 6)}`,
        },
        ...(seed % 2
          ? [
              {
                promptId: "pr_escalation_rules",
                name: "escalation-rules",
                version: "v2",
              },
            ]
          : []),
      ],
      from: `fagi.prompts.get(…) in ${file}`,
    },
    members: handoffs
      ? [
          {
            id: "triage",
            name: "triage_agent",
            role: "Entry · hands off",
            model,
            promptRef: `${folder}/prompts/triage.md`,
          },
          {
            id: "specialist",
            name: `${(found?.name || "specialist")
              .toLowerCase()
              .replace(/[^a-z]+/g, "_")
              .replace(/(^_|_$)/g, "")}_agent`,
            role: "Does the work",
            model,
            promptRef:
              (found?.facts?.prompts?.value || [])[0] ||
              `${folder}/prompts/system.md`,
          },
          {
            id: "escalation",
            name: "escalation_agent",
            role: "Hands over to a person",
            model: "gpt-4o-mini",
            promptRef: `${folder}/prompts/escalation.md`,
          },
        ]
      : [],
  };
};

/** Members of a framework agent made of several agents, from what the adapter described. */
export const frameworkMembersOf = (framework, structure, model) => {
  /* Several agents working as one; a single agent is just the agent. */
  if (!structure?.agents || structure.agents.length < 2) return [];
  return structure.agents.map((a, i) => ({
    id: a.name,
    name: a.name,
    role:
      i === 0
        ? `Entry${a.handoffs.length ? ` · hands off to ${a.handoffs.join(", ")}` : ""}`
        : a.handoffs.length
          ? `Hands off to ${a.handoffs.join(", ")}`
          : `${(a.tools || []).length} tools`,
    model: model || "gpt-4.1",
    promptRef: a.instructions,
    instructions: a.instructions,
    tools: (a.tools || []).map((name) => ({ name, desc: "", args: [] })),
  }));
};

/* ── what must hold before a code agent is simulated ─────────────────────── */

/* Tools that look like they write data or call outside services. */
const WRITES =
  /(^|_)(create|update|issue|refund|cancel|send|book|charge|write|delete|post|transfer|apply|modify|rebook|freeze|raise|generate|escalate|voucher)(_|$)/;

/**
 * The PRD's remaining "What we found" rows for a code agent:
 *   sideEffects — tools that look like they write data or call outside
 *                 services; the person ticks which must never run for real
 *                 in a simulation (they're stubbed)
 *   tests       — the repository's own test command and checks; every code
 *                 candidate must keep them passing
 *   credentials — the test credentials to use; production secrets are never read
 */
export const repoChecksOf = ({ repo, folder, tools = [] }) => {
  const ts = repo?.language === "TypeScript";
  const seed = hash(`${repo?.name || repo}:${folder}`);
  const sideEffects = tools.filter((t) => WRITES.test(String(t).toLowerCase()));
  const command = ts ? "npm test" : "pytest tests/";
  const checks = ts
    ? ["npm run lint", "tsc --noEmit"]
    : ["ruff check .", ...(seed % 2 ? ["mypy ."] : [])];
  return {
    sideEffects: {
      value: sideEffects,
      from: sideEffects.length
        ? `${sideEffects.length} of ${tools.length} tools look like they write data or call outside services`
        : "No tool looks like it writes data or calls an outside service",
    },
    tests: {
      value: command,
      checks,
      from: ts
        ? "From package.json scripts and .github/workflows/ci.yml"
        : "From pyproject.toml and .github/workflows/ci.yml",
    },
    credentials: {
      value: ts ? ".env.test" : "tests/.env.test",
      from: "Test credentials found next to the tests — we never read production secrets",
    },
  };
};

/* ── framework adapters ───────────────────────────────────────────────────── */

/*
  The adapter is one package extra and two lines around the object the team
  already has — a compiled graph, the entry agent, a crew, a pipeline.
  serve() exposes it at /fagi and answers two calls: "describe" (what's on
  the next step) and "run" (a simulated conversation, in the framework's own
  run loop). Python frameworks take a pip extra; the TypeScript ones an npm
  package.
*/
const py = (extra, wraps, lines, entry) => ({
  install: `pip install "futureagi[${extra}]"`,
  wraps,
  code: [
    ...lines,
    "",
    `app = fagi.serve(${lines.serveArg})  # serves /fagi on :8000`,
  ].join("\n"),
  run: "uvicorn serve:app --port 8000",
  entry,
});
const pyLines = (module, from, what, serveArg) =>
  Object.assign(
    [
      `from futureagi.adapters import ${module} as fagi`,
      `from ${from} import ${what.split(" ")[0]}${what.includes(" ") ? `  # ${what.split(" ").slice(1).join(" ")}` : ""}`,
    ],
    { serveArg },
  );
const ts = (subpath, wraps, importLine, serveArg, entry) => ({
  install: "npm install @futureagi/adapters",
  wraps,
  code: [
    `import { serve } from "@futureagi/adapters/${subpath}";`,
    importLine,
    "",
    `serve(${serveArg}, { port: 8000 }); // serves /fagi`,
  ].join("\n"),
  run: "npx tsx serve.ts",
  entry,
});

export const ADAPTERS = {
  langgraph: {
    install: 'pip install "futureagi[langgraph]"',
    wraps: "your compiled graph",
    code: [
      "from futureagi.adapters import langgraph as fagi",
      "from support.graph import graph  # your compiled StateGraph",
      "",
      "app = fagi.serve(graph)  # serves /fagi on :8000",
    ].join("\n"),
    run: "uvicorn serve:app --port 8000",
    entry: "support/graph.py:graph",
  },
  "openai-agents": {
    install: 'pip install "futureagi[openai-agents]"',
    wraps: "the Agent conversations start at",
    code: [
      "from agents import Runner",
      "from futureagi.adapters import openai_agents as fagi",
      "from support.agents import triage_agent  # the entry Agent",
      "",
      "app = fagi.serve(triage_agent, runner=Runner)  # serves /fagi on :8000",
    ].join("\n"),
    run: "uvicorn serve:app --port 8000",
    entry: "support/agents.py:triage_agent",
  },
  crewai: {
    install: 'pip install "futureagi[crewai]"',
    wraps: "your Crew",
    code: [
      "from futureagi.adapters import crewai as fagi",
      "from support.crew import SupportCrew",
      "",
      "app = fagi.serve(SupportCrew().crew())  # serves /fagi on :8000",
    ].join("\n"),
    run: "uvicorn serve:app --port 8000",
    entry: "support/crew.py:SupportCrew",
  },
  "claude-agent-sdk": py(
    "claude-agent-sdk",
    "your agent's options",
    pyLines(
      "claude_agent_sdk",
      "support.agent",
      "options your ClaudeAgentOptions: system prompt, tools, subagents",
      "options",
    ),
    "support/agent.py:options",
  ),
  livekit: {
    ...py(
      "livekit",
      "your agent's entrypoint",
      pyLines(
        "livekit",
        "agent",
        "entrypoint your AgentSession entrypoint",
        "entrypoint",
      ),
      "agent.py:entrypoint",
    ),
    run: "python serve.py  # audio over WebRTC",
  },
  pipecat: {
    ...py(
      "pipecat",
      "your pipeline",
      pyLines(
        "pipecat",
        "bot",
        "build_pipeline returns your Pipeline",
        "build_pipeline",
      ),
      "bot.py:build_pipeline",
    ),
    run: "python serve.py  # audio over WebRTC",
  },
  "google-adk": py(
    "google-adk",
    "your root agent",
    pyLines(
      "google_adk",
      "support.agent",
      "root_agent your root Agent",
      "root_agent",
    ),
    "support/agent.py:root_agent",
  ),
  mastra: ts(
    "mastra",
    "your Mastra agent",
    'import { mastra } from "./src/mastra"; // your Mastra instance',
    'mastra.getAgent("supportAgent")',
    "src/mastra/index.ts:supportAgent",
  ),
  "vercel-ai": ts(
    "ai-sdk",
    "your agent",
    'import { supportAgent } from "./lib/agent"; // your Agent: model, system and tools',
    "supportAgent",
    "lib/agent.ts:supportAgent",
  ),
  strands: py(
    "strands",
    "your Strands agent",
    pyLines("strands", "support.agent", "agent your strands Agent", "agent"),
    "support/agent.py:agent",
  ),
  "pydantic-ai": py(
    "pydantic-ai",
    "your agent",
    pyLines(
      "pydantic_ai",
      "support.agent",
      "support_agent your pydantic_ai Agent",
      "support_agent",
    ),
    "support/agent.py:support_agent",
  ),
  agno: py(
    "agno",
    "your agent or team",
    pyLines(
      "agno",
      "support.team",
      "support_team your Agent or Team",
      "support_team",
    ),
    "support/team.py:support_team",
  ),
  haystack: py(
    "haystack",
    "your pipeline",
    pyLines(
      "haystack",
      "support.pipeline",
      "pipeline your Pipeline or Agent",
      "pipeline",
    ),
    "support/pipeline.py:pipeline",
  ),
  llamaindex: py(
    "llamaindex",
    "your workflow",
    pyLines(
      "llamaindex",
      "support.workflow",
      "SupportAgent your Workflow",
      "SupportAgent()",
    ),
    "support/workflow.py:SupportAgent",
  ),
  smolagents: py(
    "smolagents",
    "your manager agent",
    pyLines("smolagents", "support.agent", "manager your CodeAgent", "manager"),
    "support/agent.py:manager",
  ),
  dspy: py(
    "dspy",
    "your compiled program",
    pyLines(
      "dspy",
      "support.program",
      "program your compiled dspy.Module",
      "program",
    ),
    "support/program.py:program",
  ),
  "microsoft-agent-framework": py(
    "agent-framework",
    "your agent or workflow",
    pyLines(
      "agent_framework",
      "support.app",
      "agent your ChatAgent or Workflow",
      "agent",
    ),
    "support/app.py:agent",
  ),
};

export const ADAPTER_ENDPOINT_PLACEHOLDER = "http://localhost:8000/fagi";

/*
  What "describe" returns per framework — the structure as the framework
  itself holds it, in one of three shapes the agent record keeps:
    graph   steps in order (a LangGraph graph, a Pipecat or Haystack
            pipeline, a LlamaIndex workflow, a DSPy program)
    agents  agents and who they hand to (sub-agents, delegation, managed
            agents, workflows)
    crew    a CrewAI crew
  `from` says where each structural fact was read.
*/
const node = (id, prompt, tools) => ({ id, prompt, ...(tools && { tools }) });
const agt = (name, instructions, handoffs, tools) => ({
  name,
  instructions,
  handoffs,
  tools,
});
const DESCRIBED = {
  langgraph: {
    name: "Support router",
    graph: {
      flow: "router → refund_agent | order_agent → respond",
      nodes: [
        node("router", "prompts/router.md"),
        node("refund_agent", "prompts/refund_agent.md", [
          "lookup_order",
          "check_return_eligibility",
          "issue_refund",
        ]),
        node("order_agent", "prompts/order_agent.md", [
          "lookup_order",
          "shipment_status",
          "update_shipping_address",
        ]),
        node("respond", "prompts/respond.md"),
      ],
      edges: 5,
      conditional: 1,
      from: "the compiled StateGraph",
    },
    tools: [
      "lookup_order",
      "check_return_eligibility",
      "issue_refund",
      "shipment_status",
      "update_shipping_address",
      "escalate_to_human",
    ],
    model: "gpt-4o",
  },
  "openai-agents": {
    name: "Support triage",
    agents: [
      agt(
        "triage_agent",
        "prompts/triage.md",
        ["refunds_agent", "orders_agent"],
        [],
      ),
      agt(
        "refunds_agent",
        "prompts/refunds.md",
        ["escalation_agent"],
        ["lookup_order", "issue_refund"],
      ),
      agt(
        "orders_agent",
        "prompts/orders.md",
        [],
        ["lookup_order", "shipment_status", "cancel_order"],
      ),
      agt(
        "escalation_agent",
        "prompts/escalation.md",
        [],
        ["escalate_to_human"],
      ),
    ],
    agentsFrom: "Agent objects reachable from the entry",
    handoffsFrom: "handoffs= on each Agent",
    guardrails: ["input · pii_redaction", "output · refund_limit_check"],
    tools: [
      "lookup_order",
      "issue_refund",
      "shipment_status",
      "cancel_order",
      "escalate_to_human",
    ],
    model: "gpt-4.1",
  },
  crewai: {
    name: "Support crew",
    crew: {
      name: "SupportCrew",
      process: "sequential",
      agents: [
        {
          role: "Triage specialist",
          goal: "Work out what the customer needs and route it",
          tools: [],
        },
        {
          role: "Refunds analyst",
          goal: "Decide refunds within policy and issue them",
          tools: ["lookup_order", "check_return_eligibility", "issue_refund"],
        },
        {
          role: "Customer writer",
          goal: "Reply clearly and say exactly what was done",
          tools: [],
        },
      ],
      tasks: [
        { name: "classify_request", agent: "Triage specialist" },
        { name: "resolve_refund", agent: "Refunds analyst" },
        { name: "draft_reply", agent: "Customer writer" },
      ],
    },
    tools: [
      "lookup_order",
      "check_return_eligibility",
      "issue_refund",
      "escalate_to_human",
    ],
    model: "gpt-4o",
  },
  "claude-agent-sdk": {
    name: "Support agent",
    agents: [
      agt(
        "main",
        "CLAUDE.md",
        ["refunds", "orders"],
        ["mcp__shop__lookup_order"],
      ),
      agt(
        "refunds",
        ".claude/agents/refunds.md",
        [],
        ["mcp__shop__lookup_order", "mcp__shop__issue_refund"],
      ),
      agt(
        "orders",
        ".claude/agents/orders.md",
        [],
        ["mcp__shop__lookup_order", "mcp__shop__shipment_status"],
      ),
    ],
    agentsFrom: "ClaudeAgentOptions and the subagents in .claude/agents/",
    handoffsFrom: "Subagents the main agent can hand a task to",
    tools: [
      "mcp__shop__lookup_order",
      "mcp__shop__issue_refund",
      "mcp__shop__shipment_status",
      "mcp__shop__escalate_to_human",
    ],
    model: "claude-sonnet-4",
  },
  livekit: {
    name: "Returns line agent",
    graph: {
      flow: "room audio → stt → llm → tts → room audio",
      nodes: [
        node("stt", null),
        node("llm", "prompts/returns_system.md", [
          "lookup_order",
          "check_return_eligibility",
          "issue_refund",
          "escalate_to_human",
        ]),
        node("tts", null),
      ],
      edges: 4,
      conditional: 0,
      from: "the AgentSession and its Agent",
    },
    tools: [
      "lookup_order",
      "check_return_eligibility",
      "issue_refund",
      "escalate_to_human",
    ],
    model: "gpt-4o",
  },
  pipecat: {
    name: "Concierge bot",
    graph: {
      flow: "transport.input → stt → context → llm → tts → transport.output",
      nodes: [
        node("stt", null),
        node("llm", "flows/concierge_flow.json", [
          "lookup_booking",
          "update_booking",
          "escalate_to_human",
        ]),
        node("tts", null),
      ],
      edges: 5,
      conditional: 0,
      from: "the Pipeline and its Pipecat Flows file",
    },
    tools: ["lookup_booking", "update_booking", "escalate_to_human"],
    model: "gpt-4o",
  },
  "google-adk": {
    name: "Concierge planner",
    agents: [
      agt(
        "root_agent",
        "root_agent.yaml · instruction",
        ["booking_agent", "faq_agent"],
        [],
      ),
      agt(
        "booking_agent",
        "booking_agent.yaml · instruction",
        [],
        ["lookup_booking", "rebook_segment"],
      ),
      agt(
        "faq_agent",
        "faq_agent.yaml · instruction",
        [],
        ["search_knowledge_base"],
      ),
    ],
    agentsFrom: "The root Agent and its sub_agents",
    handoffsFrom: "sub_agents= on each Agent",
    tools: ["lookup_booking", "rebook_segment", "search_knowledge_base"],
    model: "gemini-2.5-pro",
  },
  mastra: {
    name: "Order help",
    agents: [
      agt(
        "supportAgent",
        "src/mastra/agents/support.ts · instructions",
        ["ordersWorkflow"],
        ["lookupOrder"],
      ),
      agt(
        "ordersWorkflow",
        "src/mastra/workflows/orders.ts",
        [],
        ["lookupOrder", "cancelOrder", "shipmentStatus"],
      ),
    ],
    agentsFrom: "Agents and workflows registered on the Mastra instance",
    handoffsFrom: "Workflows the agent can start",
    tools: ["lookupOrder", "cancelOrder", "shipmentStatus"],
    model: "gpt-4.1",
  },
  "vercel-ai": {
    name: "Returns chat",
    agents: [
      agt(
        "supportAgent",
        "lib/prompts/returns.ts · system",
        [],
        ["lookupOrder", "checkReturnEligibility", "createReturnLabel"],
      ),
    ],
    agentsFrom: "The Agent's model, system prompt and tools",
    tools: ["lookupOrder", "checkReturnEligibility", "createReturnLabel"],
    model: "gpt-4o",
  },
  strands: {
    name: "Billing helper",
    agents: [
      agt(
        "billing_agent",
        "agent.yaml · system_prompt",
        ["refund_agent"],
        ["list_transactions"],
      ),
      agt(
        "refund_agent",
        "agent.yaml · refund_agent.system_prompt",
        [],
        ["issue_refund"],
      ),
    ],
    agentsFrom: "The Agent and the agents it calls as tools",
    handoffsFrom: "Agents passed as tools",
    tools: ["list_transactions", "issue_refund", "escalate_to_human"],
    model: "claude-sonnet-4",
  },
  "pydantic-ai": {
    name: "Refund checker",
    agents: [
      agt(
        "refund_agent",
        "instructions.md",
        ["policy_agent"],
        ["lookup_order", "issue_refund"],
      ),
      agt(
        "policy_agent",
        "policy_instructions.md",
        [],
        ["search_knowledge_base"],
      ),
    ],
    agentsFrom: "The Agent and the agents its tools delegate to",
    handoffsFrom: "Agent delegation inside tools",
    tools: ["lookup_order", "issue_refund", "search_knowledge_base"],
    model: "gpt-4.1",
  },
  agno: {
    name: "Support team",
    agents: [
      agt(
        "support_team",
        "team instructions",
        ["orders_agent", "billing_agent"],
        [],
      ),
      agt(
        "orders_agent",
        "orders_agent.instructions",
        [],
        ["lookup_order", "shipment_status"],
      ),
      agt(
        "billing_agent",
        "billing_agent.instructions",
        [],
        ["list_transactions", "issue_refund"],
      ),
    ],
    agentsFrom: "The Team and its members",
    handoffsFrom: "Members the team routes to",
    tools: [
      "lookup_order",
      "shipment_status",
      "list_transactions",
      "issue_refund",
    ],
    model: "gpt-4o",
  },
  haystack: {
    name: "Policy Q&A",
    graph: {
      flow: "query_embedder → retriever → prompt_builder → llm → answer_builder",
      nodes: [
        node("retriever", null, ["search_knowledge_base"]),
        node("prompt_builder", "prompts/answer.jinja"),
        node("llm", null),
      ],
      edges: 4,
      conditional: 0,
      from: "the Pipeline's components and connections",
    },
    tools: ["search_knowledge_base", "escalate_to_human"],
    model: "gpt-4o",
  },
  llamaindex: {
    name: "Docs agent",
    graph: {
      flow: "start → retrieve → answer | escalate → stop",
      nodes: [
        node("retrieve", null, ["search_knowledge_base"]),
        node("answer", "prompts/system.md"),
        node("escalate", "prompts/escalate.md", ["escalate_to_human"]),
      ],
      edges: 4,
      conditional: 1,
      from: "the Workflow's steps and events",
    },
    tools: ["search_knowledge_base", "escalate_to_human"],
    model: "gpt-4.1",
  },
  smolagents: {
    name: "Order lookup",
    agents: [
      agt("manager", "system_prompt.yaml", ["search_agent"], ["lookup_order"]),
      agt("search_agent", "search_agent.description", [], ["web_search"]),
    ],
    agentsFrom: "The CodeAgent and its managed_agents",
    handoffsFrom: "managed_agents= on the manager",
    tools: ["lookup_order", "web_search"],
    model: "gpt-4o",
  },
  dspy: {
    name: "Intent router",
    graph: {
      flow: "classify → route → respond",
      nodes: [
        node("classify", "compiled/router.json · classify.signature"),
        node("route", null, ["lookup_order", "escalate_to_human"]),
        node("respond", "compiled/router.json · respond.signature"),
      ],
      edges: 2,
      conditional: 1,
      from: "the Module's predictors, compiled by its optimizer",
    },
    tools: ["lookup_order", "escalate_to_human"],
    model: "gpt-4o-mini",
  },
  "microsoft-agent-framework": {
    name: "Escalation desk",
    agents: [
      agt("triage", "instructions.md", ["specialist", "human_handoff"], []),
      agt(
        "specialist",
        "specialist_instructions.md",
        [],
        ["lookup_order", "issue_refund"],
      ),
      agt(
        "human_handoff",
        "handoff_instructions.md",
        [],
        ["escalate_to_human"],
      ),
    ],
    agentsFrom: "ChatAgents in the workflow",
    handoffsFrom: "Edges between the workflow's agents",
    tools: ["lookup_order", "issue_refund", "escalate_to_human"],
    model: "gpt-4o",
  },
};

/** The structure's shape for a framework: "graph" | "agents" | "crew". */
export const adapterShapeOf = (framework) => {
  const d = DESCRIBED[framework] || DESCRIBED.langgraph;
  return d.crew ? "crew" : d.agents ? "agents" : "graph";
};

/**
 * What the adapter at `endpoint` describes: the structure (kept as is on
 * the definition) and the facts to confirm, each with where it came from.
 */
export const describeAdapter = ({ framework, endpoint }) => {
  const d = DESCRIBED[framework] || DESCRIBED.langgraph;
  const a = ADAPTERS[framework] || ADAPTERS.langgraph;
  const local = /localhost|127\.0\.0\.1/.test(endpoint || "");
  const shape = adapterShapeOf(framework);
  const across =
    shape === "crew" ? "crew" : shape === "agents" ? "agents" : "steps";
  const base = {
    name: { value: d.name, from: "Name passed to serve(), or the module" },
    entry: {
      value: a.entry,
      from: `The object handed to serve()${local ? " · running locally" : ""}`,
    },
    tools: {
      value: d.tools,
      from: `${d.tools.length} tools bound across the ${across}`,
    },
    model: {
      value: d.model,
      from: "Model the adapter saw on the first describe call",
    },
  };
  if (shape === "agents") {
    const handing = d.agents.filter((x) => x.handoffs.length);
    return {
      structure: {
        agents: d.agents,
        ...(d.guardrails && { guardrails: d.guardrails }),
      },
      facts: {
        ...base,
        agents: {
          value: d.agents.map((x) => `${x.name} · ${x.instructions}`),
          from: `${d.agents.length} ${d.agents.length === 1 ? "agent" : "agents"} · ${d.agentsFrom || "reachable from the entry"}`,
        },
        ...(handing.length && {
          handoffs: {
            value: handing.map((x) => `${x.name} → ${x.handoffs.join(" | ")}`),
            from: d.handoffsFrom || "Handoffs on each agent",
          },
        }),
        ...(d.guardrails && {
          guardrails: {
            value: d.guardrails,
            from: "input_guardrails and output_guardrails",
          },
        }),
      },
    };
  }
  if (shape === "crew") {
    const c = d.crew;
    return {
      structure: { crew: c },
      facts: {
        ...base,
        crew: {
          value: `${c.name} · ${c.process} process · ${c.agents.length} agents · ${c.tasks.length} tasks`,
          from: "The Crew handed to serve()",
        },
        agents: {
          value: c.agents.map((x) => `${x.role} — ${x.goal}`),
          from: "Role and goal from config/agents.yaml",
        },
        tasks: {
          value: c.tasks.map((t) => `${t.name} → ${t.agent}`),
          from: "config/tasks.yaml, in run order",
        },
      },
    };
  }
  const g = d.graph;
  return {
    structure: { graph: g },
    facts: {
      ...base,
      graph: {
        value: g.flow,
        from: `${g.nodes.length} steps · ${g.edges} edges · ${g.conditional} conditional — from ${g.from || "the compiled graph"}`,
      },
      prompts: {
        value: g.nodes
          .filter((n) => n.prompt)
          .map((n) => `${n.id} · ${n.prompt}`),
        from: "What each step loads its instructions from",
      },
    },
  };
};

/* Frameworks a repository scan names that the adapter doesn't wrap. */
const TUNING_BY_NAME = {
  "Direct API calls": "Direct OpenAI, Anthropic or Gemini calls",
};

/**
 * What improvements edit for a framework (an adapter id like "google-adk",
 * or the name a scan found like "Google ADK"), from the tuning table:
 *   { label, edits, tuned, traced } — null when it's none we know.
 */
export const frameworkTuningOf = (framework) => {
  const id = FRAMEWORK_META[framework] ? framework : frameworkIdOf(framework);
  const meta = id ? FRAMEWORK_META[id] : null;
  const key = meta?.tuning || TUNING_BY_NAME[framework];
  const row = key ? FRAMEWORK_TUNING.find((t) => t.edits?.[key]) : null;
  if (!row) return null;
  return {
    label: meta?.label || framework,
    edits: row.edits[key],
    tuned: !!row.tuned,
    traced: meta?.traced !== false,
  };
};

/* ── built in Future AGI ──────────────────────────────────────────────────── */

export const BUILTIN_MODELS = [
  { id: "gpt-4o", label: "gpt-4o", vendor: "OpenAI" },
  { id: "gpt-4.1", label: "gpt-4.1", vendor: "OpenAI" },
  { id: "gpt-4o-mini", label: "gpt-4o-mini", vendor: "OpenAI" },
  { id: "claude-sonnet-4", label: "claude-sonnet-4", vendor: "Anthropic" },
  { id: "gemini-2.5-pro", label: "gemini-2.5-pro", vendor: "Google" },
];

/* Tools teams start from; any of them can be renamed or described again. */
export const BUILTIN_TOOL_LIBRARY = [
  {
    name: "lookup_order",
    desc: "Fetch an order by ID, email or phone",
    args: ["order_id"],
  },
  {
    name: "issue_refund",
    desc: "Refund up to the order total",
    args: ["order_id", "amount"],
  },
  {
    name: "check_return_eligibility",
    desc: "Confirm a return fits the policy",
    args: ["order_id"],
  },
  {
    name: "lookup_booking",
    desc: "Find a booking by reference or email",
    args: ["reference"],
  },
  {
    name: "search_knowledge_base",
    desc: "Search help articles and policies",
    args: ["query"],
  },
  {
    name: "escalate_to_human",
    desc: "Hand the conversation to a person",
    args: ["reason"],
  },
];

export const BUILTIN_PROMPT_PLACEHOLDER = [
  "You are the returns assistant for Acme.",
  "",
  "- Confirm the order number before changing anything.",
  "- Refunds over $500 go to a person — use escalate_to_human.",
  "- Keep replies short and say what you did.",
].join("\n");

/* ── constraints ──────────────────────────────────────────────────────────── */

/** Whether the limits differ from the safe defaults — only then are they saved. */
export const limitsCustomised = (limits) => {
  const d = DEFAULT_LIMITS;
  const same = (a, b) =>
    [...(a || [])].sort().join("|") === [...(b || [])].sort().join("|");
  return !(
    limits.model?.mode === d.model.mode &&
    same(limits.model?.approved, d.model.approved) &&
    limits.costCeiling === d.costCeiling &&
    (limits.costMode || "pct") === "pct" &&
    limits.latencyCeiling === d.latencyCeiling &&
    same(limits.allowed, d.allowed) &&
    same(limits.protected, d.protected) &&
    limits.budget?.dollars === d.budget.dollars &&
    limits.budget?.minutes === d.budget.minutes
  );
};

/** "+1 415 555 0142" → the country and the rest, for the contact-number fields. */
export const phoneParts = (phone) => {
  const m = /^(\+\d+)\s+(.*)$/.exec(String(phone || "").trim());
  if (!m) return { countryIso: "US", number: "" };
  return {
    countryIso:
      { "+1": "US", "+44": "GB", "+91": "IN", "+61": "AU" }[m[1]] || "US",
    number: m[2],
  };
};

/* ── matching a repository to an Observe project's traces ─────────────────── */

const fwKey = (x) =>
  String(x || "")
    .toLowerCase()
    .replace(/[^a-z]/g, "");
/* "langgraph" (traces) vs "LangGraph" (code); "openai-agents" vs "OpenAI Agents SDK". */
const sameFramework = (traced, found) => {
  const a = fwKey(traced);
  const b = fwKey(found);
  return (
    !!a &&
    !!b &&
    (a === b ||
      a.startsWith(b) ||
      b.startsWith(a) ||
      b.startsWith(a.replace(/sdk$/, "")))
  );
};
const repoWords = (name) =>
  String(name || "")
    .split("/")
    .pop()
    .toLowerCase();

/**
 * Does what we read in the code match what the project's traces said? The
 * framework must be the same and at least half the traced tools must be
 * registered in the code — otherwise connecting it would point Fix at a
 * different agent.
 *   { ok, frameworkOk, toolsFound, toolsTraced, modelOk }
 */
export const traceMatchOf = (traced, found) => {
  if (!traced || !found) return null;
  const frameworkOk = sameFramework(traced.framework, found.framework);
  const tools = new Set(found.tools || []);
  const toolsFound = (traced.tools || []).filter((t) => tools.has(t)).length;
  const toolsTraced = (traced.tools || []).length;
  return {
    ok: frameworkOk && toolsFound >= Math.ceil(toolsTraced / 2),
    frameworkOk,
    toolsFound,
    toolsTraced,
    modelOk: !traced.model || !found.model || traced.model === found.model,
  };
};

/** The repository the traces most likely come from: named like the traced service, else holding an agent of that framework. */
export const repoForTraces = (traced, repos = []) => {
  if (!traced) return null;
  const byName = repos.find(
    (r) => repoWords(r.name) === String(traced.service || "").toLowerCase(),
  );
  if (byName) return byName.name;
  return null;
};

/** The folder of the agent in a repository that matches the traces' framework. */
export const folderForTraces = (traced, repo) => {
  const a = (repo?.agents || []).find((x) =>
    sameFramework(traced?.framework, x.framework),
  );
  return a ? dirOf(a.entry) : null;
};

/** The entry point in a folder that matches the traces' framework. */
export const entryForTraces = (traced, repo, folder) =>
  (repo?.agents || []).find(
    (x) =>
      dirOf(x.entry) === folder &&
      sameFramework(traced?.framework, x.framework),
  )?.id || null;
