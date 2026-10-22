/*
  An agent's life after it's connected (updated RSI PRD, "Agents, defined"):

  · status — connected, disconnected (a revoked key, a removed GitHub App, an
    agent deleted on its platform) or archived (new runs and improvements
    stop; test copies we made on the platform are removed; versions,
    environments, runs and proofs stay). Archive is reversible. Delete is only
    possible once no environment tests the agent.
  · main environment — the one an improvement runs in by default: the first
    built, unless someone picks another.
  · channels — voice and chat can be one agent; each channel gets its own
    environment.
  · Retell numbers — a new draft becomes the newest version, so live numbers
    that follow the newest version are pinned to a released one first.
  · closed platforms and framework tuning phases, as the PRD lists them.

  Stored on the agent's values (all optional):
    values.disconnected        { reason, at, cause: "key" | "app" | "deleted" } | null
    values.archived            { at, removedCopies: [name] } | null
    values.mainEnvId           an environment id the agent is used in
    values.channels            ["voice", "chat"]
    values.liveTestConsent     { at, replayFlag }
    values.retellNumbersPinned { at, version }
    values.releaseDetection    { watch: "branch" | "tag", ref, dailySync }

  Re-exported from rsiModel, so screens read them from one place.
*/
import { PROVIDER_LABELS, agentSourceOf, envAgentKey } from "./agentRegistry";

const hash = (s) => {
  let h = 0;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
};

/* ── status ───────────────────────────────────────────────────────────────── */

const providerName = (agent) => PROVIDER_LABELS[agent?.values?.provider] || agentSourceOf(agent).label || "the platform";

/** Why an agent is disconnected, said for its source. */
export const disconnectReasonFor = (agent, cause = "key") => {
  const name = providerName(agent);
  if (cause === "app") return "The GitHub App no longer has access to this repository, so we can't read it or open pull requests.";
  if (cause === "deleted") return `The agent was deleted on ${name} — it no longer exists in the account. Point this agent at another one, or archive it.`;
  return `The ${name} API key was revoked — ${name} refused the last read. Replace the key's reference on Connections to reconnect.`;
};

export const AGENT_STATUS_LABELS = { connected: "Connected", disconnected: "Disconnected", archived: "Archived", deleted: "Deleted" };

/**
 * { status: "connected" | "disconnected" | "archived" | "deleted", label, reason, at?, cause? }
 * Archived wins over disconnected: an archived agent stays archived whatever
 * happens to its key. A repository the GitHub App lost reads as disconnected.
 */
export const agentStatusOf = (agent) => {
  const v = agent?.values || {};
  /* Deleted wins: a tombstone kept only so its history can be read. */
  if (agent?.deleted?.at) {
    return {
      status: "deleted",
      label: AGENT_STATUS_LABELS.deleted,
      at: agent.deleted.at,
      reason: "Deleted — it's no longer in Agents or any picker. Its versions, and the runs and proofs that name it, are kept here.",
    };
  }
  if (v.archived?.at) {
    return {
      status: "archived",
      label: AGENT_STATUS_LABELS.archived,
      at: v.archived.at,
      reason: "Archived — new runs and improvements are stopped. Its versions, environments, runs and proofs stay; restore it to start again.",
    };
  }
  if (v.disconnected && (v.disconnected.at || v.disconnected.reason)) {
    const cause = v.disconnected.cause || "key";
    return {
      status: "disconnected",
      label: AGENT_STATUS_LABELS.disconnected,
      at: v.disconnected.at || null,
      cause,
      reason: v.disconnected.reason || disconnectReasonFor(agent, cause),
    };
  }
  if (agentSourceOf(agent).appLost) {
    return { status: "disconnected", label: AGENT_STATUS_LABELS.disconnected, cause: "app", reason: disconnectReasonFor(agent, "app") };
  }
  return { status: "connected", label: AGENT_STATUS_LABELS.connected, reason: null };
};

/* ── where it's tested ────────────────────────────────────────────────────── */

/* The environments testing this agent: its usedIn when it's a merged record,
   otherwise every environment whose agent has the same identity. */
const usedInOf = (agent, state) => {
  if (agent?.usedIn) return agent.usedIn;
  const key = agent?.key || envAgentKey(agent, null);
  return (state?.myEnvironments || [])
    .filter((e) => key && envAgentKey(state?.byEnv?.[e.id]?.agent, e.id) === key)
    .map((e) => ({ envId: e.id, envName: e.name }));
};

/**
 * The agent's main environment — what Fix runs in by default: the one the
 * user picked (values.mainEnvId), else the first one built.
 *   { id, envId, envName, env, picked } | null
 */
export const mainEnvOf = (agent, state) => {
  const used = usedInOf(agent, state);
  if (!used.length) return null;
  const envs = state?.myEnvironments || [];
  const order = (id) => {
    const i = envs.findIndex((e) => e.id === id);
    return i < 0 ? envs.length : i;
  };
  const at = (id) => Date.parse(envs.find((e) => e.id === id)?.adoptedAt || 0) || 0;
  const picked = used.find((u) => u.envId === agent?.values?.mainEnvId) || null;
  /* First built: earliest adopted; ties go to the one listed first. */
  const first = [...used].sort((a, b) => at(a.envId) - at(b.envId) || order(a.envId) - order(b.envId))[0];
  const u = picked || first;
  return { id: u.envId, envId: u.envId, envName: u.envName, env: envs.find((e) => e.id === u.envId) || null, picked: !!picked };
};

/* ── channels ─────────────────────────────────────────────────────────────── */

export const CHANNEL_LABELS = { voice: "Voice", chat: "Chat" };
const VOICE_TYPES = new Set(["voice_platform", "voice_sip"]);

/** The channels an agent serves — stored, else read from its type. */
export const agentChannelsOf = (agent) => {
  const list = agent?.values?.channels;
  if (Array.isArray(list) && list.length) return list;
  return [VOICE_TYPES.has(agent?.typeId) || agent?.values?.contact || agent?.values?.phoneNumber ? "voice" : "chat"];
};

/** Each channel with the environments that test it — voice by a voice world, chat by any other. */
export const channelEnvsOf = (agent, state) => {
  const used = usedInOf(agent, state);
  const envs = state?.myEnvironments || [];
  const surfaceOf = (id) => envs.find((e) => e.id === id)?.surface || null;
  return agentChannelsOf(agent).map((channel) => ({
    channel,
    label: CHANNEL_LABELS[channel] || channel,
    envs: used.filter((u) => (channel === "voice" ? surfaceOf(u.envId) === "voice" : surfaceOf(u.envId) !== "voice")),
  }));
};

/* ── Retell: pin the live numbers first ───────────────────────────────────── */

/**
 * Retell makes every new draft the newest version, so a live number that
 * follows the newest version would start taking real calls on it. Until the
 * numbers are pinned to a released version, a test runs the agent with its
 * settings overridden for one call.
 *   { pinned, version, numbers: [{ number, follows }], line } | null
 */
export const retellPinStateOf = (agent) => {
  const v = agent?.values || {};
  if (v.provider !== "retell") return null;
  const seed = hash(v.agentId || agent?.key);
  /* The newest released version on Retell — its own numbering, not ours. */
  const version = v.retellNumbersPinned?.version || `v${3 + (seed % 3)}`;
  const base = 100 + (seed % 800);
  const numbers = [
    { number: `+1 415 555 0${String(base).padStart(3, "0")}`, follows: "newest" },
    { number: `+1 628 555 0${String((base + 37) % 1000).padStart(3, "0")}`, follows: "newest" },
  ];
  const pinned = !!v.retellNumbersPinned?.at;
  const n = numbers.length;
  return {
    pinned,
    version,
    at: v.retellNumbersPinned?.at || null,
    numbers: numbers.map((x) => ({ ...x, follows: pinned ? version : "newest" })),
    line: pinned
      ? `${n} live numbers pinned to ${version} — drafts can be created without taking real calls`
      : `${n} live numbers follow the newest version — pin them to ${version} before drafts are created`,
  };
};

/* ── release detection (Phase 2) ──────────────────────────────────────────── */

export const releaseDetectionOf = (agent) => {
  const r = agent?.values?.releaseDetection || {};
  const watch = r.watch === "tag" ? "tag" : "branch";
  return { watch, ref: r.ref || (watch === "tag" ? "prod" : "main"), dailySync: !!r.dailySync };
};

/* ── closed platforms ─────────────────────────────────────────────────────── */

export const CLOSED_PLATFORMS = [
  { id: "sierra", label: "Sierra" },
  { id: "decagon", label: "Decagon" },
  { id: "intercom_fin", label: "Intercom Fin" },
  { id: "zendesk", label: "Zendesk" },
];
export const CLOSED_PLATFORM_NOTE = "Closed platforms don't expose their agents' settings, so they're test only — through their phone number, chat channel, or a conversation API where one exists. Improvements end in a suggested fix.";

/* ── framework-aware tuning, by phase ─────────────────────────────────────── */

/* Any framework the environment can run works; each row is the PRD's tuning
   table — the frameworks improvements learn the structure of (graphs,
   handoffs, crews, pipelines) so they can change it, grouped by when they
   were tuned. Every row is tuned now, the Later ones included. `edits` is
   the PRD's "What the improver edits" column, by framework. The phase is
   bookkeeping only — never shown. */
export const FRAMEWORK_TUNING = [
  {
    phase: "1", tuned: true, items: ["LangGraph / LangChain", "OpenAI Agents SDK", "Direct OpenAI, Anthropic or Gemini calls", "LiveKit Agents"],
    edits: {
      "LangGraph / LangChain": "Prompts, tools and each step's model",
      "OpenAI Agents SDK": "Each agent's instructions, model, tools and handoffs",
      "Direct OpenAI, Anthropic or Gemini calls": "Prompts and tools written in the code; you name where the agent starts",
      "LiveKit Agents": "Each agent's instructions and tools, and the speech, model and turn-taking settings it uses",
    },
  },
  {
    phase: "2", tuned: true, items: ["Pipecat", "Google ADK", "Mastra and Vercel AI SDK", "AWS Strands and Pydantic AI", "CrewAI and Claude Agent SDK"],
    edits: {
      Pipecat: "The pipeline's settings, and its flow file when it has one",
      "Google ADK": "Agents in code or a configuration file",
      "Mastra and Vercel AI SDK": "Agents defined in code or files; Mastra also keeps its own draft versions",
      "AWS Strands and Pydantic AI": "Agents in code; Strands can also load one from a configuration file",
      "CrewAI and Claude Agent SDK": "CrewAI agents in configuration files; Claude agents in code plus instruction files",
    },
  },
  {
    phase: "later", tuned: true, items: ["Agno", "Haystack", "LlamaIndex", "smolagents", "DSPy", "Microsoft Agent Framework"],
    edits: {
      Agno: "Agents in code", Haystack: "Agents in code", LlamaIndex: "Agents in code", smolagents: "Agents in code",
      DSPy: "Agents in code; DSPy's instructions are rewritten by its own optimizers",
      "Microsoft Agent Framework": "Agents in code",
    },
  },
];
export const FRAMEWORK_TUNING_NOTE = "Any framework the environment can run works. Where its structure is tuned, improvements also learn its graph, handoffs or crew and can change them.";

const FRAMEWORK_PHASE_BY_KEY = [
  [/langgraph|langchain/i, "1"], [/openai[- ]agents/i, "1"], [/livekit/i, "1"], [/direct|openai|anthropic|gemini/i, "1"],
  [/pipecat|adk|mastra|vercel|strands|pydantic|crew|claude[_ -]agent/i, "2"],
  [/agno|haystack|llama|smol|dspy|microsoft|autogen|semantic/i, "later"],
];
/** The tuning row for a framework id or name (`tuned` says whether improvements learn its structure); null when unknown. */
export const frameworkPhaseOf = (framework) => {
  const hit = FRAMEWORK_PHASE_BY_KEY.find(([re]) => re.test(String(framework || "")));
  return hit ? FRAMEWORK_TUNING.find((t) => t.phase === hit[1]) : null;
};
