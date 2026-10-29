/**
 * The workspace's connected agents.
 *
 * An agent is connected in one of two places — while building an environment,
 * or from the Agents page — and both have to land in the same list. Each
 * environment keeps its own copy of the agent it tests, so the list is built
 * by identity: two environments pointed at the same Vapi assistant are one
 * agent with one version history, used in two places.
 *
 * An agent version is not a prompt. It is a snapshot of everything that
 * decides behaviour — instructions, model, voice, tools, knowledge and how we
 * reach it. How much of that we can see depends on where the agent lives: a
 * hosted platform hands us its whole config, a repo its code at a commit, an
 * endpoint nothing but what it does.
 */
import { getAgentType } from "./agentTypes";
import "../environments/colorLogos"; // registers fagi:* brand marks
import { agentVersions, currentAgentVersion, isAgentPinned, majorOf, versionNumber } from "./versions";
import { agentToolsFor } from "./toolFit";
import { agentPromptOf } from "./agentPrompt";
import { getEnvironment } from "./environments";
import { GIT_HOST_LABELS, gitHostOfUrl, hostRepoVisible, installedRepos, repoNameOf } from "./integrations";

/* ── identity ─────────────────────────────────────────────────────────────── */

const slugOf = (s) => String(s || "agent").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
const hash = (s) => {
  let h = 0;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
};

/**
 * One spelling per repository: "acme/repo", "github.com/acme/repo",
 * "https://github.com/acme/repo.git/" are all the same place. Everything that
 * stores or compares a repo URL goes through this.
 */
export const normalizeRepoUrl = (url) => {
  let u = String(url || "").trim().replace(/\.git$/i, "").replace(/\/+$/, "");
  if (!u) return "";
  if (/^[\w.-]+\/[\w.-]+$/.test(u)) u = `https://github.com/${u}`;
  else if (/^(github|gitlab|bitbucket)\.(com|org)\//i.test(u)) u = `https://${u}`;
  return u.replace(/^http:\/\//i, "https://");
};

/** A repo agent is its repository + entry file — a monorepo holds several. */
export const repoAgentKey = (url, entry) => `repo:${normalizeRepoUrl(url).toLowerCase()}${entry ? `#${String(entry).toLowerCase()}` : ""}`;

/** Stable identity across environments — provider + where it lives. Once
    an agent has a key it keeps it, even when a later version reaches it a
    different way. */
export const agentKeyOf = (agent) => {
  if (agent?.key) return agent.key;
  const v = agent?.values || {};
  const d = agent?.definition || {};
  /* Same rule as agentRecordFromSource, wherever it was connected: a hosted
     assistant is its provider + assistant ID — a linked repository is an
     attribute, not who it is; a repo agent is its repository + entry. */
  if (v.provider && v.provider !== "other" && v.agentId) return `${v.provider}:${String(v.agentId).toLowerCase()}`;
  if (v.phoneNumber) return `phone:${String(v.phoneNumber).replace(/[^\d+]/g, "")}`;
  const def = definitionOf(agent);
  if (def.kind === "repo" && def.repoUrl) return repoAgentKey(def.repoUrl, def.entry);
  const where = d.repoUrl || v.repoUrl || v.endpoint || v.sdkEndpoint || v.mcpUrl || agent?.location || null;
  return where ? `${v.provider || agent?.sourceKind || "agent"}:${String(where).toLowerCase()}` : null;
};

/** An environment's agent always has an identity — when its connection
    doesn't name where it lives, the environment it was connected for does. */
export const envAgentKey = (agent, envId) => (agent ? agentKeyOf(agent) || `env:${envId}` : null);

export const PROVIDER_LABELS = {
  vapi: "Vapi", retell: "Retell AI", bland: "Bland.ai", elevenlabs: "ElevenLabs", livekit: "LiveKit",
  telnyx: "Telnyx", google_cx: "Google CX Agent Studio", voiceflow: "Voiceflow", agentforce: "Salesforce Agentforce", copilot_studio: "Microsoft Copilot Studio",
  openai_assistants: "OpenAI Assistants", langgraph: "LangGraph Cloud", crewai: "CrewAI", claude_agents: "Claude Agents",
  github: "GitHub", gitlab: "GitLab", bitbucket: "Bitbucket", other: "Custom platform",
  builtin: "Future AGI",
};

/*
  Frameworks our SDK adapter wraps. The adapter serves the agent and reports
  its structure — a graph or pipeline, agents with handoffs, or a crew.
    structure  what the adapter reports, in the framework's own words
    tuning     its row in the tuning table (agentLifecycle FRAMEWORK_TUNING),
               which says what improvements edit
    lang       "typescript" for the TypeScript-first frameworks
    traced     false when traceAI doesn't instrument it — its production
               traces reach the Error Feed another way
    logo       a bundled brand mark (connectOptions PLATFORM_LOGOS), where one exists
*/
export const FRAMEWORK_META = {
  langgraph: { label: "LangGraph", icon: "simple-icons:langgraph", structure: "Graph", tuning: "LangGraph / LangChain" },
  "openai-agents": { label: "OpenAI Agents SDK", icon: "simple-icons:openai", structure: "Agents & handoffs", tuning: "OpenAI Agents SDK" },
  crewai: { label: "CrewAI", icon: "simple-icons:crewai", structure: "Crew", tuning: "CrewAI and Claude Agent SDK" },
  "claude-agent-sdk": { label: "Claude Agent SDK", icon: "simple-icons:claude", structure: "Agent & subagents", tuning: "CrewAI and Claude Agent SDK" },
  /* A code framework, not a hosted platform: improvements come through the
     code. A running LiveKit agent can still be connected by its project, for
     testing only (connectOptions HOSTED_PLATFORMS_BY_TYPE). */
  livekit: { label: "LiveKit Agents", icon: "solar:soundwave-linear", logo: "livekit", structure: "Agent session", tuning: "LiveKit Agents", voice: true },
  pipecat: { label: "Pipecat", icon: "simple-icons:pipecat", structure: "Pipeline", tuning: "Pipecat", voice: true },
  "google-adk": { label: "Google ADK", icon: "simple-icons:google", structure: "Agents & sub-agents", tuning: "Google ADK" },
  mastra: { label: "Mastra", icon: "thesvg:mastra", structure: "Agents & workflows", tuning: "Mastra and Vercel AI SDK", lang: "typescript" },
  "vercel-ai": { label: "Vercel AI SDK", icon: "simple-icons:vercel", structure: "Agent & tools", tuning: "Mastra and Vercel AI SDK", lang: "typescript" },
  strands: { label: "AWS Strands", icon: "simple-icons:amazonwebservices", structure: "Agents as tools", tuning: "AWS Strands and Pydantic AI" },
  "pydantic-ai": { label: "Pydantic AI", icon: "simple-icons:pydantic", structure: "Agents & delegation", tuning: "AWS Strands and Pydantic AI" },
  agno: { label: "Agno", icon: "fagi:agno", structure: "Team", tuning: "Agno" },
  haystack: { label: "Haystack", icon: "simple-icons:haystack", structure: "Pipeline", tuning: "Haystack" },
  llamaindex: { label: "LlamaIndex", icon: "thesvg:llamaindex", structure: "Workflow", tuning: "LlamaIndex" },
  smolagents: { label: "smolagents", icon: "simple-icons:huggingface", structure: "Agents & managed agents", tuning: "smolagents" },
  dspy: { label: "DSPy", icon: "fagi:dspy", structure: "Program", tuning: "DSPy" },
  "microsoft-agent-framework": { label: "Microsoft Agent Framework", icon: "simple-icons:microsoft", structure: "Agents & workflow", tuning: "Microsoft Agent Framework", traced: false },
};

/** The framework id for a scanned framework name ("Google ADK" → "google-adk"), or null. */
export const frameworkIdOf = (name) => {
  const n = String(name || "").toLowerCase();
  if (!n) return null;
  return Object.keys(FRAMEWORK_META).find((id) => id === n || FRAMEWORK_META[id].label.toLowerCase() === n) || null;
};

/*
  An agent has two independent facts, and the environment flow keeps them
  apart:
    definition — where we READ what the agent is made of (a repo at a ref,
                 the platform's config, a prompt pasted in, an uploaded
                 bundle). Decides what the Files tab can show and whether
                 Sync means anything.
    runtime    — how we REACH it to run a simulation (the platform's API or
                 phone line, an HTTP endpoint, the SDK). Making the copy a
                 simulation drives is the platform's job, not the user's
                 (see replication.js): code is built in our sandbox or run
                 through the SDK, a hosted assistant is cloned or exported,
                 and only an opaque endpoint is driven as it is.
  A hosted platform is both at once. A bare endpoint is a runtime with no
  readable definition.
*/
export const definitionOf = (agent) => {
  if (agent?.definition) {
    /* What the person corrected on "Check what we found" reads as the record. */
    const fixes = agent.values?.foundCorrections;
    return fixes ? { ...agent.definition, ...fixes } : agent.definition;
  }
  const v = agent?.values || {};
  const sk = agent?.sourceKind;
  if (sk === "builtin") return { kind: "builtin" };
  if (sk === "framework") return { kind: "framework", framework: v.framework || null };
  if (v.repoUrl || sk === "repo") return { kind: "repo", repoUrl: v.repoUrl || null, ref: agent?.ref?.value || v.ref || null };
  if (sk === "upload") return { kind: "upload", bundle: agent?.location || null };
  if (sk === "mcp" || v.mcpUrl) return { kind: "mcp" };
  if (sk === "platform" || (v.provider && v.agentId) || agent?.typeId === "voice_platform" || agent?.typeId === "chat_openai") {
    return { kind: "platform" };
  }
  return { kind: "none" };
};

/*
  The reading surface, which is what the Files tab and Sync key off:
    platform — a managed assistant; its full config is one API call away
    repo     — code at a ref; prompt files, model config and tool defs
    prompt   — instructions pasted in when it was connected
    upload   — code read once from a bundle; nothing to re-read
    endpoint — a black box we drive; we see what it does, not how
    builtin  — built in Future AGI: its prompt, tools and model are defined
               here and it runs here, so there is nothing external to read
    framework — code wrapped by our SDK adapter (LangGraph, OpenAI Agents
               SDK, CrewAI): the adapter serves it and reports its structure,
               prompts, tools and model, so it reads like a repository
    mcp      — legacy: environments that read tools from an MCP manifest
*/
export const sourceKindOf = (agent) => {
  const d = definitionOf(agent).kind;
  if (d === "none") return "endpoint";
  /*
    A prompt pasted in isn't a source of its own (RSI PRD, "Ways an agent
    comes in": GitHub, upload, Vapi or Retell, built in Future AGI). On a
    hosted platform we can reach by its agent ID it's that platform — we
    import its configuration through the API. Otherwise the prompt runs here,
    which is an agent built in Future AGI.
  */
  if (d === "prompt") {
    const v = agent?.values || {};
    if (v.provider && v.provider !== "other" && v.agentId) return "platform";
    /* A number we dial or an endpoint we call, with its instructions pasted
       as the reference — still an agent we can only call, not one built here. */
    if (v.phoneNumber || v.endpoint) return "endpoint";
    return "builtin";
  }
  return d;
};

const SOURCE_META = {
  platform: { icon: "solar:cloud-linear" },
  repo: { icon: "solar:code-linear", label: "Source repository" },
  prompt: { icon: "solar:document-text-linear", label: "Pasted prompt" },
  upload: { icon: "solar:cloud-upload-linear", label: "Code upload" },
  endpoint: { icon: "solar:global-linear", label: "Endpoint" },
  builtin: { icon: "solar:widget-add-linear", label: "Built in Future AGI" },
  /* Label and icon come from the framework; this is the fallback. */
  framework: { icon: "solar:layers-minimalistic-linear", label: "SDK adapter" },
  mcp: { icon: "solar:plug-circle-linear", label: "MCP manifest" },
  /* Read from the agent's own production traces: its prompt, model and tools,
     as the spans report them. Where it runs isn't known yet. */
  traces: { icon: "solar:pulse-2-linear", label: "Read from traces" },
};

/** How we reach the agent when a simulation runs. */
export const runtimeOf = (agent) => {
  const v = agent?.values || {};
  /* Read from traces: we have its prompt and tools, not a way to call it —
     it runs as that prompt on our model until its home is connected. */
  if (definitionOf(agent).kind === "traces") {
    return { kind: "platform", label: "Its prompt and tools, on our model", where: "until you connect where it runs", by: "web call" };
  }
  /* Built here, so it runs here — nothing of theirs to call or deploy. */
  if (definitionOf(agent).kind === "builtin") {
    return { kind: "platform", label: "Runs in Future AGI", where: "nothing to deploy", by: v.contact?.mode === "phone" ? "phone" : v.contact ? "web call" : null };
  }
  if (v.provider && v.agentId) {
    const by = v.contact?.mode === "phone" ? `phone${v.contact.number ? ` ${v.contact.countryCode || ""} ${v.contact.number}` : ""}` : v.contact?.mode === "web" ? "web call" : null;
    return { kind: "platform", label: `${PROVIDER_LABELS[v.provider] || "Hosted platform"} API`, where: v.agentId, by };
  }
  /* A pasted prompt runs here — an agent built in Future AGI — unless it is
     the reference for a number we dial or an endpoint we call. */
  if ((v.provider === "other" || definitionOf(agent).kind === "prompt") && !v.phoneNumber && !v.endpoint) {
    const by = v.contact?.mode === "phone" ? `phone${v.contact.number ? ` ${v.contact.countryCode || ""} ${v.contact.number}` : ""}` : "web call";
    return { kind: "platform", label: "Runs in Future AGI", where: "nothing to deploy", by };
  }
  /* A phone number we dial — test only. */
  if (v.phoneNumber) return { kind: "endpoint", label: "Phone number", where: `${v.countryCode || ""} ${v.phoneNumber}`.trim(), by: "phone" };
  const url = v.endpoint || v.sdkEndpoint;
  /* The adapter's serve() — still an endpoint we call, but one that speaks
     the framework's own run loop. */
  if (url && definitionOf(agent).kind === "framework") return { kind: "endpoint", label: "SDK adapter", where: url };
  if (url) return { kind: "endpoint", label: "HTTP endpoint", where: url };
  if (v.mcpUrl) return { kind: "endpoint", label: "MCP server", where: v.mcpUrl };
  return { kind: "none", label: "Not recorded", where: "" };
};

/**
 * A framework agent served by our adapter, with no repository to build it
 * from and nowhere to reach it — connected and versioned, but a simulation has
 * nothing to drive yet. Code from a repository or an upload needs nothing: we
 * build it fresh in the hosted sandbox (your own machine is Coming soon).
 */
export const agentNeedsRuntime = (agent) => {
  if (!agent) return false;
  const kind = sourceKindOf(agent);
  return kind === "framework" && !definitionOf(agent).repoUrl && !runtimeOf(agent).where;
};

/**
 * A code agent (repository, upload or framework adapter) connected through
 * New agent whose "Check what we found" isn't done yet: the person confirms
 * or corrects each item we read before it runs or is improved (RSI PRD
 * Flow 1). Agents connected before that step existed aren't held back.
 */
export const needsFoundCheck = (agent) => !!agent
  && ["repo", "upload", "framework"].includes(sourceKindOf(agent))
  && !!agent.values?.setupStartedAt
  && !agent.values?.foundCheckedAt;
/* The one part of "Check what we found" a live run can't go without: which
   tools must never run for real. The rest is optional to check. */
export const needsToolSafety = (agent) => needsFoundCheck(agent) && !agent.values?.toolSafetyAt;

/** { kind, label, icon, syncable, where, runtime } — where we read it, whether we can re-read it, and how we reach it. */
export const agentSourceOf = (agent) => {
  const kind = sourceKindOf(agent);
  const v = agent?.values || {};
  const d = definitionOf(agent);
  const meta = SOURCE_META[kind] || SOURCE_META.endpoint;
  /* Linked through GitHub when its repository is in an installation right
     now — however it was connected. If the installation went away, the agent
     stays but can't be re-read, pushed to or fixed by PR. */
  const repoName = d.repoUrl ? repoNameOf(normalizeRepoUrl(d.repoUrl)).toLowerCase() : null;
  const viaApp = (kind === "repo" || kind === "framework") && !!repoName && installedRepos().some((r) => r.name.toLowerCase() === repoName);
  const appLost = d.app === "github" && !viaApp;
  /* GitLab and Bitbucket the same way, through the workspace's connection to that host. */
  const host = (kind === "repo" || kind === "framework") ? gitHostOfUrl(d.repoUrl) : null;
  const otherHost = host === "gitlab" || host === "bitbucket" ? host : null;
  const viaHost = !!otherHost && hostRepoVisible(d.repoUrl);
  const hostLost = !!otherHost && d.app === otherHost && !viaHost;
  /* A framework agent is named for its framework, wherever its code lives. */
  const fw = kind === "framework" ? FRAMEWORK_META[d.framework] : null;
  const label = kind === "platform"
    ? (PROVIDER_LABELS[v.provider] || getAgentType(agent?.typeId)?.label || "Hosted platform")
    : fw ? fw.label
      : viaApp ? "GitHub" : appLost ? "GitHub · disconnected"
        : viaHost ? GIT_HOST_LABELS[otherHost] : hostLost ? `${GIT_HOST_LABELS[otherHost]} · disconnected`
          : otherHost ? `${GIT_HOST_LABELS[otherHost]} · not connected`
        /* A GitHub repository the app can't see — read once, can't sync or open PRs. */
        : kind === "repo" && /github\.com/i.test(d.repoUrl || "") ? "GitHub · app not installed" : meta.label;
  const runtime = runtimeOf(agent);
  const where = kind === "repo" ? (d.repoUrl ? `${d.repoUrl}${d.ref ? ` @ ${d.ref}` : ""}` : "repository not recorded")
    : kind === "framework" ? [d.repoUrl && repoNameOf(normalizeRepoUrl(d.repoUrl)), d.entry || "entry not recorded"].filter(Boolean).join(" · ")
    : kind === "upload" ? (d.bundle || "bundle")
      : kind === "platform" ? (v.agentId || "")
        : kind === "prompt" ? "pasted when connected"
          : kind === "builtin" ? (d.kind === "prompt" ? "prompt written here" : `${d.model || "model not set"} · ${(d.tools || []).length} tool${(d.tools || []).length === 1 ? "" : "s"}`)
            : kind === "traces" ? `Observe · ${d.project || "project"}`
            : runtime.where;
  /* Sync re-reads the definition, so it needs one we can read again — a
     framework agent is re-read by asking its adapter, wherever it runs. */
  const syncable = kind === "platform" ? !!v.agentId : kind === "repo" ? !!d.repoUrl && !appLost && !hostLost
    : kind === "framework" ? !!runtime.where : kind === "mcp";
  /* A platform agent that lost its assistant ID — it can be re-read again
     once the ID is back, so this is "reconnect", not "can't be synced". */
  const idMissing = kind === "platform" && !v.agentId && v.provider !== "other";
  const icon = fw ? fw.icon : viaApp || appLost ? "mdi:github" : otherHost ? `mdi:${otherHost}` : meta.icon;
  /* A phone number is named for how we reach it, whatever it pasted. */
  if (v.phoneNumber) {
    return { kind, label: v.closedPlatform ? `${CLOSED_LABELS[v.closedPlatform] || "Closed platform"} · phone` : "Phone number", icon: "solar:phone-linear", syncable: false, where: runtime.where, runtime, viaApp: false, appLost: false, idMissing: false, phone: true };
  }
  /* A closed platform reached by its chat channel or conversation API. */
  if (v.closedPlatform) {
    return { kind, label: `${CLOSED_LABELS[v.closedPlatform] || "Closed platform"} · ${v.closedReach === "api" ? "conversation API" : "chat"}`, icon: "solar:chat-round-dots-linear", syncable: false, where: runtime.where, runtime, viaApp: false, appLost: false, idMissing: false };
  }
  return { kind, label, icon, syncable, where, runtime, viaApp, appLost, idMissing, ...(otherHost && { gitHost: otherHost, viaHost, hostLost }), ...(fw && { framework: d.framework }) };
};

const CLOSED_LABELS = { sierra: "Sierra", decagon: "Decagon", intercom_fin: "Intercom Fin", zendesk: "Zendesk" };

const tail = (s) => String(s || "").replace(/\/+$/, "").split("/").pop();

/** A readable name — what the person would call this agent. */
export const agentNameOf = (agent) => {
  if (agent?.name) return agent.name;
  const v = agent?.values || {};
  const d = definitionOf(agent);
  if (d.repoUrl) return tail(d.repoUrl);
  if (v.provider && v.agentId) return v.agentId;
  if (v.phoneNumber) return `Phone line ${`${v.countryCode || ""} ${v.phoneNumber}`.trim()}`;
  const url = v.endpoint || v.sdkEndpoint || v.mcpUrl;
  if (url) {
    try { return new URL(url).hostname; } catch { return url; }
  }
  return agent?.location || "Connected agent";
};

/* ── turning a connect form into an agent record ──────────────────────────── */

/* A credential the form captured: present, and — when it came as one — the
   secret-manager reference it resolves through. A legacy raw value only
   marks the flag; it is never copied onto the record. */
const secretFlag = (flag, refKey, ref, legacy) => {
  const r = typeof ref === "string" ? ref.trim() : "";
  if (r) return { [flag]: true, [refKey]: r };
  return legacy ? { [flag]: true } : {};
};

/**
 * The connect panels hand back a `source` object (the same one the
 * environment builder receives). Turned into the agent record every other
 * surface reads — keeping everything the form asked for, because a field
 * the person filled in and we threw away is a field they'll fill in again.
 * Secrets are noted as present, never stored in the prototype.
 */
export const agentRecordFromSource = (source, now = new Date().toISOString()) => {
  const kind = source?.kind || "repo";
  const voice = source?.agentType === "voice";
  const runtime = source?.runtime?.endpoint ? { endpoint: source.runtime.endpoint.trim(), ...secretFlag("hasToken", "tokenRef", source.runtime.tokenRef, source.runtime.token) } : {};
  const linkedRepo = (source?.linkedRepo || source?.repoUrl || "").trim();
  let record;

  if (kind === "platform") {
    const pasted = source?.agentMode === "prompt";
    record = {
      typeId: voice || !source?.agentType ? "voice_platform" : "chat_openai",
      values: {
        provider: source?.provider || "vapi",
        ...(!pasted && { agentId: (source?.agentId || source?.value || "").trim() }),
        ...secretFlag("hasApiKey", "apiKeyRef", source?.apiKeyRef, source?.apiKey),
        ...(source?.callDirection && { callDirection: source.callDirection }),
        ...(source?.contact && { contact: source.contact }),
        /* The chat channel a simulation reaches it on (ElevenLabs WhatsApp, Bland messaging). */
        ...(source?.chatChannel && { chatChannel: source.chatChannel }),
        /* The model the assistant runs, when listing the account told us. */
        ...(!pasted && source?.model && { model: source.model }),
        /* Watching production: on straight away for a hosted agent — we pull
           its calls with the provider key. */
        ...(!pasted && source?.observability && { observability: { on: true, since: now, project: source?.observeProject?.id || slugOf(source?.name || source?.agentName || source?.agentId) } }),
      },
      definition: pasted
        ? { kind: "prompt", prompt: source?.prompt || "" }
        : linkedRepo ? { kind: "platform", repoUrl: normalizeRepoUrl(linkedRepo) } : { kind: "platform" },
    };
  } else if (kind === "endpoint" && source?.phone) {
    /* A phone number: we dial it, with the pasted instructions as the
       reference for what it should do. Test only. */
    const number = String(source.phoneNumber || source.value || "").replace(/^tel:/, "").trim();
    record = {
      typeId: "voice_sip",
      values: {
        phoneNumber: number,
        ...(source.countryCode && { countryCode: source.countryCode }),
        /* Inbound: we call its number. Outbound: it calls our test number. */
        callDirection: source.callDirection === "outbound" ? "outbound" : "inbound",
        contact: {
          mode: "phone-only", number, ...(source.countryCode && { countryCode: source.countryCode }),
          inboundCalls: source.callDirection !== "outbound",
          ...(typeof source.agentSpeaksFirst === "boolean" && { agentSpeaksFirst: source.agentSpeaksFirst }),
          ...(source.testNumber && { testNumber: source.testNumber }),
        },
        ...(source.closedPlatform && { closedPlatform: source.closedPlatform }),
      },
      definition: source.systemPrompt ? { kind: "prompt", prompt: source.systemPrompt } : { kind: "none" },
    };
  } else if (kind === "endpoint") {
    record = {
      typeId: voice ? "voice_sip" : "chat_webhook",
      values: {
        endpoint: (source?.value || "").trim(),
        ...secretFlag("hasToken", "tokenRef", source?.tokenRef, source?.token),
        /* HTTP or WebSocket, and where in its response the reply sits. */
        ...(source?.transport && { transport: source.transport }),
        ...(source?.replyPath && { replyPath: source.replyPath }),
        ...(source?.callDirection && { callDirection: source.callDirection }),
        /* A closed platform's chat channel or conversation API — test only. */
        ...(source?.closedPlatform && { closedPlatform: source.closedPlatform, closedReach: source.closedReach || "chat" }),
        /* An endpoint needs the team to add tracing — recorded as asked for,
           and on once they confirm the snippet is in. */
        ...(source?.observability && { observability: { on: false, requested: true } }),
      },
      definition: linkedRepo ? { kind: "repo", repoUrl: normalizeRepoUrl(linkedRepo), ref: "main" }
        : source?.systemPrompt ? { kind: "prompt", prompt: source.systemPrompt }
          : { kind: "none" },
    };
  } else if (kind === "traces") {
    /* Saved from its production traces: the prompt, model and tools the spans
       report, and the Observe project they come from — watched from the start. */
    record = {
      typeId: voice ? "voice_platform" : "chat_openai",
      values: {
        observability: { on: true, since: now, project: source?.project || null },
        fromTraces: { project: source?.project || null, traces: source?.traceCount || null, framework: source?.framework || null, language: source?.language || null },
      },
      definition: {
        kind: "traces",
        project: source?.project || null,
        prompt: source?.prompt || "",
        model: source?.model || null,
        tools: source?.tools || [],
        ...(source?.framework && { framework: source.framework }),
      },
    };
  } else if (kind === "builtin") {
    /* Defined here — instructions, tools and model — and run here. */
    record = {
      typeId: voice ? "voice_platform" : "chat_openai",
      values: { ...(source?.contact && { contact: source.contact }) },
      definition: {
        kind: "builtin",
        prompt: source?.prompt || "",
        model: source?.model || "gpt-4o",
        ...(source?.temperature != null && { temperature: source.temperature }),
        tools: source?.tools || [],
      },
    };
  } else if (kind === "repo") {
    record = {
      typeId: "framework_agent",
      values: runtime,
      definition: {
        kind: "repo", repoUrl: normalizeRepoUrl(source?.value), ref: source?.ref || "main", provider: source?.provider, entry: source?.entry || null,
        ...(source?.githubApp && { app: "github", framework: source.framework }),
        /* GitLab or Bitbucket: read through the workspace's connection to that host. */
        ...(source?.hostConnection && { app: source.hostConnection, framework: source.framework }),
        /* What the person confirmed (or corrected) on "Here's what we found". */
        ...(source?.folder && { folder: source.folder }),
        ...(source?.prompts?.length && { prompts: source.prompts }),
        ...(source?.tools?.length && { tools: source.tools }),
        ...(source?.model && { model: source.model }),
        ...(source?.runCommand && { runCommand: source.runCommand }),
      },
    };
  } else if (kind === "framework") {
    /* Wrapped by the SDK adapter: serve() is how we reach it, and what the
       adapter reported — structure, prompts, tools, model — is the version. */
    record = {
      typeId: "framework_agent",
      values: runtime,
      definition: {
        kind: "framework", framework: source?.framework || "langgraph", entry: source?.entry || null,
        ...(source?.graph && { graph: source.graph }),
        ...(source?.agents && { agents: source.agents }),
        ...(source?.guardrails?.length && { guardrails: source.guardrails }),
        ...(source?.crew && { crew: source.crew }),
        ...(source?.prompts?.length && { prompts: source.prompts }),
        tools: source?.tools || [],
        ...(source?.model && { model: source.model }),
        /* Where its code lives, when they told us — improvements open a PR
           there; without it they come back as a patch. */
        ...(linkedRepo && { repoUrl: normalizeRepoUrl(linkedRepo), ref: source?.ref || "main", ...(source?.githubApp && { app: "github" }) }),
      },
    };
  } else {
    const bundle = (source?.files || []).map((f) => f.name).join(" + ") || source?.entry || "bundle";
    record = {
      typeId: "framework_agent",
      values: runtime,
      definition: { kind: "upload", bundle, entry: source?.entry || null },
    };
  }

  /* How the platform copies it, when the connect flow settled it: an
     override (a key without create scope runs on the exported definition),
     where a code agent runs, and the endpoint acknowledgment. */
  const runLocation = source?.runLocation || source?.runtime?.location;
  record.values = {
    ...record.values,
    ...(source?.replicationMode && { replicationMode: source.replicationMode }),
    ...(runLocation && { runLocation }),
    ...(source?.endpointConsent && { endpointConsent: source.endpointConsent }),
    /* What the person settled on "What we found" (code agents) and the
       platform steps (Phase 2 sources). */
    ...(source?.neverRunForReal && { neverRunForReal: source.neverRunForReal }),
    ...(source?.repoTests && { repoTests: source.repoTests }),
    ...(source?.testCredentialsRef && { testCredentialsRef: source.testCredentialsRef }),
    ...(source?.channels?.length && { channels: source.channels }),
    ...(source?.versioningOnAt && { versioningOnAt: source.versioningOnAt }),
    ...(source?.releaseDetection && { releaseDetection: source.releaseDetection }),
    /* Who it is and how it's reached — the New agent confirm step. */
    ...(source?.owner && { owner: source.owner }),
    ...(source?.languages?.length && { languages: source.languages }),
    ...(source?.description && { description: source.description }),
    ...(source?.reach && Object.keys(source.reach).length && { reach: source.reach }),
    /* What importing or reading it found. */
    ...(source?.retellKind && { retellKind: source.retellKind }),
    ...(source?.fagiPrompts?.length && { fagiPrompts: source.fagiPrompts }),
    ...(source?.gitHost && { gitHost: source.gitHost }),
    ...(source?.localSdk && { localSdk: true }),
    /* A hosted assistant imported into our sandbox with its tool code
       uploaded — tested as a sealed copy (rsiModel's proofKindOf). */
    ...(source?.importedCopy && { importedCopy: true }),
    ...(source?.toolCodeUploaded && { toolCodeUploaded: true }),
    ...(source?.toolCodeFiles?.length && { toolCodeFiles: source.toolCodeFiles }),
    /* Test credentials and settings the sandbox runs the code with —
       secrets as references only. */
    ...(source?.envValues?.length && { envValues: source.envValues.map((e) => (e.secret ? { name: e.name, secret: true, ref: e.ref } : { name: e.name, value: e.value })) }),
    /* A repository given by its link: who can read it, and how. */
    ...(source?.repoVisibility && { repoVisibility: source.repoVisibility }),
    /* A private repository read without the GitHub App: the access token or deploy key, by reference. */
    ...(source?.repoAccess?.ref && { repoAccess: { kind: source.repoAccess.kind || "token", ref: source.repoAccess.ref } }),
    ...(source?.credentialFiles?.length && { credentialFiles: source.credentialFiles.map((f) => ({ name: f.name, ref: f.ref })) }),
    /* Public hosts the sandbox may reach while it runs; everything else is firewalled. */
    ...(source?.allowedEgressDomains?.length && { allowedEgressDomains: source.allowedEgressDomains }),
    /* LiveKit doesn't expose the agent's prompt: the one pasted at connect seeds scenarios and the judge. */
    ...(source?.provider === "livekit" && source?.systemPrompt && { systemPrompt: source.systemPrompt }),
  };
  /* The Observe project picked at connect: an existing one is already
     receiving traces; a new one waits for them (a hosted agent's key pulls
     them straight away). */
  const observe = source?.observeProject;
  if (observe?.id) {
    const own = record.values.observability;
    record.values.observability = observe.mode === "existing"
      ? { ...(own || {}), on: true, since: own?.since || now, project: observe.id }
      : { ...(own || { on: false, requested: true }), project: observe.id };
  }
  /* Several agents working as one (a squad, a flow, handoffs), and what a
     code read found beyond prompts, tools and model. */
  record.definition = {
    ...record.definition,
    ...(source?.members?.length && { members: source.members }),
    ...(source?.language && { language: source.language }),
    ...(source?.installCommand && { installCommand: source.installCommand }),
    ...(source?.modelAtRuntime && { modelAtRuntime: true }),
    ...(source?.runtimePrompts?.length && { runtimePrompts: source.runtimePrompts }),
    ...(source?.credentialKeys?.length && { credentialKeys: source.credentialKeys }),
  };

  /* Identity never comes out empty: the definition's location when there is
     one, else how we reach it, else the name. No timestamps — connecting the
     same agent twice must land on the same row. */
  const d = record.definition;
  const v = record.values;
  /* A hosted assistant is its provider + ID, whatever repository it links;
     a repository can hold several agents — the entry file tells them apart;
     a pasted prompt is its instructions, not the name someone typed. */
  /* A framework agent in a repository is that repository + entry, the same
     agent a scan of the repo finds; otherwise its framework + where it's served. */
  const key = v.phoneNumber ? `phone:${v.phoneNumber.replace(/[^\d+]/g, "")}`
    : v.agentId ? `${v.provider}:${v.agentId.toLowerCase()}`
    : d.repoUrl ? repoAgentKey(d.repoUrl, d.entry)
      : kind === "framework" ? `framework:${d.framework}:${(v.endpoint || slugOf(source?.name)).toLowerCase()}${d.entry ? `#${d.entry.toLowerCase()}` : ""}`
      : kind === "upload" ? `upload:${d.bundle.toLowerCase()}${v.endpoint ? `@${v.endpoint.toLowerCase()}` : ""}`
        /* A built-in agent is its name and instructions — creating the same
           one twice lands on the same row. */
        : kind === "builtin" ? `builtin:${slugOf(source?.name)}-${hash(d.prompt).toString(36)}`
        : v.endpoint ? `endpoint:${v.endpoint.toLowerCase()}`
          : `${v.provider || kind}:prompt-${hash(`${d.prompt || ""}|${v.contact?.number || ""}`).toString(36)}`;

  const full = { ...record, sourceKind: kind === "platform" && d.kind === "prompt" ? "prompt" : kind, via: "endpoint", connectedAt: now };
  return { ...full, key, name: source?.name?.trim() || source?.agentName || agentNameOf(full) };
};

/** The reverse: an agent record as a build source for the environment builder. */
export const sourceFromAgentRecord = (agent) => {
  const kind = sourceKindOf(agent);
  const v = agent?.values || {};
  const d = definitionOf(agent);
  const voice = agent?.typeId === "voice_platform" || agent?.typeId === "voice_sip";
  const copy = {
    ...(v.replicationMode && { replicationMode: v.replicationMode }),
    ...(v.runLocation && { runLocation: v.runLocation }),
    ...(v.endpointConsent && { endpointConsent: v.endpointConsent }),
    ...(v.allowedEgressDomains?.length && { allowedEgressDomains: v.allowedEgressDomains }),
    ...(v.systemPrompt && { systemPrompt: v.systemPrompt }),
  };
  return { ...sourceOf(agent, kind, v, d, voice), ...copy };
};
const sourceOf = (agent, kind, v, d, voice) => {
  if (v.phoneNumber) {
    return { kind: "endpoint", phone: true, value: `tel:${v.phoneNumber}`, phoneNumber: v.phoneNumber, countryCode: v.countryCode, agentType: "voice", ...(d.prompt && { systemPrompt: d.prompt }), ...(v.closedPlatform && { closedPlatform: v.closedPlatform }) };
  }
  if (v.provider && v.agentId) {
    return {
      kind: "platform", provider: v.provider, agentId: v.agentId, value: v.agentId,
      agentType: voice ? "voice" : "chat", callDirection: v.callDirection, contact: v.contact,
      ...(d.repoUrl && { repoUrl: d.repoUrl }),
    };
  }
  if ((kind === "repo" || kind === "framework") && d.repoUrl) return { kind: "repo", provider: d.provider || "github", value: d.repoUrl, ref: d.ref || "main" };
  if (kind === "prompt") return { kind: "platform", provider: v.provider || "other", agentMode: "prompt", prompt: d.prompt, agentType: voice ? "voice" : "chat", contact: v.contact };
  /* Run here like a pasted prompt — the builder reads the same instructions. */
  if (kind === "builtin") return { kind: "platform", provider: "builtin", agentMode: "prompt", prompt: d.prompt, agentType: voice ? "voice" : "chat", contact: v.contact };
  return { kind: "endpoint", value: v.endpoint || v.sdkEndpoint || agent?.location || "", agentType: voice ? "voice" : "chat" };
};

/** URL-safe id for an agent's identity key. */
export const agentIdOf = (key) => `agt_${hash(key).toString(36)}`;

/* ── the merged list ──────────────────────────────────────────────────────── */

const versionsOfEnv = (es) => agentVersions(es).map((v, i) => (
  /* The implicit first version has no stored timestamp; anchor it to when the
     agent was connected so it doesn't claim "just now" on every render. */
  !es?.agentVersions?.length && i === 0 ? { ...v, createdAt: es?.agent?.connectedAt || v.createdAt } : v
));

const mergeVersions = (into, more) => {
  const byLabel = new Map(into.map((v) => [v.label, v]));
  more.forEach((v) => { if (!byLabel.has(v.label)) byLabel.set(v.label, v); });
  return [...byLabel.values()].sort((a, b) => versionNumber(a.label) - versionNumber(b.label));
};

/**
 * Every agent the workspace knows about, newest activity first.
 *   versions — union of every environment's history for this agent
 *   usedIn   — each environment testing it, and the version it runs
 */
/**
 * A hosted-platform agent can't be connected without its assistant ID — the
 * connect forms require it — so a stored platform agent without one is
 * leftover prototype data: an older version-save replaced the agent's
 * connection with the new version's (which carries no ID). Put it back:
 *   1. from the identity key, when it is "<provider>:<assistant id>"
 *   2. from the connection note ("Vapi assistant asst_…") or location
 *   3. otherwise a stable stand-in derived from the identity, so the record
 *      matches what the product guarantees rather than showing a state it
 *      can't reach
 * The identity is frozen on the record first (`fallbackKey` for an
 * environment's agent), so adding the ID never turns it into another agent.
 */
const ASSISTANT_ID = /\b((?:asst|agent|assistant|agt)_[A-Za-z0-9-]+)\b/;
export const repairAgentRecord = (agent, fallbackKey = null) => {
  if (!agent) return agent;
  const v = agent.values || {};
  if (v.provider === "other" || definitionOf(agent).kind !== "platform") return agent;
  /* Connecting a hosted agent requires its API key, so every one connected
     has it. Records saved before the key was tracked just lack the flag —
     fill it rather than ask for a key the user already gave. */
  if (v.agentId) return v.hasApiKey === undefined ? { ...agent, values: { ...v, hasApiKey: true } } : agent;
  const key = agent.key || agentKeyOf(agent) || fallbackKey;
  const m = /^([a-z_]+):(.+)$/.exec(key || "");
  const fromKey = m && PROVIDER_LABELS[m[1]] && m[1] !== "env" ? m : null;
  const fromNote = ASSISTANT_ID.exec(`${agent.via || ""} ${agent.location || ""}`);
  const agentId = fromKey?.[2] || fromNote?.[1] || `asst_${hash(key || agent.name || "agent").toString(36)}`;
  return {
    ...agent,
    ...(key && { key }),
    values: { ...v, provider: v.provider || fromKey?.[1] || "vapi", agentId, hasApiKey: v.hasApiKey ?? true },
  };
};

/* Deleted agents are tombstones: kept with their versions so history can
   open them, but off the Agents list and every picker unless asked for. */
export const connectedAgentsFrom = (state, { withDeleted = false } = {}) => {
  const map = new Map();
  (state?.connectedAgents || []).map((a) => repairAgentRecord(a)).forEach((a) => {
    map.set(a.key, { ...a, versions: [...(a.versions || [])], usedIn: [], origin: a.origin || "agents" });
  });
  (state?.myEnvironments || []).forEach((env) => {
    const raw = state?.byEnv?.[env.id];
    const es = raw?.agent ? { ...raw, agent: repairAgentRecord(raw.agent, `env:${env.id}`) } : raw;
    const key = envAgentKey(es?.agent, env.id);
    if (!key) return;
    const existing = map.get(key);
    const entry = existing || {
      key,
      /* An environment-connected agent has no name of its own; the
         environment it was connected for is what people call it. */
      name: es.agent.name || `${env.name} agent`,
      /* Named after its environment, not by anyone — two such agents can
         share a name, and get told apart below. */
      derivedName: !es.agent.name,
      /* A template's stand-in, not anyone's agent — kept off the Agents list.
         A world's sample agent is listed, tagged Sample. */
      placeholder: !!es.agent.seeded && !es.agent.sample,
      ...(es.agent.sample && { sample: true, sampleOf: es.agent.sampleOf }),
      typeId: es.agent.typeId,
      values: es.agent.values,
      sourceKind: es.agent.sourceKind,
      location: es.agent.location,
      definition: es.agent.definition,
      ref: es.agent.ref,
      connectedAt: es.agent.connectedAt,
      versions: [],
      usedIn: [],
      origin: "environment",
      firstEnvName: env.name,
    };
    /* Each environment keeps its own copy of the agent. A field one copy lost
       (an assistant ID, a definition) is filled from another, so the page
       never shows a copy that can't be re-read while another one can. */
    if (existing) {
      entry.values = { ...(es.agent.values || {}), ...(entry.values || {}) };
      Object.keys(entry.values).forEach((k) => {
        if ((entry.values[k] === undefined || entry.values[k] === "") && es.agent.values?.[k]) entry.values[k] = es.agent.values[k];
      });
      if (!entry.definition && es.agent.definition) entry.definition = es.agent.definition;
    }
    entry.versions = mergeVersions(entry.versions, versionsOfEnv(es));
    entry.usedIn.push({
      envId: env.id,
      envName: env.name,
      running: currentAgentVersion(es)?.label || "v1",
      /* Held on one version instead of following production. */
      pinned: isAgentPinned(es),
    });
    if (!existing) map.set(key, entry);
  });
  /* Environments that tested an agent before changing to another one. */
  (state?.myEnvironments || []).forEach((env) => {
    (state?.byEnv?.[env.id]?.previousAgents || []).forEach((p) => {
      const a = map.get(p.key);
      if (a && !a.usedIn.some((u) => u.envId === env.id)) {
        a.formerlyIn = [...(a.formerlyIn || []), { envId: env.id, envName: env.name, replacedAt: p.replacedAt }];
      }
    });
  });
  /* Two different agents named after same-named environments: add what
     tells them apart — the assistant ID or where the code lives — instead of
     showing the same name twice. Nothing is renamed in storage. */
  const nameCount = new Map();
  [...map.values()].forEach((a) => nameCount.set(a.name, (nameCount.get(a.name) || 0) + 1));
  map.forEach((a) => {
    if (!a.derivedName || nameCount.get(a.name) < 2) return;
    const v = a.values || {};
    const tell = v.agentId || (v.repoUrl && String(v.repoUrl).split("/").pop()) || v.endpoint || a.location || a.firstEnvName;
    /* Display only — the original name still names what's derived from it
       (its default Observe project). */
    if (tell) { a.baseName = a.name; a.name = `${a.name} · ${tell}`; }
  });
  /* Versions any environment has released — a draft stays a draft until
     its label is released somewhere (the PR merged, the copy went live,
     someone marked it released). */
  /* Per agent: labels repeat across agents ("v10" here isn't "v10" there),
     so only releases in environments testing this agent count. */
  const releasedByAgent = new Map();
  Object.entries(state?.byEnv || {}).forEach(([id, es]) => {
    const k = es?.agent ? envAgentKey(es.agent, id) : null;
    if (!k || !(es.releases || []).length) return;
    const set = releasedByAgent.get(k) || new Set();
    es.releases.forEach((r) => set.add(r.version));
    releasedByAgent.set(k, set);
  });
  return [...map.values()].map((a) => {
    if (!a.versions.length) a.versions = [{ id: "agent-v1", label: "v1", note: "First version connected.", createdAt: a.connectedAt, origin: "connect" }];
    const releasedLabels = releasedByAgent.get(a.key) || new Set();
    /* A proposal's version before anyone released it: on record, not live. */
    a.versions = a.versions.map((v) => ({ ...v, pendingDraft: v.status === "draft" && !releasedLabels.has(v.label) }));
    const latest = a.versions[a.versions.length - 1];
    /* What's in production: the version a workspace admin made active (a
       built-in agent's release), else the newest that isn't a pending draft —
       one answer for the header, lists, "behind" and the Versions tab. */
    const madeActive = a.values?.activeVersion?.label && a.versions.find((v) => v.label === a.values.activeVersion.label);
    const production = madeActive || [...a.versions].reverse().find((v) => !v.pendingDraft) || a.versions[0];
    /* Connected when its first version was — an environment's copy of the
       agent can carry a later stamp than the version history it holds. */
    const firstAt = a.versions[0]?.createdAt || a.versions[0]?.connectedAt;
    const connectedAt = [a.connectedAt, firstAt].filter(Boolean).sort((x, y) => Date.parse(x) - Date.parse(y))[0] || null;
    /* Every version read from the source — a sync, a push or a merged PR. */
    const lastSync = [...a.versions].reverse().find((v) => ["sync", "push", "pr"].includes(v.origin));
    return {
      ...a,
      connectedAt,
      id: agentIdOf(a.key),
      latest: latest?.label || "v1",
      latestIsDraft: !!latest?.pendingDraft,
      production: production?.label || "v1",
      lastSyncedAt: lastSync?.createdAt || null,
      updatedAt: latest?.createdAt || a.connectedAt,
      /* Behind production, not behind a draft nobody released yet. */
      usedIn: a.usedIn.map((u) => ({ ...u, behind: versionNumber(u.running) < versionNumber(production?.label) })),
    };
  }).filter((a) => withDeleted || !a.deleted?.at)
    .sort((a, b) => Date.parse(b.updatedAt || 0) - Date.parse(a.updatedAt || 0));
};

/* ── what a version is made of ────────────────────────────────────────────── */

const MODELS = ["gpt-4o", "gpt-4.1", "claude-sonnet-4", "gemini-2.5-pro"];
const VOICES = [
  { stt: "Deepgram nova-3", tts: "ElevenLabs turbo v2.5", voice: "Rachel · en-US" },
  { stt: "Deepgram nova-3", tts: "Cartesia sonic-2", voice: "Marcus · en-US" },
  { stt: "AssemblyAI universal", tts: "PlayHT 3.0", voice: "Ava · en-GB" },
];

const knowledgeFor = (env) => {
  const name = (env?.name || "Support").replace(/\s+line$/i, "");
  return [
    { name: `${name} policy.pdf`, meta: "12 pages" },
    { name: "Shipping & returns FAQ", meta: "48 entries" },
  ];
};

const UNSEEN = {
  endpoint: "Your agent runs behind an endpoint — we see what it does, not how it is configured.",
  upload: "Read once from the uploaded bundle.",
  local: "Read on your machine; nothing is uploaded.",
  mcp: "MCP servers publish tools and instructions, not the model behind them.",
  repo: "Not declared in the repository.",
  prompt: "Only the instructions were pasted in — the model and tools live wherever the agent runs.",
  builtin: "Nothing attached yet — a built-in agent reads only what's added to it here.",
  framework: "The adapter reports the graph, prompts and tools — not documents the agent retrieves at run time.",
};

/**
 * Every section of an agent version. A section is `{ visible, ... }`; when
 * the source can't show it, `visible` is false and `why` says so — the
 * reader should see the gap, not a blank.
 */
/* A roll back is a new version holding an older one's snapshot: what the
   history defines through it is that version's, plus anything newer. */
const resolveRollbacks = (list) => {
  const r = list.map((x) => !!x.rollbackOf).lastIndexOf(true);
  if (r < 0) return list;
  const target = list[r].rollbackOf;
  const base = resolveRollbacks(list.filter((x) => versionNumber(x.label) <= versionNumber(target)));
  /* The roll back's own pointers (its tools) stand; it changes nothing else. */
  const { changes: _c, ...self } = list[r];
  return [...base, self, ...list.slice(r + 1)];
};


/* Mirrors agentChannelsOf (agentLifecycle imports this file, so it can't be imported here). */
export const agentIsVoice = (agent) => {
  const ch = agent?.values?.channels;
  if (Array.isArray(ch) && ch.length) return ch.includes("voice");
  return ["voice_platform", "voice_sip"].includes(agent?.typeId) || !!agent?.values?.contact || !!agent?.values?.phoneNumber;
};

export const agentDefinitionOf = ({ agent, version, env, envState }) => {
  const kind = sourceKindOf(agent);
  const label = version?.label || currentAgentVersion(envState)?.label || "v1";
  const list = envState ? agentVersions(envState) : (agent?.versions || []);
  const upTo = resolveRollbacks(list.filter((v) => versionNumber(v.label) <= versionNumber(label)));
  const voice = agent?.typeId === "voice_platform" || agent?.typeId === "voice_sip" || env?.surface === "voice";
  const seed = hash(agentKeyOf(agent));
  const v = agent?.values || {};

  /* A pasted prompt edited in Add version carries its full new text — that
     becomes the base, and only later additions stack on it. */
  const promptAt = kind === "prompt" || kind === "builtin" || (kind === "platform" && definitionOf(agent).prompt) ? [...upTo].reverse().findIndex((x) => (x.changes || []).some((c) => typeof c.prompt === "string")) : -1;
  const editedPrompt = promptAt >= 0 ? [...upTo].reverse()[promptAt].changes.find((c) => typeof c.prompt === "string").prompt : null;
  const stacked = promptAt >= 0 ? upTo.slice(upTo.length - promptAt) : upTo;
  /* Changes synced into later versions stack on top of the base read. */
  /* A rule two versions both added (a sync, then a draft) reads once. */
  const addenda = [...new Set(stacked.flatMap((x) => (x.changes || []).filter((c) => c.addendum).map((c) => c.addendum)))];
  const modelChange = [...upTo].reverse().flatMap((x) => x.changes || []).find((c) => c.kind === "model");
  const voiceChange = [...upTo].reverse().flatMap((x) => x.changes || []).find((c) => c.kind === "voice");

  const def = definitionOf(agent);
  /* LiveKit doesn't expose the agent's prompt: the one pasted at connect is the one shown. */
  const pasted = kind === "platform" && !def.prompt && v.systemPrompt ? v.systemPrompt : null;
  const prompt = kind === "prompt" || kind === "builtin" || kind === "traces" || (kind === "platform" && (def.prompt || pasted))
    ? { text: editedPrompt ?? def.prompt ?? pasted ?? "", origin: editedPrompt != null ? "Edited in a later version" : pasted ? `Pasted when it was connected — ${PROVIDER_LABELS[v.provider] || "the platform"} doesn't expose its prompt` : kind === "platform" ? `Imported from ${PROVIDER_LABELS[v.provider] || "the platform"}` : kind === "traces" ? `Read from traces in ${def.project || "its Observe project"}` : "Written in Future AGI" }
    : agentPromptOf(
      { kind, value: def.repoUrl || agent?.location || v.endpoint || v.mcpUrl || "" },
      /* Voice or chat as the agent's Type says (agentChannelsOf), not the testing environment. */
      { ...env, surface: agentIsVoice(agent) ? "voice" : "chat", tools: env && envState ? agentToolsFor(env, envState, label) : env?.tools },
    );
  const instructionsVisible = kind === "platform" || kind === "repo" || kind === "mcp" || kind === "upload" || kind === "prompt" || kind === "builtin" || kind === "framework" || kind === "traces";
  const instructions = instructionsVisible
    ? {
      visible: true,
      text: [prompt.text, ...(addenda.length ? ["", "## Additional rules", ...addenda] : [])].join("\n"),
      origin: kind === "mcp" ? "Read from the MCP server's instructions"
        : kind === "framework" ? `Reported by the ${FRAMEWORK_META[def.framework]?.label || "SDK"} adapter`
          : prompt.origin,
    }
    : { visible: false, why: UNSEEN.endpoint };

  const modelVisible = kind === "platform" || kind === "repo" || kind === "upload" || kind === "builtin" || kind === "framework" || kind === "traces";
  const baseModel = v.model || def.model || MODELS[seed % 2];
  const model = modelVisible
    ? { visible: true, name: modelChange?.to || baseModel, temperature: def.temperature ?? 0.3, maxTokens: 1024 }
    : { visible: false, why: UNSEEN[kind] || UNSEEN.endpoint };

  const baseVoice = VOICES[seed % VOICES.length];
  const voiceSection = !voice
    ? null
    : kind === "platform" || kind === "builtin"
      ? { visible: true, ...baseVoice, ...(voiceChange?.voice && { voice: voiceChange.voice }), interruptions: "Allowed after 300ms" }
      : { visible: false, why: kind === "endpoint" ? UNSEEN.endpoint : "Voice settings live with the telephony provider, not in the source." };

  /* Not in an environment yet: what the source itself declares. Endpoints
     declare nothing — their tools only show up once runs observe them. */
  const declared = def.tools ? def.tools
    : kind === "endpoint" || kind === "prompt" ? [] : (getEnvironment("env-voice-support")?.tools || []).slice(0, 5);
  const ownTools = [...upTo].reverse().find((x) => x.tools)?.tools;
  const tools = env && envState ? agentToolsFor(env, envState, label) : (ownTools || env?.tools || declared);
  const toolsSection = {
    visible: true,
    observed: kind === "endpoint",
    items: tools.map((t) => ({ name: t.name, desc: t.desc || "", args: t.args || [] })),
  };

  const knowledge = kind === "platform"
    ? { visible: true, items: knowledgeFor(env) }
    : kind === "repo"
      ? { visible: true, items: [{ name: "policies.md", meta: "docs/ · indexed at build" }, { name: "faq.md", meta: "docs/ · indexed at build" }] }
      : { visible: false, why: kind === "mcp" ? "An MCP server publishes tools and instructions, not the documents the agent reads."
        : kind === "traces" ? "Traces show what the agent sent the model, not the documents it reads." : UNSEEN[kind] || UNSEEN.endpoint };

  const SECRET_FLAGS = { hasApiKey: "API key", hasToken: "Auth token" };
  /* Lifecycle and policy marks have their own rows on Connections. */
  const LIFECYCLE_KEYS = new Set(["mainEnvId", "versioningOnAt", "closedPlatform", "phoneNumber", "countryCode", "runLocation", "repoTests"]);
  const connection = Object.entries(v)
    /* The model is part of the definition (above), not how we reach it. */
    /* References and migration marks ride with their flag's row below. */
    /* Timestamps ("…At") and flags are bookkeeping, not how we reach it. */
    .filter(([k, val]) => val != null && val !== "" && typeof val !== "object" && typeof val !== "boolean" && !SECRET_FLAGS[k] && k !== "model" && !/(Ref|Migrated|At)$/.test(k) && !LIFECYCLE_KEYS.has(k))
    .map(([k, val]) => ({
      /* Sentence case, like every other label: "callDirection" → "Call direction". */
      label: k.replace(/([A-Z])/g, " $1").toLowerCase().replace(/^./, (c) => c.toUpperCase()),
      value: /key|token|secret|password/i.test(k) ? "••••••••" : k === "gitHost" ? GIT_HOST_LABELS[val] || String(val) : String(val),
      /* Identifiers read as code; plain words don't. */
      mono: /id$|url|endpoint/i.test(k),
    }));
  /* A credential shows the reference it resolves through — safe to show —
     or, for one connected before references, that it is held. */
  const SECRET_REFS = { hasApiKey: "apiKeyRef", hasToken: "tokenRef" };
  Object.entries(SECRET_FLAGS).forEach(([k, label]) => {
    const ref = v[SECRET_REFS[k]];
    if (v[k] || ref) connection.push({ label, value: ref || "Held by the connection · no reference recorded", ...(ref && { ref, mono: true }) });
  });
  if (kind === "builtin") connection.unshift({ label: "Runs in", value: "Future AGI — nothing to deploy" });
  if (v.contact) {
    connection.push({ label: "Simulated calls", value: v.contact.mode === "phone" ? `Phone${v.contact.number ? ` · ${v.contact.countryCode || ""} ${v.contact.number}` : ""}` : "Web call (WebRTC)" });
    if (v.contact.agentSpeaksFirst != null) connection.push({ label: "Agent speaks first", value: v.contact.agentSpeaksFirst ? "Yes" : "No" });
  }

  /* Several agents working together (a Vapi squad, a Retell or ElevenLabs
     flow, a code app with handoffs) are one agent: each member with its own
     instructions, model and tools. */
  const toolNames = (tools || []).map((t) => (typeof t === "string" ? t : t.name)).filter(Boolean);
  const members = (def.members || []).map((m, i) => ({
    id: m.id, name: m.name, role: m.role || "",
    /* A reference name ("Node: Welcome") isn't instructions — only a sentence is. */
    instructions: m.instructions && m.instructions !== m.promptRef && !/^(Node|Workflow node|Workflow|Topic|Flow):|· system prompt$/.test(m.instructions) ? m.instructions : memberInstructionsOf(m, i),
    model: m.model || baseModel,
    tools: m.tools?.length ? m.tools : memberToolsOf(m, i, toolNames),
  }));
  return {
    kind, label, instructions, model, voice: voiceSection, tools: toolsSection, knowledge, connection, structure: structureOf(def),
    ...(members.length && { members }),
    ...(kind === "framework" && { entry: def.entry || null }),
  };
};

/*
  A member the platform gave us by name and role only (a squad's
  assistants, a flow's nodes): its own instructions and its share of the
  agent's tools, read the way the platform splits them — the entry member
  checks who's calling and routes; the others get the tools their role
  names. Every member can hand off to a person.
*/
const STOP = new Set(["the", "and", "takes", "tracks", "opens", "calls", "with", "from", "node", "start", "entry", "topic", "workflow", "flow", "caller", "explains", "outcome", "what", "when", "where"]);
const stemOf = (w) => w.toLowerCase().replace(/[^a-z]/g, "").replace(/(ies)$/, "y").replace(/s$/, "");
const memberToolsOf = (m, i, toolNames) => {
  const entry = i === 0 || /entry|start|route|greet/i.test(m.role || "");
  const words = `${m.name} ${m.role || ""}`.split(/[\s·,/]+/).map(stemOf).filter((w) => w.length >= 4 && !STOP.has(w));
  const named = toolNames.filter((t) => words.some((w) => t.split("_").some((part) => stemOf(part).startsWith(w.slice(0, 5)) || w.startsWith(stemOf(part)) && stemOf(part).length >= 5)));
  const identity = entry ? toolNames.filter((t) => /verify|otp|identity|lookup/.test(t)) : [];
  const handoff = toolNames.filter((t) => /escalate|handoff|transfer/.test(t));
  return [...new Set([...identity, ...named, ...handoff])];
};
const memberInstructionsOf = (m, i) => {
  const role = String(m.role || "handles its part of the conversation").replace(/ · /g, " — ");
  return [
    `You are ${m.name}${i === 0 ? ", the first to speak with the caller" : ""}. Your role: ${role.charAt(0).toLowerCase()}${role.slice(1)}.`,
    i === 0
      ? "Find out what the caller needs, confirm who they are, then hand them to the member who handles it."
      : "Stay within that job. When the caller needs something else, hand them back with a one-line summary of where things stand.",
    "Only say something is done after the tool that does it has returned success.",
  ].join("\n");
};

/**
 * How a framework agent is put together, as the adapter reported it — a
 * LangGraph graph, OpenAI agents and their handoffs, or a CrewAI crew. Null
 * for every other kind: nothing else has a structure we can read.
 *   { label, summary, items: [{ name, detail }] }
 */
export const structureOf = (def) => {
  if (def?.kind !== "framework") return null;
  const label = FRAMEWORK_META[def.framework]?.structure || "Structure";
  if (def.graph) {
    return {
      label,
      summary: def.graph.flow,
      items: (def.graph.nodes || []).map((n) => ({ name: n.id, detail: n.prompt || (n.tools?.length ? `${n.tools.length} tools` : "no prompt") })),
    };
  }
  if (def.agents) {
    return {
      label,
      summary: def.agents.filter((a) => a.handoffs?.length).map((a) => `${a.name} → ${a.handoffs.join(" | ")}`).join(" · ") || `${def.agents.length} agents, no handoffs`,
      items: def.agents.map((a) => ({ name: a.name, detail: a.handoffs?.length ? `hands off to ${a.handoffs.join(", ")}` : `${(a.tools || []).length} tools` })),
    };
  }
  if (def.crew) {
    return {
      label,
      summary: `${def.crew.name} · ${def.crew.process} · ${def.crew.agents.length} agents · ${def.crew.tasks.length} tasks`,
      items: def.crew.agents.map((a) => ({ name: a.role, detail: a.goal })),
    };
  }
  return { label, summary: "Not reported", items: [] };
};

/* ── sync ─────────────────────────────────────────────────────────────────── */

const NEW_TOOLS = [
  { name: "check_loyalty_status", desc: "Look up the caller's loyalty tier before offering goodwill", args: ["customer_id"] },
  { name: "send_sms_receipt", desc: "Text the caller a summary of what was done", args: ["phone", "summary"] },
  { name: "lookup_store_hours", desc: "Answer opening hours for a store location", args: ["store_id"] },
];
/* The first touches a rule the seeded suites test (R1 on the support lines),
   so a sync can raise the fix-or-spec-change question. */
const ADDENDA = [
  "Refunds above $200 can be approved without a supervisor for loyalty-tier callers.",
  "If the caller mentions a chargeback, stop and hand over to a human.",
  "Never quote a delivery date the carrier hasn't confirmed.",
];
const NEW_VOICES = ["Sarah · en-US", "Leo · en-AU", "Ivy · en-US"];

/**
 * What re-reading the source finds. Deterministic per agent and version, so
 * the demo is repeatable: a source that hasn't been synced since its newest
 * version reports changes; syncing again straight after reports none.
 */
export const syncCheck = ({ agent, versions, env }) => {
  const source = agentSourceOf(agent);
  const latest = versions[versions.length - 1];
  if (!source.syncable) return { ok: false, reason: "no-source" };
  /* Connecting, a sync, a push or a merged PR all read the source as it
     stands — nothing new until it changes again. A credentials-only version
     doesn't touch the source, so it's read through to the one it's based on. */
  const read = [...versions].reverse().find((v) => v.kind !== "credentials" && v.status !== "draft" && !v.pendingDraft) || latest;
  if (["connect", "sync", "push", "pr"].includes(read?.origin)) return { ok: true, upToDate: true, latest: latest.label };
  return { ok: true, upToDate: false, ...changesSince({ agent, versions, env, source }) };
};

/* What changed in the source since the newest version. */
/* What a re-read builds on: the newest version that's live — a fix waiting
   as a draft (a pull request on its own branch, a platform draft) isn't in
   the source yet, so a sync never inherits its unmerged change. */
const readBaseOf = (versions) => [...versions].reverse().find((v) => v.status !== "draft" && !v.pendingDraft) || versions[versions.length - 1];

export const changesSince = ({ agent, versions, env, source }) => {
  const latest = readBaseOf(versions);
  const n = Math.max(0, ...versions.map((v) => majorOf(v.label))) + 1;
  const seed = hash(`${agentKeyOf(agent)}:${n}`);
  const known = new Set((latest?.tools || env?.tools || []).map((t) => t.name));
  const tool = NEW_TOOLS.find((t, i) => !known.has(t.name) && (seed + i) % 2 === 0) || NEW_TOOLS.find((t) => !known.has(t.name));
  const addendum = ADDENDA[seed % ADDENDA.length];
  const changes = [
    { kind: "instructions", title: "Instructions edited", detail: `Added: "${addendum}"`, addendum: `- ${addendum}` },
  ];
  /* A sync doesn't always bring a new tool — most changes to a live agent
     are wording and settings. One in three adds one, so tools don't pile up. */
  if (tool && seed % 3 === 0) changes.push({ kind: "tool", title: `Tool added · ${tool.name}`, detail: tool.desc, tool });
  if (source.kind === "platform" && (agent?.typeId === "voice_platform")) {
    changes.push({ kind: "voice", title: "Voice changed", detail: `Now speaking as ${NEW_VOICES[seed % NEW_VOICES.length]}`, voice: NEW_VOICES[seed % NEW_VOICES.length] });
  } else if (source.kind !== "mcp") {
    const from = MODELS[hash(agentKeyOf(agent)) % 2];
    const to = MODELS.find((m) => m !== from && MODELS.indexOf(m) === (seed % 3) + 1) || "gpt-4.1";
    changes.push({ kind: "model", title: "Model changed", detail: `${from} → ${to}`, to });
  }
  return { label: `v${n}`, basedOn: latest?.label || "v1", changes, source };
};

const COMMIT_MESSAGES = [
  "Tighten refund confirmation wording",
  "Hand chargebacks to a human",
  "Switch to the newer model for long calls",
  "Add order lookup by email",
  "Don't promise unconfirmed delivery dates",
];
const AUTHORS = ["maya.k", "devon", "priya.s", "sam-ops"];

/**
 * A push to the default branch that touched the agent — what the GitHub
 * webhook delivers when "Sync on push" is on. Saved like a sync, but carries
 * the commit so every version points at the exact code it was made from.
 */
export const pushedVersion = ({ agent, versions, env, baseTools, now = new Date().toISOString() }) => {
  const source = agentSourceOf(agent);
  const result = changesSince({ agent, versions, env, source });
  const seed = hash(`${agentKeyOf(agent)}:push:${result.label}`);
  const commit = {
    /* Low digits — the ones that move when only the version number changes. */
    sha: (Math.imul(seed, 2654435761) >>> 0).toString(16).padStart(8, "0").slice(-7),
    message: COMMIT_MESSAGES[seed % COMMIT_MESSAGES.length],
    author: AUTHORS[seed % AUTHORS.length],
    branch: definitionOf(agent).ref || "main",
  };
  return {
    ...syncedVersion({ result, baseTools, now }),
    origin: "push",
    commit,
    note: `Pushed ${commit.sha} by ${commit.author} — ${commit.message}.`,
  };
};

/** The version record a sync saves. */
export const syncedVersion = ({ result, baseTools, now = new Date().toISOString() }) => {
  const tool = result.changes.find((c) => c.kind === "tool")?.tool;
  return {
    id: `agent-${result.label}`,
    label: result.label,
    note: `Synced from ${result.source.label} — ${result.changes.map((c) => c.title.split(" · ")[0].toLowerCase()).join(", ")}.`,
    reach: "endpoint",
    createdAt: now,
    connectedAt: now,
    origin: "sync",
    /* Anything a re-read finds was changed outside Future AGI. */
    outsideChange: true,
    basedOnVersion: result.basedOn,
    changes: result.changes,
    ...(tool && { tools: [...(baseTools || []), tool] }),
  };
};
