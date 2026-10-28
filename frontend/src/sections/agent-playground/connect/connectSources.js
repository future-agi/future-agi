export const GITHUB_SOURCE = "github";

const soon = (id, name, extra = {}) => ({ id, name, ...extra });

const FRAMEWORKS = [
  soon("fw-langgraph", "LangGraph", { icon: "simple-icons:langchain", short: "Graph" }),
  soon("fw-openai-agents", "OpenAI Agents SDK", { icon: "simple-icons:openai", short: "Agents and handoffs" }),
  soon("fw-crewai", "CrewAI", { short: "Crew" }),
  soon("fw-claude-agent-sdk", "Claude Agent SDK", { icon: "simple-icons:anthropic", short: "Agent and subagents" }),
  soon("fw-livekit", "LiveKit Agents", { short: "Agent session" }),
  soon("fw-pipecat", "Pipecat", { short: "Pipeline" }),
  soon("fw-google-adk", "Google ADK", { icon: "simple-icons:google", short: "Agents and sub-agents" }),
  soon("fw-mastra", "Mastra", { short: "Agents and workflows" }),
  soon("fw-vercel-ai", "Vercel AI SDK", { icon: "simple-icons:vercel", short: "Agent and tools" }),
  soon("fw-strands", "AWS Strands", { short: "Agents as tools" }),
  soon("fw-pydantic-ai", "Pydantic AI", { icon: "simple-icons:pydantic", short: "Agents and delegation" }),
  soon("fw-llamaindex", "LlamaIndex", { short: "Workflow" }),
  soon("fw-smolagents", "smolagents", { icon: "simple-icons:huggingface", short: "Agents and managed agents" }),
  soon("fw-dspy", "DSPy", { short: "Program" }),
];

export const CONNECT_GROUPS = [
  {
    id: "code",
    title: "Code",
    sections: [
      {
        id: "repo",
        title: "From a repository",
        reach: "Connect where the code lives. We read it and work out the framework.",
        items: [
          {
            id: GITHUB_SOURCE,
            name: "GitHub",
            icon: "simple-icons:github",
            next: "Install the GitHub app, then pick the repository",
            keywords: "git repo repository code",
            available: true,
          },
          soon("gitlab", "GitLab", { icon: "simple-icons:gitlab", keywords: "git repo repository merge request" }),
          soon("bitbucket", "Bitbucket", { icon: "simple-icons:bitbucket", keywords: "git repo repository atlassian" }),
          soon("direct-api", "OpenAI, Anthropic or Gemini calls", {
            icon: "solar:code-square-linear",
            keywords: "openai anthropic claude gemini gpt llm api no framework",
          }),
        ],
      },
      {
        id: "sdk",
        title: "Through our SDK",
        reach: "Add a few lines where the agent runs. No repository access needed.",
        columns: 3,
        items: FRAMEWORKS,
      },
      {
        id: "upload",
        title: "Without a repository",
        reach: "We read it once, and fixes come back as a patch to apply.",
        dropzone: true,
        items: [soon("upload", "Upload a folder", { icon: "solar:upload-minimalistic-linear", keywords: "zip folder files local code" })],
      },
    ],
  },
  {
    id: "platforms",
    title: "Hosted platforms",
    reach: "Your API key and agent, voice or chat, tested in your account on a copy.",
    items: [
      soon("vapi", "Vapi", { keywords: "voice phone calls assistant chat" }),
      soon("retell", "Retell AI", { keywords: "voice phone calls" }),
      soon("elevenlabs", "ElevenLabs", { keywords: "eleven labs 11labs voice" }),
      soon("bland", "Bland.ai", { keywords: "bland ai voice phone calls" }),
      soon("telnyx", "Telnyx", { keywords: "voice phone sip" }),
      soon("google_cx", "Google CX Agent Studio", { icon: "simple-icons:dialogflow", keywords: "dialogflow google cloud contact center" }),
      soon("voiceflow", "Voiceflow", { keywords: "no code" }),
      soon("agentforce", "Salesforce Agentforce", { icon: "simple-icons:salesforce", keywords: "salesforce crm" }),
      soon("copilot_studio", "Microsoft Copilot Studio", { keywords: "microsoft power virtual agents teams" }),
    ],
  },
  {
    id: "other",
    title: "Other ways",
    reach: "Anything we can call, upload or phone, and agents connected for testing only.",
    items: [
      soon("livekit", "LiveKit project", { keywords: "voice webrtc room" }),
      soon("running", "Agent endpoint", { icon: "solar:global-linear", keywords: "http https api url websocket webhook endpoint rest" }),
      soon("phone", "Phone number", { icon: "solar:phone-calling-rounded-linear", keywords: "call calls number twilio sip" }),
      soon("closed-sierra", "Sierra", { keywords: "support helpdesk test only" }),
      soon("closed-decagon", "Decagon", { keywords: "support helpdesk test only" }),
      soon("closed-intercom_fin", "Intercom Fin", { icon: "simple-icons:intercom", keywords: "support helpdesk test only" }),
      soon("closed-zendesk", "Zendesk", { icon: "simple-icons:zendesk", keywords: "support helpdesk tickets test only" }),
    ],
  },
];

export const groupItems = (group) =>
  group.sections ? group.sections.flatMap((section) => section.items) : group.items;

const normalize = (text) => String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

export const searchGroups = (query) => {
  const needle = normalize(query);
  return CONNECT_GROUPS.map((group) => ({
    ...group,
    items: groupItems(group).filter(
      (item) =>
        normalize(item.name).includes(needle) ||
        String(item.keywords || "")
          .split(" ")
          .some((word) => normalize(word).startsWith(needle)),
    ),
  })).filter((group) => group.items.length);
};
