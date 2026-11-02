/*
  Every way in the Build environment drawer lists, from Vel's design. Every item
  opens its setup. One with `setup.uiOnly` is the design without its API yet:
  it runs on sample data and builds nothing.

  `setup.preset` names the hosted platform id each agent type builds with. A
  `null` side opens that type UI only.

  `channels` says which agents a way can bring ("voice", "chat"), so connecting
  an agent for a voice or chat world lists only the ways that fit. Code ways
  are untagged: the code says what it is.
*/

export const CATALOG_TABS = [
  { id: "code", label: "Code" },
  { id: "hosted", label: "Hosted platforms" },
  { id: "other", label: "Other ways" },
];

export const CODE_SECTIONS = [
  {
    id: "repo",
    title: "From a repository",
    line: "Connect where the code lives. We read it and work out the framework.",
  },
  {
    id: "sdk",
    title: "Through our SDK",
    line: "Add a few lines where the agent runs. No repository access needed.",
  },
  {
    id: "upload",
    title: "Without a repository",
    line: "We read it once, and fixes come back as a patch to apply.",
  },
];

// SDK rows say only the framework's name, as in Vel's design.
const FRAMEWORKS = [
  [
    "langgraph",
    "LangGraph",
    { icon: "simple-icons:langgraph" },
    "langchain python",
  ],
  [
    "openai-agents",
    "OpenAI Agents SDK",
    { icon: "simple-icons:openai" },
    "openai python handoffs",
  ],
  ["crewai", "CrewAI", { icon: "simple-icons:crewai" }, "crew ai python"],
  [
    "claude-agent-sdk",
    "Claude Agent SDK",
    { icon: "simple-icons:claude" },
    "anthropic claude python typescript",
  ],
  [
    "livekit-agents",
    "LiveKit Agents",
    { logo: "livekit" },
    "voice webrtc python",
  ],
  [
    "pipecat",
    "Pipecat",
    { icon: "simple-icons:pipecat" },
    "voice python daily",
  ],
  [
    "google-adk",
    "Google ADK",
    { icon: "simple-icons:google" },
    "google gemini vertex python",
  ],
  ["mastra", "Mastra", { icon: "thesvg:mastra" }, "typescript javascript node"],
  [
    "vercel-ai",
    "Vercel AI SDK",
    { icon: "simple-icons:vercel" },
    "vercel typescript javascript next",
  ],
  [
    "strands",
    "AWS Strands",
    { icon: "simple-icons:amazonwebservices" },
    "aws amazon bedrock python",
  ],
  [
    "pydantic-ai",
    "Pydantic AI",
    { icon: "simple-icons:pydantic" },
    "pydantic python",
  ],
  ["agno", "Agno", { icon: "fagi:agno" }, "python team"],
  [
    "haystack",
    "Haystack",
    { icon: "simple-icons:haystack" },
    "deepset python pipeline",
  ],
  [
    "llamaindex",
    "LlamaIndex",
    { icon: "thesvg:llamaindex" },
    "llama index python workflow",
  ],
  [
    "smolagents",
    "smolagents",
    { icon: "simple-icons:huggingface" },
    "hugging face python",
  ],
  ["dspy", "DSPy", { icon: "fagi:dspy" }, "stanford python program"],
  [
    "microsoft-agent-framework",
    "Microsoft Agent Framework",
    { icon: "simple-icons:microsoft" },
    "microsoft autogen semantic kernel dotnet python",
  ],
].map(([id, name, look, keywords]) => ({
  id: `fw-${id}`,
  tab: "code",
  section: "sdk",
  name,
  ...look,
  keywords,
  framework: id,
  setup: { panel: "framework", uiOnly: true },
}));

const hosted = (id, name, channels, look, next, keywords, setup) => ({
  id,
  tab: "hosted",
  name,
  channels,
  next,
  ...look,
  keywords,
  setup: setup || {
    panel: "hostedSoon",
    platform: id,
    uiOnly: true,
  },
});

const closed = (id, name, channels, icon, reach, keywords) => ({
  id: `closed-${id}`,
  tab: "other",
  name,
  channels,
  next: `Test only, reached by ${reach}`,
  icon,
  keywords: `${keywords} closed test only`,
  setup: { panel: "closed", platform: id, uiOnly: true },
});

export const CATALOG_ITEMS = [
  {
    id: "github",
    tab: "code",
    section: "repo",
    name: "GitHub",
    next: "Install the GitHub app, then pick the repository",
    logo: "github",
    keywords: "git repo repository code",
    setup: { panel: "repo" },
  },
  {
    id: "gitlab",
    tab: "code",
    section: "repo",
    name: "GitLab",
    next: "Add a group token, then pick the repository",
    logo: "gitlab",
    keywords: "git repo repository merge request",
    setup: { panel: "gitHost", host: "gitlab", uiOnly: true },
  },
  {
    id: "bitbucket",
    tab: "code",
    section: "repo",
    name: "Bitbucket",
    next: "Add a workspace token, then pick the repository",
    logo: "bitbucket",
    keywords: "git repo repository atlassian",
    setup: { panel: "gitHost", host: "bitbucket", uiOnly: true },
  },
  {
    id: "direct-api",
    tab: "code",
    section: "repo",
    name: "OpenAI, Anthropic or Gemini calls",
    next: "Connect the repository, then name where the agent starts",
    icon: "solar:code-square-linear",
    keywords:
      "openai anthropic claude gemini gpt llm api no framework python typescript",
    setup: { panel: "directApi", uiOnly: true },
  },
  ...FRAMEWORKS,
  {
    id: "upload",
    tab: "code",
    section: "upload",
    name: "Drop a folder or a .zip here",
    next: "Up to 200 MB · built in our sandbox for every run",
    icon: "solar:upload-minimalistic-linear",
    keywords: "upload zip folder files local code",
    setup: { panel: "upload", title: "Upload a folder" },
  },
  hosted(
    "vapi",
    "Vapi",
    ["voice", "chat"],
    { logo: "vapi" },
    "Add your Vapi API key, then pick the assistant · Voice and chat",
    "voice phone assistant",
    { panel: "hosted", preset: { voice: "vapi", chat: null } },
  ),
  hosted(
    "retell",
    "Retell AI",
    ["voice", "chat"],
    { logo: "retell" },
    "Add your Retell AI API key, then pick the agent · Voice and chat",
    "voice phone chat agent",
    { panel: "hosted", preset: { voice: "retell", chat: "retell_chat" } },
  ),
  hosted(
    "elevenlabs",
    "ElevenLabs",
    ["voice", "chat"],
    { logo: "elevenlabs" },
    "Add your ElevenLabs API key, then pick the agent · Voice and chat",
    "voice chat eleven labs",
  ),
  hosted(
    "bland",
    "Bland.ai",
    ["voice", "chat"],
    { logo: "bland" },
    "Add your Bland.ai API key, then pick the agent · Voice and chat",
    "voice phone pathway",
  ),
  hosted(
    "telnyx",
    "Telnyx",
    ["voice"],
    { icon: "fagi:telnyx" },
    "Add your Telnyx API key, then pick the assistant · Voice",
    "voice phone",
  ),
  hosted(
    "google_cx",
    "Google CX Agent Studio",
    ["voice", "chat"],
    { icon: "simple-icons:dialogflow" },
    "Add a service account, then pick the agent · Voice and chat",
    "google dialogflow voice chat",
  ),
  hosted(
    "voiceflow",
    "Voiceflow",
    ["voice", "chat"],
    { icon: "fagi:voiceflow" },
    "Add your Voiceflow API key, then pick the project · Voice and chat",
    "voice chat",
  ),
  hosted(
    "agentforce",
    "Salesforce Agentforce",
    ["chat"],
    { icon: "simple-icons:salesforce" },
    "Add a connected app, then pick the agent · Chat",
    "salesforce chat",
  ),
  hosted(
    "copilot_studio",
    "Microsoft Copilot Studio",
    ["chat"],
    { icon: "simple-icons:microsoft" },
    "Add an Entra app, then pick the agent · Chat",
    "microsoft copilot chat",
  ),
  {
    id: "livekit",
    tab: "other",
    name: "LiveKit project",
    next: "Add the server URL and the agent's name · Voice",
    logo: "livekit",
    keywords: "voice webrtc server",
    channels: ["voice"],
    setup: { panel: "livekit", title: "LiveKit" },
  },
  {
    id: "running",
    tab: "other",
    name: "Agent endpoint",
    next: "Paste the URL we should call",
    icon: "solar:global-linear",
    keywords: "http websocket url endpoint api",
    channels: ["chat", "voice"],
    setup: { panel: "endpoint", uiOnly: true },
  },
  {
    id: "phone",
    tab: "other",
    name: "Phone number",
    next: "Enter its number. We call it, or it calls our test number",
    icon: "solar:phone-calling-rounded-linear",
    keywords: "call telephone pstn voice",
    channels: ["voice"],
    setup: { panel: "phone" },
  },
  closed(
    "sierra",
    "Sierra",
    ["voice", "chat"],
    "fagi:sierra",
    "phone, chat",
    "sierra",
  ),
  closed(
    "decagon",
    "Decagon",
    ["voice", "chat"],
    "fagi:decagon",
    "phone, chat, API",
    "decagon",
  ),
  closed(
    "intercom_fin",
    "Intercom Fin",
    ["chat"],
    "simple-icons:intercom",
    "chat, API",
    "intercom fin",
  ),
  closed(
    "zendesk",
    "Zendesk",
    ["voice", "chat"],
    "simple-icons:zendesk",
    "phone, chat, API",
    "zendesk sunshine",
  ),
];

/** The items whose name or keywords contain `query`; all of them for a blank one. */
export function searchCatalog(items, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return items;
  return items.filter((it) =>
    `${it.name} ${it.keywords || ""}`.toLowerCase().includes(q),
  );
}

/**
 * The ways that can bring an agent of `modality` ("voice", or "text" for
 * chat): a way tagged with other channels drops out. Every way for none.
 */
export function connectItemsFor(modality, items = CATALOG_ITEMS) {
  if (!modality) return items;
  const channel = modality === "voice" ? "voice" : "chat";
  return items.filter((it) => !it.channels || it.channels.includes(channel));
}
