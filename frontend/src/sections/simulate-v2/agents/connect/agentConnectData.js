/*
  New agent's ways in — the same directory as the Environments landing,
  mapped onto the agent flows. Each item names the flow (`kind`) and what it
  starts with (`initial`): a platform, a framework, a connected account's
  agent. Traces aren't a way in: an Observe project is linked after
  connecting, from the agent's Next steps.
*/
import { FRAMEWORK_META } from "../../_mock/agentRegistry";
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

/* A hosted platform, listed once: voice, chat or both. Its setup asks which
   when it does both — one key per platform either way. */
const platformItem = (id, name, channels, extra = {}) => ({
  id, name, kind: "hosted", fix: "In your account", channels,
  initial: { platform: id, channels, ...(channels.length === 1 && { agentType: channels[0] }) },
  ...extra,
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
    id: "platforms",
    title: "Hosted platforms",
    reach: "Your API key and agent — voice or chat, tested in your account on a copy, draft or branch.",
    items: [
      platformItem("vapi", "Vapi", ["voice", "chat"], { logo: "vapi", need: "API key · assistant" }),
      platformItem("retell", "Retell AI", ["voice", "chat"], { logo: "retell", need: "API key · agent" }),
      platformItem("elevenlabs", "ElevenLabs", ["voice", "chat"], { logo: "elevenlabs", need: "Agents API key · agent" }),
      platformItem("bland", "Bland.ai", ["voice", "chat"], { logo: "bland", need: "API key · agent" }),
      platformItem("telnyx", "Telnyx", ["voice"], { icon: "solar:phone-calling-linear", need: "API key · assistant" }),
      platformItem("google_cx", "Google CX Agent Studio", ["voice", "chat"], { icon: "simple-icons:dialogflow", need: "Service account · agent" }),
      platformItem("voiceflow", "Voiceflow", ["voice", "chat"], { icon: "solar:routing-2-linear", need: "API key · project" }),
      platformItem("agentforce", "Salesforce Agentforce", ["chat"], { icon: "simple-icons:salesforce", need: "Connected app · agent" }),
      platformItem("copilot_studio", "Microsoft Copilot Studio", ["chat"], { icon: "simple-icons:microsoft", need: "Entra app · agent" }),
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
const CHANNEL_GROUP_TITLES = {};
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
        /* Two kinds of code way in, said once each: where the code lives, or
           our SDK beside a framework — whose rows then say only what's
           distinctive (Graph, Crew), not the same step seventeen times. */
        const ordered = upload ? [first, upload, ...rest] : g.items;
        return {
          ...g, title: "Code", reach: "Fixes come back as pull requests.",
          sections: CODE_SECTIONS,
          items: [...ordered.filter((it) => it.kind !== "framework"), ...ordered.filter((it) => it.kind === "framework")]
            .map(withNext)
            .map((it) => (it.kind === "framework"
              ? { ...it, section: "sdk", short: String(it.need || "").replace(/^SDK adapter · /, "").replace(/^./, (c) => c.toUpperCase()) }
              : { ...it, section: it.kind === "upload" ? "upload" : "repo" })),
        };
      }
      return {
        ...g,
        items: g.items.filter((it) => it.kind !== "builtin" && it.kind !== "upload").map(withNext),
      };
    })
    .filter((g) => g.items.length);
  return { builtin, groups: [...groups.filter((g) => g.id === "code"), ...groups.filter((g) => g.id !== "code")] };
};


/*
  The step a row starts — said on the row, with Connect beside it, instead
  of how a fix comes back (each group's line says that once). Worded from the
  kind, the host and what the platform asks for.
*/
const NEXT_BY_ID = {
  github: "Install the GitHub app, then pick the repository",
  gitlab: "Add a group token, then pick the repository",
  bitbucket: "Add a workspace token, then pick the repository",
  upload: "Drop a folder or a .zip",
  "direct-api": "Connect the repository, then name where the agent starts",
  running: "Paste the URL we should call",
  phone: "Enter its number — we call it, or it calls our test number",
  livekit: "Add the server URL and the agent's name",
};
const nextStepOf = (it) => {
  if (NEXT_BY_ID[it.id]) return NEXT_BY_ID[it.id];
  if (it.kind === "framework") return `Add our SDK where your ${it.name} agent runs`;
  if (it.kind === "closed") return `Test only — reached by ${String(it.need || "").replace(/^Test only · /, "")}`;
  if (it.kind === "hosted") {
    const [first = "", second] = String(it.need || "").split(" · ");
    const platform = it.name.replace(/ (chat|messaging)$/i, "");
    const key = /service account/i.test(first) ? "a service account"
      : /connected app/i.test(first) ? "a connected app"
        : /entra app/i.test(first) ? "an Entra app"
          : `your ${platform} API key`;
    const thing = (second || "agent").replace(/^(chat|messaging) /, "");
    return `Add ${key}, then pick the ${thing}`;
  }
  return it.need || "";
};
/*
  What people type for a way in besides its name: other names for it
  (LangChain, Dialogflow), its vendor, the language, what it is. Search
  reads these too.
*/
const KEYWORDS = {
  github: "git repo repository code",
  gitlab: "git repo repository merge request",
  bitbucket: "git repo repository atlassian",
  upload: "zip folder files local code",
  "direct-api": "openai anthropic claude gemini gpt llm api no framework python typescript",
  "fw-langgraph": "langchain python",
  "fw-openai-agents": "openai python handoffs",
  "fw-crewai": "crew ai python",
  "fw-claude-agent-sdk": "anthropic claude python typescript",
  "fw-livekit": "voice webrtc python",
  "fw-pipecat": "voice python daily",
  "fw-google-adk": "google gemini vertex python",
  "fw-mastra": "typescript javascript node",
  "fw-vercel-ai": "vercel ai typescript javascript next",
  "fw-strands": "aws amazon bedrock python",
  "fw-pydantic-ai": "pydantic python",
  "fw-agno": "phidata python",
  "fw-haystack": "deepset python rag",
  "fw-llamaindex": "llama index python rag",
  "fw-smolagents": "hugging face huggingface python",
  "fw-dspy": "stanford python",
  "fw-microsoft-agent-framework": "microsoft autogen semantic kernel dotnet python",
  vapi: "voice phone calls assistant chat",
  retell: "voice phone calls",
  elevenlabs: "eleven labs 11labs voice",
  bland: "bland ai voice phone calls",
  telnyx: "voice phone sip",
  google_cx: "dialogflow google cloud ccai contact center",
  voiceflow: "no code",
  agentforce: "salesforce crm",
  copilot_studio: "microsoft power virtual agents teams",
  running: "http https api url websocket webhook endpoint rest",
  phone: "call calls number twilio sip pstn",
  livekit: "voice webrtc room",
};
/* Which channels a hosted platform does, said on its row. */
const CHANNEL_WORDS = { "voice,chat": "Voice and chat", voice: "Voice", chat: "Chat" };
const withNext = (it) => ({
  ...it,
  next: it.kind === "hosted" && it.channels?.length ? `${nextStepOf(it)} · ${CHANNEL_WORDS[it.channels.join(",")] || ""}` : nextStepOf(it),
  keywords: [KEYWORDS[it.id] || KEYWORDS[it.initial?.platform] || "", it.kind === "closed" ? "support helpdesk tickets test only" : "", ...(it.channels || [])].filter(Boolean).join(" "),
});

/* The Code tab's two parts, each with its step said once. */
const CODE_SECTIONS = {
  repo: { title: "From a repository", reach: "Connect where the code lives — we read it and work out the framework." },
  sdk: { title: "Through our SDK", reach: "Add a few lines where the agent runs — no repository access needed." },
  /* Code that isn't in a repository: read once, fixed by a patch — the fallback, so last. */
  upload: { title: "Without a repository", reach: "We read it once, and fixes come back as a patch to apply.", dropzone: true },
};
