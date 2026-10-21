/*
  New agent's ways in — the same directory and paste bar as the Environments
  landing, mapped onto the agent flows. Each item names the flow (`kind`) and
  what it starts with (`initial`): a platform, a framework, a pasted ID.
*/
import { FRAMEWORK_META } from "../../_mock/agentRegistry";
import { detectPaste } from "../../environments/landingData";
import { CLOSED_PLATFORM_REACH } from "../../environments/connectOptions";
import { CLOSED_PLATFORMS } from "../../_mock/rsiModel";

const frameworkItems = Object.entries(FRAMEWORK_META).map(([id, f]) => ({
  id: `fw-${id}`,
  name: f.label,
  icon: f.icon,
  ...(f.logo && { logo: f.logo }),
  need: `SDK adapter · ${f.structure.toLowerCase()}`,
  kind: "framework",
  fix: "Pull request",
  initial: { framework: id },
}));

/* Hosted platforms beyond the five with bundled logos — their brand mark
   from Iconify, or a generic one where none exists. */
const hosted = (id, name, channel, extra = {}) => ({
  id: channel === "chat" ? `${id}-chat` : id, name, kind: "hosted", fix: "In your account", channels: [channel], initial: { agentType: channel, platform: id }, ...extra,
});

/* Closed platforms: test only, by phone, chat or conversation API. */
const closedItems = CLOSED_PLATFORMS.map((c) => ({
  id: `closed-${c.id}`,
  name: c.label,
  icon: CLOSED_PLATFORM_REACH[c.id]?.icon || "solar:chat-round-dots-linear",
  need: `Test only · ${CLOSED_PLATFORM_REACH[c.id]?.ways.map((w) => ({ phone: "phone", chat: "chat", api: "API" })[w]).join(", ")}`,
  kind: "closed",
  fix: "Suggested change",
  /* Phone reaches a voice agent; chat and the API a chat agent. */
  channels: [...new Set(CLOSED_PLATFORM_REACH[c.id]?.ways.map((w) => (w === "phone" ? "voice" : "chat")))],
  initial: { closedPlatform: c.id },
}));

export const AGENT_CONNECT_GROUPS = [
  {
    id: "voice",
    title: "Voice platforms",
    reach: "Your API key and assistant — tested in your account on a copy, draft or branch.",
    items: [
      { id: "vapi", name: "Vapi", fix: "In your account", logo: "vapi", need: "API key · assistant", kind: "hosted", channels: ["voice"], initial: { agentType: "voice", platform: "vapi" } },
      { id: "retell", name: "Retell AI", fix: "In your account", logo: "retell", need: "API key · agent", kind: "hosted", channels: ["voice"], initial: { agentType: "voice", platform: "retell" } },
      { id: "elevenlabs", name: "ElevenLabs", fix: "In your account", logo: "elevenlabs", need: "Agents API key", kind: "hosted", channels: ["voice"], initial: { agentType: "voice", platform: "elevenlabs" } },
      { id: "bland", name: "Bland.ai", fix: "In your account", logo: "bland", need: "API key · agent", kind: "hosted", channels: ["voice"], initial: { agentType: "voice", platform: "bland" } },
      hosted("telnyx", "Telnyx", "voice", { icon: "solar:phone-calling-linear", need: "API key · assistant" }),
      hosted("google_cx", "Google CX Agent Studio", "voice", { icon: "simple-icons:dialogflow", need: "Service account · agent" }),
      hosted("voiceflow", "Voiceflow", "voice", { icon: "solar:routing-2-linear", need: "API key · project" }),
    ],
  },
  {
    id: "chat",
    title: "Chat and built here",
    reach: "The same platforms over chat, chat-first platforms, or one you build here.",
    items: [
      { id: "vapi-chat", name: "Vapi chat", fix: "In your account", logo: "vapi", need: "Same assistant, over chat", kind: "hosted", channels: ["chat"], initial: { agentType: "chat", platform: "vapi" } },
      hosted("retell", "Retell chat", "chat", { logo: "retell", need: "API key · chat agent" }),
      hosted("elevenlabs", "ElevenLabs chat", "chat", { logo: "elevenlabs", need: "Chat or WhatsApp agent" }),
      hosted("bland", "Bland messaging", "chat", { logo: "bland", need: "API key · messaging agent" }),
      hosted("google_cx", "Google CX Agent Studio", "chat", { icon: "simple-icons:dialogflow", need: "Service account · agent" }),
      hosted("voiceflow", "Voiceflow", "chat", { icon: "solar:routing-2-linear", need: "API key · project" }),
      hosted("agentforce", "Salesforce Agentforce", "chat", { icon: "simple-icons:salesforce", need: "Connected app · agent" }),
      hosted("copilot_studio", "Microsoft Copilot Studio", "chat", { icon: "simple-icons:microsoft", need: "Entra app · agent" }),
      { id: "builtin", name: "Build in Future AGI", icon: "solar:widget-add-linear", need: "Prompt, model and tools", kind: "builtin", channels: ["chat", "voice"] },
    ],
  },
  {
    id: "code",
    title: "Repo and SDK",
    reach: "We read your repo or SDK — fixes come back as pull or merge requests.",
    items: [
      { id: "github", name: "GitHub", fix: "Pull request", logo: "github", need: "Read-only app · repo and folder", kind: "repo", initial: { host: "github" } },
      { id: "gitlab", name: "GitLab", fix: "Pull request", logo: "gitlab", need: "Group token · repo and folder", kind: "repo", initial: { host: "gitlab" } },
      { id: "bitbucket", name: "Bitbucket", fix: "Pull request", logo: "bitbucket", need: "Workspace token · repo and folder", kind: "repo", initial: { host: "bitbucket" } },
      /* No framework: code that calls the model APIs itself. */
      { id: "direct-api", name: "OpenAI, Anthropic or Gemini calls", fix: "Pull request", icon: "solar:code-square-linear", need: "No framework · name where it starts", kind: "repo", initial: { host: "github", direct: true } },
      ...frameworkItems,
    ],
  },
  {
    id: "traces",
    title: "Traces",
    reach: "Already sending traces, or add traceAI now — we read the agent from its spans: prompt, model and tools.",
    items: [
      { id: "traces-project", name: "Traces in an Observe project", icon: "solar:pulse-2-linear", need: "Pick the project they land in", kind: "traces", fix: "Suggested change", initial: { mode: "project" } },
      { id: "traces-new", name: "Send traces with traceAI", icon: "solar:code-scan-linear", need: "A few lines in your agent's code", kind: "traces", fix: "Suggested change", initial: { mode: "send" } },
    ],
  },
  {
    id: "other",
    title: "Other ways",
    reach: "Anything we can call, upload or phone — and agents connected for testing only.",
    items: [
      { id: "upload", name: "Upload a folder", fix: "Patch", icon: "solar:upload-minimalistic-linear", need: "Folder or .zip, up to 200 MB", kind: "upload" },
      /* LiveKit Agents is a code framework (under Repo and SDK); a running
         agent can still be connected by its LiveKit project, for testing —
         its fixes end as a suggested change. */
      { id: "livekit", name: "LiveKit project", fix: "Suggested change", logo: "livekit", need: "Test only · server URL · agent name", kind: "hosted", channels: ["voice"], initial: { agentType: "voice", platform: "livekit" } },
      { id: "running", name: "Agent endpoint", fix: "Suggested change", icon: "solar:global-linear", need: "HTTP or WebSocket URL", kind: "running", channels: ["chat", "voice"] },
      { id: "phone", name: "Phone number", fix: "Suggested change", icon: "solar:phone-calling-rounded-linear", need: "Any agent you can call", kind: "phone", channels: ["voice"] },
      ...closedItems,
    ],
  },
];

/* Where an agent is connected for one environment (`channel`, e.g.
   "voice"), only the ways that can bring that type of agent: an option
   tagged with other channels drops out, and every option that's left opens
   with the agent type set. Code is untagged — the code says what it is. A
   group left with only "built here" is named for that. */
const CHANNEL_GROUP_TITLES = { chat: { voice: { title: "Built here", reach: "Defined and run in Future AGI. Fixes ship as a new version." } } };
export const connectGroupsFor = (channel) => (!channel ? AGENT_CONNECT_GROUPS : AGENT_CONNECT_GROUPS
  .map((g) => ({
    ...g,
    ...(CHANNEL_GROUP_TITLES[g.id]?.[channel] || {}),
    items: g.items
      .filter((it) => !it.channels || it.channels.includes(channel))
      .map((it) => ({ ...it, initial: { ...(it.initial || {}), agentType: channel } })),
  }))
  .filter((g) => g.items.length));

/*
  New agent opens on the PRD's three first choices (Flow 1): code — a GitHub
  repository or an upload — a third-party platform, or built in Future AGI.
  The same directory, with code first (the upload beside GitHub), and built
  here taken out of the chat column to stand as its own choice.
  { builtin, groups } — `builtin` is the directory item, or null.
*/
export const newAgentChoicesFor = (channel) => {
  const all = connectGroupsFor(channel);
  const items = all.flatMap((g) => g.items);
  const builtin = items.find((it) => it.kind === "builtin") || null;
  const upload = items.find((it) => it.kind === "upload") || null;
  const groups = all
    .map((g) => {
      if (g.id === "code") {
        const [first, ...rest] = g.items;
        return { ...g, title: "Code", reach: "A GitHub repository, an upload or an SDK adapter — fixes come back as pull requests, or a patch for an upload.", items: upload ? [first, upload, ...rest] : g.items };
      }
      return {
        ...g,
        ...(g.id === "chat" && { title: "Chat platforms", reach: "The same platforms over chat, and chat-first platforms." }),
        items: g.items.filter((it) => it.kind !== "builtin" && it.kind !== "upload"),
      };
    })
    .filter((g) => g.items.length);
  return { builtin, groups: [...groups.filter((g) => g.id === "code"), ...groups.filter((g) => g.id !== "code")] };
};

/* What most people start with — shown first when nothing is searched. */
export const POPULAR_IDS = ["github", "vapi", "retell", "traces-new", "running", "upload"];

export const pasteExamplesFor = (channel) => (!channel ? AGENT_PASTE_EXAMPLES : AGENT_PASTE_EXAMPLES.filter((e) => !e.channels || e.channels.includes(channel)));

/* Examples that make sense for an agent (no MCP — that's tools, not an agent). */
export const AGENT_PASTE_EXAMPLES = [
  { id: "github", label: "GitHub repo", logo: "github", value: "https://github.com/skyline-air/rebooking-agent" },
  { id: "vapi", label: "Vapi assistant ID", logo: "vapi", channels: ["voice"], value: "3f9c2ab1-4d7e-4c1a-9b2f-8e61d0a5c7e4" },
  { id: "retell", label: "Retell agent ID", logo: "retell", channels: ["voice"], value: "agent_9f2c41d8e7" },
  { id: "gitlab", label: "GitLab repo", logo: "gitlab", value: "https://gitlab.com/acme-inc/claims-voice" },
  { id: "endpoint", label: "Endpoint", icon: "solar:global-linear", channels: ["chat"], value: "https://api.acme.com/agent/chat" },
  { id: "prompt", label: "A prompt", icon: "solar:document-text-linear", channels: ["chat"], value: "You are a friendly refill assistant for Acme Pharmacy. Check insurance before booking a pickup slot." },
];

/* What a pasted string opens on New agent: the environment detector's
   reading, mapped onto the agent flows. `choice` is the flow kind. */
const KIND_OF_CHOICE = { hosted: "hosted", source: "repo", running: "running", phone: "phone", scratch: "builtin" };

export const detectAgentPaste = (raw) => {
  const found = detectPaste(raw);
  if (!found) return null;
  if (found.kind === "mcp") {
    return { ...found, blocked: true, choice: null, note: "That's tools, not an agent — build an environment from it instead." };
  }
  if (found.kind === "describe") {
    return { ...found, label: "A prompt", note: "We'll start an agent built in Future AGI with it.", choice: "builtin", prefill: { prompt: found.prefill.flowText } };
  }
  const kind = found.choice ? KIND_OF_CHOICE[found.choice] : null;
  return kind ? { ...found, choice: kind } : { ...found, choice: null, blocked: true };
};
