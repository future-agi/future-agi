/*
  Shared data for the "connect your own agent" panels — the environment
  builder's and the Agents page's New agent flow. Kept out of the .jsx so
  those files export components only.
*/
import { countries } from "src/assets/data/countries";
import vapiLogo from "./platform-logos/vapi.svg?raw";
import retellLogo from "./platform-logos/retell.svg?raw";
import blandLogo from "./platform-logos/bland.svg?raw";
import elevenlabsLogo from "./platform-logos/elevenlabs.svg?raw";
import livekitLogo from "./platform-logos/livekit.svg?raw";

/* Official brand marks for the hosted voice platforms, inlined so they inherit
   `currentColor` (theme-aware). Each vendor's own published logo, used to
   identify its integration — the same way the app labels its other service
   integrations. `wordmark` logos already contain the brand name (so the chip
   drops its text label); `mark` logos are a symbol only (chip keeps the name). */
export const PLATFORM_LOGOS = {
  vapi: { svg: vapiLogo, type: "wordmark", maxWidth: 46 },
  retell: { svg: retellLogo, type: "wordmark", maxWidth: 54 },
  bland: { svg: blandLogo, type: "mark" },
  elevenlabs: { svg: elevenlabsLogo, type: "mark" },
  livekit: { svg: livekitLogo, type: "mark" },
};

/* Hosted-platform providers grouped by agent type — the platform roster
   only makes sense once we know what kind of agent is being connected.
   Agents supports voice and chat; computer-use agents are listed as
   Coming soon. Frameworks (LangGraph, CrewAI, Claude Agent SDK,
   OpenAI Agents SDK…) aren't hosted platforms: they come in as code,
   through a repository or an upload. */
export const AGENT_TYPES = [
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "chat", label: "Chat", icon: "solar:chat-round-linear" },
  { id: "computer", label: "Computer use", icon: "solar:monitor-linear", comingSoon: true },
];

/*
  `icon` on a platform without a bundled logo is its brand mark from
  Iconify (or a generic one when none exists), shown in the platform chip.
  `chatChannels` — the chat channels one agent on that platform can be
  tested through; the one picked is recorded on the agent.
*/
/* eslint-disable no-restricted-syntax -- ds: data colour */
const TELNYX = { id: "telnyx", name: "Telnyx", icon: "solar:phone-calling-linear", brand: "#00C08B", idLabel: "Assistant ID", idPlaceholder: "assistant-7c1e9a42…", keyLabel: "Telnyx API key" };
const GOOGLE_CX = { id: "google_cx", name: "Google CX Agent Studio", icon: "simple-icons:dialogflow", brand: "#FF9800", idLabel: "Agent resource name", idPlaceholder: "projects/acme/locations/global/agents/…", keyLabel: "Service account key", idNote: "Dialogflow CX agents. The resource name is on the agent's settings page." };
const VOICEFLOW = { id: "voiceflow", name: "Voiceflow", icon: "solar:routing-2-linear", brand: "#1E1E1E", idLabel: "Project ID", idPlaceholder: "66f2c1d9e7…", keyLabel: "Voiceflow API key" };
const AGENTFORCE = { id: "agentforce", name: "Salesforce Agentforce", icon: "simple-icons:salesforce", brand: "#00A1E0", idLabel: "Agent API name", idPlaceholder: "Service_Agent", keyLabel: "Connected app credentials" };
const COPILOT_STUDIO = { id: "copilot_studio", name: "Microsoft Copilot Studio", icon: "simple-icons:microsoft", brand: "#5E5E5E", idLabel: "Agent schema name", idPlaceholder: "cr4f2_supportAgent", keyLabel: "Entra app credentials" };
/* eslint-enable no-restricted-syntax */

export const HOSTED_PLATFORMS_BY_TYPE = {
  voice: [
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    { id: "vapi", name: "Vapi", icon: "solar:phone-calling-rounded-linear", brand: "#12A594", idLabel: "Assistant ID", idPlaceholder: "asst_9f2c…", keyLabel: "Vapi API key" },
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    { id: "retell", name: "Retell AI", icon: "solar:microphone-3-linear", brand: "#635BFF", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "Retell API key" },
    /* Bland's new agent model: agents, not pathways. */
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    { id: "bland", name: "Bland.ai", icon: "solar:phone-linear", brand: "#F26D5B", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", idNote: "Agents on Bland's agent model. A pathway agent connects once it has moved to that model.", keyLabel: "Bland API key" },
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    { id: "elevenlabs", name: "ElevenLabs", icon: "solar:soundwave-linear", brand: "#111111", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "ElevenLabs API key" },
    /* Test only: LiveKit Agents is a code framework, so improvements come
       through the code. A running agent connected by its LiveKit project is
       tested and gets a suggested fix (rsiModel fixDepthOf). */
    {
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      id: "livekit", name: "LiveKit", icon: "solar:server-minimalistic-linear", brand: "#1FD5F9", idLabel: "Agent name", idPlaceholder: "returns-line-agent", keyLabel: "LiveKit API key",
      idNote: "Test only — connected by its LiveKit project, it gets a suggested fix. LiveKit Agents is a code framework, so proven changes come through its code: connect the agent's repository from New agent → GitHub.",
    },
    /* Full configuration through their APIs; a change ships as a draft version. */
    TELNYX,
    GOOGLE_CX,
    VOICEFLOW,
    /* No platform — a pasted system prompt we run against the LLM directly. */
    { id: "other", name: "Others", icon: "solar:menu-dots-linear", isOther: true },
  ],
  /* The same assistants over chat — Vapi and Retell chat agents, ElevenLabs
     chat and WhatsApp agents, Bland's messaging agents — and the platforms
     that are chat first. OpenAI Assistants is gone — retired by OpenAI; an
     Agents SDK agent comes in as code, as do LangGraph, CrewAI and Claude
     Agent SDK agents. */
  chat: [
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    { id: "vapi", name: "Vapi", icon: "solar:chat-round-linear", brand: "#12A594", idLabel: "Assistant ID", idPlaceholder: "asst_9f2c…", keyLabel: "Vapi API key" },
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    { id: "retell", name: "Retell AI", icon: "solar:chat-round-linear", brand: "#635BFF", idLabel: "Chat agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "Retell API key" },
    {
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      id: "elevenlabs", name: "ElevenLabs", icon: "solar:chat-round-linear", brand: "#111111", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "ElevenLabs API key",
      chatChannels: [{ id: "chat", label: "Chat" }, { id: "whatsapp", label: "WhatsApp", icon: "simple-icons:whatsapp" }],
    },
    {
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      id: "bland", name: "Bland.ai", icon: "solar:chat-round-linear", brand: "#F26D5B", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "Bland API key",
      chatChannels: [{ id: "messaging", label: "Messaging" }],
    },
    GOOGLE_CX,
    VOICEFLOW,
    AGENTFORCE,
    COPILOT_STUDIO,
  ],
  computer: [
    { id: "browserbase", name: "Browserbase", icon: "solar:global-linear", idLabel: "Session template ID", idPlaceholder: "sess_9f2c…", keyLabel: "Browserbase API key" },
    { id: "anthropic_cu", name: "Anthropic Computer Use", icon: "solar:monitor-linear", idLabel: "Agent name", idPlaceholder: "returns-agent", keyLabel: "Anthropic API key" },
    { id: "skyvern", name: "Skyvern", icon: "solar:mouse-linear", idLabel: "Task template ID", idPlaceholder: "tmpl_9f2c…", keyLabel: "Skyvern API key" },
  ],
};

/*
  Closed platforms (Sierra, Decagon, Intercom Fin, Zendesk) don't expose
  their agents' settings, so they're test only — through their phone line,
  their chat channel, or a conversation API where one exists. Each lists the
  ways it can be reached; `api` names what the conversation API is called.
*/
export const CLOSED_PLATFORM_REACH = {
  sierra: { icon: "solar:chat-round-dots-linear", ways: ["phone", "chat"], chatPlaceholder: "https://acme.sierra.chat/agent" },
  decagon: { icon: "solar:chat-round-dots-linear", ways: ["phone", "chat", "api"], api: "Conversation API", apiPlaceholder: "https://api.decagon.ai/conversation", chatPlaceholder: "https://acme.decagon.ai/chat" },
  intercom_fin: { icon: "simple-icons:intercom", ways: ["chat", "api"], api: "Conversations API", apiPlaceholder: "https://api.intercom.io/conversations", chatPlaceholder: "https://acme.intercom.help/messenger" },
  zendesk: { icon: "simple-icons:zendesk", ways: ["phone", "chat", "api"], api: "Sunshine Conversations API", apiPlaceholder: "https://api.smooch.io/v2/apps/…/conversations", chatPlaceholder: "https://acme.zendesk.com/chat" },
};
export const CLOSED_REACH_LABELS = { phone: "Phone line", chat: "Chat channel", api: "Conversation API" };

/* What a platform API key is used for — said where the key is entered. */
export const PLATFORM_KEY_SCOPE = "This key reads your agent, creates test copies or drafts and places test calls. We never edit, publish or delete your live agent.";
/* The key field's own line: how it's kept and what it's for (RSI PRD, "Credentials"). */
export const PLATFORM_KEY_HELPER = "Stored encrypted. Used only to read the agent, make test copies and place test calls — never shown again, never sent to the improver.";

/* Retired, so it's said in the framework path. */
export const OPENAI_AGENT_BUILDER_NOTE = "OpenAI agents come in as code, through the Agents SDK.";
export const TRACEAI_FRAMEWORK_NOTE = "traceAI traces every framework here except Microsoft Agent Framework.";

export const isLivePlatform = (p) => !!p && !p.comingSoon && !p.later;
export const firstLive = (platforms) => platforms.find(isLivePlatform);

/* Full country list from src/assets/data/countries. Ordering surfaces
   the `suggested` markets first (US, GB, IN, AU, DE, FR, …), then the
   rest alphabetised, so the picker opens on the countries the voice
   pilots actually land in without hiding the long tail. */
const flagOf = (iso) =>
  iso && iso.length === 2
    ? String.fromCodePoint(...iso.toUpperCase().split("").map((c) => 0x1F1E6 + c.charCodeAt(0) - 65))
    : "";

export const COUNTRY_OPTIONS = (() => {
  const filled = countries.filter((c) => c.code && c.phone);
  /* `phone` in the seed uses "1-268" for territories that share a
     dial prefix — we keep just the leading trunk so the picker never
     shows a hyphenated code the user cannot type. */
  const cleaned = filled.map((c) => ({
    iso: c.code,
    name: c.label,
    dial: `+${String(c.phone).split("-")[0]}`,
    suggested: !!c.suggested,
    flag: flagOf(c.code),
  }));
  const suggested = cleaned.filter((c) => c.suggested);
  const rest = cleaned.filter((c) => !c.suggested).sort((a, b) => a.name.localeCompare(b.name));
  return [...suggested, ...rest];
})();

export const COUNTRY_BY_ISO = Object.fromEntries(COUNTRY_OPTIONS.map((c) => [c.iso, c]));

