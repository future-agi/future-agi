/*
  The Environments landing screen's data: what a pasted string is, the
  directory of ways to connect an agent, the seeds (ready-made worlds with a
  stand-in agent and a finished demo run) and the prebuilt worlds. Kept out of
  the .jsx so the landing file exports components only.

  Every entry opens one of the existing setup panels (StartEnvironment's
  PANELS) with what we already know filled in — `{ choice, prefill }`.
*/
import { gatewayServerForHost } from "../_mock/gatewayMcp";
import { mcpServerHost } from "../_mock/mcpServers";
import { ENVIRONMENT_TEMPLATES } from "../_mock/environments";
import { CATALOGUE } from "../_mock/catalogue";
import { packStats } from "../_mock/scenarios";
import { TWIN_CATALOG } from "../_mock/twins";

/* ── what a pasted string is ───────────────────────────────────────────── */

const GITHUB = /^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:\/.*)?$|^git@github\.com:([\w.-]+)\/([\w.-]+?)(?:\.git)?$/i;
const GITLAB = /(?:^|\/\/|@)(?:www\.)?gitlab\.com[/:]/i;
const BITBUCKET = /(?:^|\/\/|@)(?:www\.)?bitbucket\.org[/:]/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PHONE = /^\+?[\d\s().-]{8,}$/;
const URL_LIKE = /^(?:https?|wss?):\/\/\S+$/i;
/* Links to a page or file, not to something we can call. */
const DOC_HOSTS = [
  [/(?:^|\.)notion\.(?:so|site)$/, "Notion page"],
  [/^docs\.google\.com$/, "Google Doc"],
  [/^drive\.google\.com$/, "Google Drive file"],
  [/\.atlassian\.net$/, "Confluence page"],
  [/(?:^|\.)(?:sharepoint\.com|onedrive\.live\.com|1drv\.ms)$/, "SharePoint or OneDrive file"],
  [/(?:^|\.)(?:dropbox\.com|coda\.io|quip\.com|figma\.com|miro\.com|loom\.com)$/, "A shared page"],
];
const docLinkOf = (text) => {
  const host = /^(?:https?:\/\/)?([^/\s?#:]+)/i.exec(text)?.[1]?.toLowerCase().replace(/^www\./, "");
  if (!host || !/\./.test(host) || (!/^https?:\/\//i.test(text) && !/\//.test(text))) return null;
  const hit = DOC_HOSTS.find(([re]) => re.test(host));
  return hit ? { host, label: hit[1] } : null;
};
/* Raw secrets: never accepted here — keys go in by secret-manager reference. */
const RAW_KEY = /^(sk|pk|rk|key|api)[-_][\w-]{12,}$|^[A-Za-z0-9_-]{32,}$/;

/**
 * What a pasted string looks like, and where it goes.
 * { kind, label, detail, choice, prefill, logo, tag?, blocked?, warn? } or null.
 *   blocked  recognised, but not a path we can take today (said why).
 *   warn     a raw secret — refused, with where it belongs instead.
 *   action   what Continue says, when it isn't just "Continue".
 *   alternatives  other ways the same paste can go — [{ label, choice, prefill }].
 */
export const detectPaste = (raw) => {
  const text = String(raw || "").trim();
  if (!text) return null;
  const gh = GITHUB.exec(text);
  if (gh) {
    const repo = `${gh[1] || gh[3]}/${(gh[2] || gh[4]).replace(/\.git$/, "")}`;
    return {
      kind: "github", label: "GitHub repository", detail: repo, logo: "github",
      note: "Read through the GitHub App — fixes open as draft pull requests.",
      choice: "source", prefill: { repo },
    };
  }
  if (GITLAB.test(text) || BITBUCKET.test(text)) {
    const host = GITLAB.test(text) ? "gitlab" : "bitbucket";
    const label = host === "gitlab" ? "GitLab" : "Bitbucket";
    /* "acme-inc/claims-voice" from any of the URL forms, subgroups kept. */
    const repo = text.replace(/^(?:https?:\/\/|git@)(?:www\.)?(?:gitlab\.com|bitbucket\.org)[/:]/i, "").replace(/\.git$|\/+$/g, "").split("/-/")[0];
    return {
      kind: host, label: `${label} repository`, detail: repo, logo: host,
      note: `Read through your ${label} connection — fixes open as draft ${host === "gitlab" ? "merge" : "pull"} requests.`,
      choice: "source", prefill: { repo, host },
    };
  }
  /* A Dialogflow CX agent's resource name. */
  if (/^projects\/[\w-]+\/locations\/[\w-]+\/agents\/[\w-]+$/i.test(text)) {
    return {
      kind: "google_cx", label: "Google CX agent", detail: text.split("/").pop(), icon: "simple-icons:dialogflow",
      note: "Tested on a draft flow version in your project — reference the service account key next.",
      choice: "hosted", prefill: { agentType: "chat", platform: "google_cx", agentId: text },
    };
  }
  if (/^vault:\/\/|^aws-sm:\/\/|^gcp-sm:\/\//i.test(text)) {
    return {
      kind: "secret-ref", label: "Secret reference", detail: text, icon: "solar:key-minimalistic-square-linear",
      note: "A key reference goes with its platform — pick the platform in Connect your agent below.",
      blocked: true,
    };
  }
  const doc = docLinkOf(text);
  if (doc) {
    return {
      kind: "document", label: doc.label, detail: doc.host, icon: "solar:document-text-linear",
      note: "A document can't be called like an agent. Paste its text here instead, or pick where the agent runs below.",
      blocked: true,
    };
  }
  if (RAW_KEY.test(text) && !UUID.test(text) && !/^(asst|agent)_/i.test(text)) {
    return {
      kind: "raw-key", label: "Looks like an API key", detail: "Not kept", icon: "solar:shield-warning-linear",
      note: "Not kept here. Pick the platform below and paste it into its key field — stored encrypted, never shown again.",
      warn: true, blocked: true,
    };
  }
  if (/\/(mcp|sse)\b|^npx\s|^uvx\s|^docker\s+run\b/i.test(text)) {
    const transport = /^(npx|uvx|docker)\s/i.test(text) ? "stdio" : /\/sse\b/i.test(text) ? "sse" : "http";
    return {
      kind: "mcp", label: "MCP server", detail: text, logo: "mcp",
      note: transport === "stdio"
        ? "That's a command, and a pasted command never runs in our sandbox — paste the server's HTTP or SSE URL, or pick it from your Gateway."
        : gatewayServerForHost(mcpServerHost(text))
          ? `${gatewayServerForHost(mcpServerHost(text)).label} is in your Gateway — its tools become the world of one of your agents' environments, with the Gateway's credentials. Pick the agent next.`
          : "Its tools become the world of one of your agents' environments — pick the agent next.",
      choice: "mcp", prefill: { target: text, transport },
    };
  }
  if (/^wss:\/\/[\w.-]*livekit\.cloud/i.test(text)) {
    return {
      kind: "livekit", label: "LiveKit server", detail: text.replace(/^wss:\/\//, ""), logo: "livekit",
      note: "Next, name the agent and reference its LiveKit key.",
      choice: "hosted", prefill: { agentType: "voice", platform: "livekit" },
    };
  }
  /* An OpenAI Assistants ID. OpenAI retired Assistants, so there's nothing
     to call by it — the agent lives on in the code that called it. */
  if (/^asst_[\w-]{8,}$/i.test(text)) {
    return {
      kind: "openai-assistant", label: "OpenAI assistant ID", detail: text, icon: "simple-icons:openai",
      note: "OpenAI retired Assistants, so we can't run it by its ID. Connect the code that calls it, or start from its instructions.",
      choice: "source", prefill: { host: "github", direct: true }, action: "Connect its code",
      alternatives: [{ label: "Use its instructions", choice: "scratch", prefill: { flowText: "" } }],
    };
  }
  if (UUID.test(text)) {
    return {
      kind: "vapi", label: "Vapi assistant ID", detail: text, logo: "vapi",
      note: "Tested live in your account on a copy we make — reference your Vapi key next.",
      choice: "hosted", prefill: { agentType: "voice", platform: "vapi", agentId: text },
    };
  }
  if (/^agent_[\w]{6,}$/i.test(text)) {
    return {
      kind: "retell", label: "Retell agent ID", detail: text, logo: "retell",
      note: "Tested on a pinned draft in your account — reference your Retell key next.",
      choice: "hosted", prefill: { agentType: "voice", platform: "retell", agentId: text },
    };
  }
  if (PHONE.test(text) && text.replace(/\D/g, "").length >= 8) {
    return {
      kind: "phone", label: "Phone number", detail: text, icon: "solar:phone-calling-rounded-linear",
      note: "We call it like a customer would. Test only — improvements end in a suggested fix.",
      choice: "phone", prefill: { number: text },
    };
  }
  if (URL_LIKE.test(text)) {
    return {
      kind: "endpoint", label: "Agent endpoint", detail: text.replace(/^https?:\/\//, ""), icon: "solar:global-linear",
      note: "We call it on every simulated turn. Tell us where the reply sits next.",
      choice: "running", prefill: { endpoint: text, transport: /^wss?:/i.test(text) ? "websocket" : "http" },
    };
  }
  if (text.split(/\s+/).length >= 6) {
    return {
      kind: "describe", label: "A description", detail: `${text.slice(0, 60)}${text.length > 60 ? "…" : ""}`, icon: "solar:document-text-linear",
      note: "We'll set this up as an agent built in Future AGI, then build its environment.",
      choice: "scratch", prefill: { flowText: text },
    };
  }
  return {
    kind: "unknown", label: "Not recognised yet", detail: text, icon: "solar:question-circle-linear",
    note: "Try a GitHub, GitLab or Bitbucket URL, a Vapi or Retell ID, an MCP URL, an endpoint, a phone number — or describe the agent in a sentence.",
    blocked: true,
  };
};

/* One click fills the field with a real-looking example of each kind. */
export const PASTE_EXAMPLES = [
  { id: "github", label: "GitHub repo", logo: "github", value: "https://github.com/skyline-air/rebooking-agent" },
  { id: "vapi", label: "Vapi assistant ID", logo: "vapi", value: "3f9c2ab1-4d7e-4c1a-9b2f-8e61d0a5c7e4" },
  { id: "retell", label: "Retell agent ID", logo: "retell", value: "agent_9f2c41d8e7" },
  { id: "mcp", label: "MCP URL", logo: "mcp", value: "https://tools.acme.dev/mcp" },
  { id: "endpoint", label: "Endpoint", icon: "solar:global-linear", value: "https://api.acme.com/agent/chat" },
  { id: "describe", label: "A sentence", icon: "solar:document-text-linear", value: "A pharmacy refill line that checks insurance before booking a pickup slot" },
];

/* ── the directory of ways in ───────────────────────────────────────────── */

/*
  Grouped by where the agent lives today, each group with the one line a buyer
  needs: how we test it there, and how far a fix can go. `need` is what they'll
  be asked for.
*/
export const CONNECT_GROUPS = [
  {
    id: "voice",
    title: "Voice platforms",
    reach: "Tested live in your account. Fixes ship as a copy or draft you swap in.",
    items: [
      { id: "vapi", name: "Vapi", logo: "vapi", need: "API key · assistant ID", choice: "hosted", prefill: { agentType: "voice", platform: "vapi" } },
      { id: "retell", name: "Retell AI", logo: "retell", need: "API key · agent ID", choice: "hosted", prefill: { agentType: "voice", platform: "retell" } },
      { id: "livekit", name: "LiveKit", logo: "livekit", need: "Test only · server URL · agent name", choice: "hosted", prefill: { agentType: "voice", platform: "livekit" } },
      { id: "elevenlabs", name: "ElevenLabs", logo: "elevenlabs", need: "Agents API key", choice: "hosted", prefill: { agentType: "voice", platform: "elevenlabs" } },
      { id: "bland", name: "Bland.ai", logo: "bland", need: "API key · agent ID", choice: "hosted", prefill: { agentType: "voice", platform: "bland" } },
      { id: "phone", name: "Phone number", icon: "solar:phone-calling-rounded-linear", need: "Any agent you can call", choice: "phone" },
    ],
  },
  {
    id: "chat",
    title: "Chat and prompts",
    reach: "Chat agents run live on their platform. A pasted prompt runs on our model — fixes ship as a new prompt version.",
    items: [
      { id: "vapi-chat", name: "Vapi chat", logo: "vapi", need: "Same assistant, over chat", choice: "hosted", prefill: { agentType: "chat", platform: "vapi" } },
      { id: "retell-chat", name: "Retell chat", logo: "retell", need: "API key · chat agent", choice: "hosted", prefill: { agentType: "chat", platform: "retell" } },
      { id: "elevenlabs-chat", name: "ElevenLabs chat", logo: "elevenlabs", need: "Chat or WhatsApp agent", choice: "hosted", prefill: { agentType: "chat", platform: "elevenlabs" } },
      { id: "prompt", name: "Paste a system prompt", icon: "solar:document-text-linear", need: "No key or endpoint", choice: "hosted", prefill: { agentType: "voice", platform: "other" } },
    ],
  },
  {
    id: "code",
    title: "Source code",
    reach: "We read the code. Fixes reach prompts, tools and code — as draft pull requests.",
    items: [
      { id: "github", name: "GitHub", logo: "github", need: "Read-only app · repo and folder", choice: "source" },
      { id: "upload", name: "Upload a folder", icon: "solar:upload-minimalistic-linear", need: "Folder or .zip, up to 200 MB", choice: "upload" },
      /* Frameworks, not hosted platforms — they come in as code. */
      { id: "gitlab", name: "GitLab", logo: "gitlab", need: "Group token · repo and folder", choice: "source", prefill: { host: "gitlab" } },
      { id: "bitbucket", name: "Bitbucket", logo: "bitbucket", need: "Workspace token · repo and folder", choice: "source", prefill: { host: "bitbucket" } },
      { id: "langgraph", name: "LangGraph", logo: "langgraph", need: "Framework · from a repository or an upload", choice: "source" },
      { id: "claude-agent-sdk", name: "Claude Agent SDK", logo: "claude", need: "Framework · from a repository or an upload", choice: "source" },
    ],
  },
  {
    id: "other",
    title: "Endpoints and tools",
    reach: "An agent we can only call is test only. An MCP server's tools can be an agent's world.",
    items: [
      { id: "endpoint", name: "Agent endpoint", icon: "solar:global-linear", need: "HTTP or WebSocket URL", choice: "running" },
      { id: "mcp", name: "MCP server", logo: "mcp", need: "HTTP · SSE · stdio", choice: "mcp" },
    ],
  },
];

/** Groups with only the items a search matches; groups keep their place. */
export const filterGroups = (query) => {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return CONNECT_GROUPS;
  return CONNECT_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((it) => `${it.name} ${it.need} ${g.title}`.toLowerCase().includes(q)),
  }));
};

export const itemCount = CONNECT_GROUPS.reduce((n, g) => n + g.items.length, 0);

/* ── seeds and prebuilt worlds ──────────────────────────────────────────── */

/*
  A prebuilt world is tables, tools and rules as designed — the world an
  agent's environment can start from. It's never an environment on its own:
  the agent is picked from Agents first.
*/

/* The kind of agent a world is built for — the same as the agent types:
   voice and chat, with browser and computer use coming soon. Worlds built
   for other kinds of agent (coding, robotics) aren't offered. */
export const WORLD_TYPES = [
  { id: "voice", label: "Voice" },
  { id: "chat", label: "Chat" },
  { id: "computer", label: "Computer use", soon: true },
];
const TYPE_OF_AGENT = {
  voice_platform: "voice",
  chat_webhook: "chat", api_agent: "chat", mcp_agent: "chat",
  browser_agent: "computer", game_agent: "computer",
  coding_agent: null,
  sim_agent: null,
};
/** The world's agent type, or null for one built for an agent type we don't offer. */
export const worldTypeOf = (agentType) => {
  const id = agentType in TYPE_OF_AGENT ? TYPE_OF_AGENT[agentType] : "chat";
  return id ? WORLD_TYPES.find((t) => t.id === id) : null;
};

const DOMAIN_LABEL = {
  ecommerce: "E-commerce", fintech: "Finance", travel: "Travel", software: "Software", robotics: "Robotics",
  gaming: "Games", research: "Research", operations: "Operations", ml: "Machine learning", hardware: "Hardware",
  support: "Support", sales: "Sales",
  scheduling: "Scheduling", knowledge: "Knowledge", healthcare: "Healthcare", telecom: "Telecom",
  utilities: "Utilities", insurance: "Insurance", hospitality: "Hospitality", automotive: "Automotive",
  property: "Property", education: "Education", hr: "HR", logistics: "Logistics", saas: "SaaS",
  finance: "Finance", procurement: "Procurement", it: "IT", apps: "Apps · synthetic",
};
export const domainLabelOf = (d) => DOMAIN_LABEL[d] || (d ? d.charAt(0).toUpperCase() + d.slice(1) : "Other");

/* One card's worth of a world template. */
const worldCardOf = (t, entry = null) => {
  const type = worldTypeOf(t.agentType);
  return {
    id: entry ? entry.id : t.id,
    name: t.name,
    tagline: entry ? entry.task : t.tagline,
    type: type.id,
    typeLabel: type.label,
    domain: t.domain,
    domainLabel: domainLabelOf(t.domain),
    tools: (t.tools || []).length,
    toolNames: (t.tools || []).map((x) => x.name),
    tables: (t.seed?.tables || []).length,
    scenarios: packStats(t).scenarios,
    popularity: t.popularity || 0,
    template: t,
    /* The catalogue's own: task ID, release, readiness, launch world. */
    ...(entry && {
      catalogueId: entry.id,
      catalogueDomain: entry.domain,
      release: entry.release,
      readiness: entry.release.status,
      launch: !!entry.launch,
      entry,
    }),
  };
};

/* The launch worlds lead, in the doc's order. */
const LAUNCH_ORDER = ["V15", "V01", "V06", "V12", "V11"];

/**
 * Prebuilt worlds — the 30-task catalogue (launch worlds first, then by
 * task ID), plus worlds for agent types that are Coming soon.
 */
export const prebuiltWorlds = () => {
  const byId = Object.fromEntries(ENVIRONMENT_TEMPLATES.map((t) => [t.id, t]));
  const tasks = CATALOGUE
    .map((c) => (byId[c.templateId] ? worldCardOf(byId[c.templateId], c) : null))
    .filter(Boolean)
    .sort((a, b) => {
      const la = LAUNCH_ORDER.indexOf(a.catalogueId);
      const lb = LAUNCH_ORDER.indexOf(b.catalogueId);
      if (la !== lb) return (la < 0 ? 99 : la) - (lb < 0 ? 99 : lb);
      /* Domain tasks (V) before app-shaped ones (M). */
      return `${a.catalogueId[0] === "V" ? 0 : 1}${a.catalogueId}`.localeCompare(`${b.catalogueId[0] === "V" ? 0 : 1}${b.catalogueId}`);
    });
  const used = new Set(CATALOGUE.map((c) => c.templateId));
  const others = ENVIRONMENT_TEMPLATES
    .filter((t) => t.agentType !== "twin_backed" && worldTypeOf(t.agentType) && !used.has(t.id))
    .map((t) => worldCardOf(t));
  return [...tasks, ...others];
};

/** Worlds a search, a type, a domain and a readiness leave. Search covers the task, its ID and its tools. */
export const filterWorlds = (worlds, { query = "", type = "all", domain = "all", readiness = "all" } = {}) => {
  const q = query.trim().toLowerCase();
  return worlds.filter((w) => (type === "all" || w.type === type)
    && (domain === "all" || w.domain === domain)
    && (readiness === "all" || (w.readiness || "ready") === readiness)
    && (!q || `${w.name} ${w.tagline} ${w.domainLabel} ${w.typeLabel} ${w.catalogueId || ""} ${w.catalogueDomain || ""} ${(w.toolNames || []).join(" ")}`.toLowerCase().includes(q)));
};

/* ── the New environment journey ───────────────────────────────────────── */

/** SaaS sandboxes for web environments. */
export const sandboxServices = () => TWIN_CATALOG.map((t) => ({ id: t.id, name: t.name, icon: t.icon, iconDark: t.iconDark }));

/*
  The SaaS sandbox counts as one more world in the full list — pinned first,
  under its own "Any agent" type. It shows when no type or domain narrows it
  out and a search, if any, matches it or one of its apps.
*/
export const SANDBOX_TYPE = { id: "any", label: "Any agent" };
export const sandboxMatches = ({ query = "", type = "all", domain = "all" } = {}) => {
  if (domain !== "all" || (type !== "all" && type !== SANDBOX_TYPE.id)) return false;
  const q = query.trim().toLowerCase();
  return !q || `saas sandbox any agent ${TWIN_CATALOG.map((t) => t.name).join(" ")}`.toLowerCase().includes(q);
};
/** Every world the full list shows — the prebuilt ones and the sandbox. */
export const worldTotal = () => prebuiltWorlds().length + 1;
