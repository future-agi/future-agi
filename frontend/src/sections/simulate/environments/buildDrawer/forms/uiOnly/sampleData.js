/*
  What the UI-only setup forms show before their APIs exist, ported from
  Vel's prototype: each framework's adapter snippets, what improvements can
  change per framework, the git hosts and their sample repositories, the
  sample agents a hosted key lists, the closed platforms' ways in. Every list
  here is sample data; the forms say so where they show it.
*/

const hash = (s) => {
  let h = 0;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1)
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
};

/** A stable 7-character id for a seed, as a commit sha reads. */
export const shortSha = (s) =>
  hash(s).toString(16).padStart(7, "0").slice(0, 7);

/* Frameworks, keyed by the catalog's framework id. */
export const FRAMEWORK_META = {
  langgraph: {
    label: "LangGraph",
    structure: "Graph",
    tuning: "LangGraph / LangChain",
  },
  "openai-agents": {
    label: "OpenAI Agents SDK",
    structure: "Agents & handoffs",
    tuning: "OpenAI Agents SDK",
  },
  crewai: {
    label: "CrewAI",
    structure: "Crew",
    tuning: "CrewAI and Claude Agent SDK",
  },
  "claude-agent-sdk": {
    label: "Claude Agent SDK",
    structure: "Agent & subagents",
    tuning: "CrewAI and Claude Agent SDK",
  },
  "livekit-agents": {
    label: "LiveKit Agents",
    structure: "Agent session",
    tuning: "LiveKit Agents",
    voice: true,
  },
  pipecat: {
    label: "Pipecat",
    structure: "Pipeline",
    tuning: "Pipecat",
    voice: true,
  },
  "google-adk": {
    label: "Google ADK",
    structure: "Agents & sub-agents",
    tuning: "Google ADK",
  },
  mastra: {
    label: "Mastra",
    structure: "Agents & workflows",
    tuning: "Mastra and Vercel AI SDK",
    lang: "typescript",
  },
  "vercel-ai": {
    label: "Vercel AI SDK",
    structure: "Agent & tools",
    tuning: "Mastra and Vercel AI SDK",
    lang: "typescript",
  },
  strands: {
    label: "AWS Strands",
    structure: "Agents as tools",
    tuning: "AWS Strands and Pydantic AI",
  },
  "pydantic-ai": {
    label: "Pydantic AI",
    structure: "Agents & delegation",
    tuning: "AWS Strands and Pydantic AI",
  },
  agno: { label: "Agno", structure: "Team", tuning: "Agno" },
  haystack: { label: "Haystack", structure: "Pipeline", tuning: "Haystack" },
  llamaindex: {
    label: "LlamaIndex",
    structure: "Workflow",
    tuning: "LlamaIndex",
  },
  smolagents: {
    label: "smolagents",
    structure: "Agents & managed agents",
    tuning: "smolagents",
  },
  dspy: { label: "DSPy", structure: "Program", tuning: "DSPy" },
  "microsoft-agent-framework": {
    label: "Microsoft Agent Framework",
    structure: "Agents & workflow",
    tuning: "Microsoft Agent Framework",
    traced: false,
  },
};

/* What the improver edits, by the tuning table's framework name. */
const TUNING_EDITS = {
  "LangGraph / LangChain": "Prompts, tools and each step's model",
  "OpenAI Agents SDK": "Each agent's instructions, model, tools and handoffs",
  "Direct OpenAI, Anthropic or Gemini calls":
    "Prompts and tools written in the code; you name where the agent starts",
  "LiveKit Agents":
    "Each agent's instructions and tools, and the speech, model and turn-taking settings it uses",
  Pipecat: "The pipeline's settings, and its flow file when it has one",
  "Google ADK": "Agents in code or a configuration file",
  "Mastra and Vercel AI SDK":
    "Agents defined in code or files; Mastra also keeps its own draft versions",
  "AWS Strands and Pydantic AI":
    "Agents in code; Strands can also load one from a configuration file",
  "CrewAI and Claude Agent SDK":
    "CrewAI agents in configuration files; Claude agents in code plus instruction files",
  Agno: "Agents in code",
  Haystack: "Agents in code",
  LlamaIndex: "Agents in code",
  smolagents: "Agents in code",
  DSPy: "Agents in code; DSPy's instructions are rewritten by its own optimizers",
  "Microsoft Agent Framework": "Agents in code",
};
const TUNING_BY_NAME = {
  "Direct API calls": "Direct OpenAI, Anthropic or Gemini calls",
};

/**
 * What improvements edit for a framework: a catalog id ("google-adk") or the
 * name a scan found ("Google ADK"). `{ label, edits, traced }`, or null.
 */
export const frameworkTuningOf = (framework) => {
  const id = FRAMEWORK_META[framework]
    ? framework
    : Object.keys(FRAMEWORK_META).find(
        (k) =>
          FRAMEWORK_META[k].label.toLowerCase() ===
          String(framework || "").toLowerCase(),
      );
  const meta = id ? FRAMEWORK_META[id] : null;
  const key = meta?.tuning || TUNING_BY_NAME[framework];
  const edits = key ? TUNING_EDITS[key] : null;
  if (!edits) return null;
  return {
    label: meta?.label || framework,
    edits,
    traced: meta?.traced !== false,
  };
};

/*
  Our SDK adapter per framework: the install line, what serve() wraps, the
  wrap itself and how it's run. Python frameworks take a pip extra; the
  TypeScript ones an npm package.
*/
const py = (extra, wraps, lines) => ({
  install: `pip install "futureagi[${extra}]"`,
  wraps,
  code: [
    ...lines,
    "",
    `app = fagi.serve(${lines.serveArg})  # serves /fagi on :8000`,
  ].join("\n"),
  run: "uvicorn serve:app --port 8000",
});
const pyLines = (module, from, what, serveArg) =>
  Object.assign(
    [
      `from futureagi.adapters import ${module} as fagi`,
      `from ${from} import ${what.split(" ")[0]}${what.includes(" ") ? `  # ${what.split(" ").slice(1).join(" ")}` : ""}`,
    ],
    { serveArg },
  );
const ts = (subpath, wraps, importLine, serveArg) => ({
  install: "npm install @futureagi/adapters",
  wraps,
  code: [
    `import { serve } from "@futureagi/adapters/${subpath}";`,
    importLine,
    "",
    `serve(${serveArg}, { port: 8000 }); // serves /fagi`,
  ].join("\n"),
  run: "npx tsx serve.ts",
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
  ),
  "livekit-agents": {
    ...py(
      "livekit",
      "your agent's entrypoint",
      pyLines(
        "livekit",
        "agent",
        "entrypoint your AgentSession entrypoint",
        "entrypoint",
      ),
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
  ),
  mastra: ts(
    "mastra",
    "your Mastra agent",
    'import { mastra } from "./src/mastra"; // your Mastra instance',
    'mastra.getAgent("supportAgent")',
  ),
  "vercel-ai": ts(
    "ai-sdk",
    "your agent",
    'import { supportAgent } from "./lib/agent"; // your Agent: model, system and tools',
    "supportAgent",
  ),
  strands: py(
    "strands",
    "your Strands agent",
    pyLines("strands", "support.agent", "agent your strands Agent", "agent"),
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
  ),
  smolagents: py(
    "smolagents",
    "your manager agent",
    pyLines("smolagents", "support.agent", "manager your CodeAgent", "manager"),
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
  ),
};

export const ADAPTER_ENDPOINT_PLACEHOLDER = "http://localhost:8000/fagi";

/* The adapter's wrap with serve() swapped for init(): the agent runs on this
   machine and checks in, instead of being served at a URL. */
export const localInitOf = (adapter) =>
  adapter.code
    .replace(
      /^app = fagi\.serve\((.*)\)\s+#.*$/m,
      "fagi.init($1)  # checks in from this machine with its commit",
    )
    .replace(/import \{ serve \}/, "import { init }")
    .replace(
      /^serve\((.*), \{ port: 8000 \}\);.*$/m,
      "init($1); // checks in from this machine with its commit",
    );

export const OPENAI_AGENT_BUILDER_NOTE =
  "OpenAI agents come in as code, through the Agents SDK.";
export const TRACEAI_FRAMEWORK_NOTE =
  "traceAI traces every framework here except Microsoft Agent Framework.";

/* Git hosts: one connection per group (GitLab) or workspace (Bitbucket). */
export const GIT_HOST_META = {
  github: {
    label: "GitHub",
    icon: "mdi:github",
    base: "https://github.com",
    accountPlaceholder: "acme-inc",
  },
  gitlab: {
    label: "GitLab",
    icon: "mdi:gitlab",
    base: "https://gitlab.com",
    account: "Group",
    accountPlaceholder: "acme-inc",
    accounts: ["acme-inc", "acme-labs"],
    token: "Group access token",
    review: "merge request",
    readScopes: [
      {
        scope: "read_repository",
        access: "Read",
        why: "Read the agent's code: prompts, tools, config and the entry point.",
      },
      {
        scope: "read_api",
        access: "Read",
        why: "List the group's projects and branches.",
      },
    ],
  },
  bitbucket: {
    label: "Bitbucket",
    icon: "mdi:bitbucket",
    base: "https://bitbucket.org",
    account: "Workspace",
    accountPlaceholder: "acme",
    accounts: ["acme", "acme-labs"],
    token: "Workspace access token",
    review: "pull request",
    readScopes: [
      {
        scope: "repository",
        access: "Read",
        why: "Read the agent's code: prompts, tools, config and the entry point.",
      },
      {
        scope: "account",
        access: "Read",
        why: "List the workspace's repositories.",
      },
    ],
  },
};
export const GIT_HOSTS = ["github", "gitlab", "bitbucket"];

/*
  Every repository with an agent holds at least sixteen, to try choosing
  one agent from a long list. The extra ones are siblings of the
  repository's first agent: same framework, same file layout.
*/
const MIN_AGENTS = 16;
const SIBLINGS = [
  ["order-status", "Order status helper"],
  ["shipping", "Shipping updates"],
  ["loyalty", "Loyalty points desk"],
  ["escalations", "Escalations router"],
  ["survey", "After-call survey"],
  ["recovery", "Account recovery"],
  ["hours", "Store hours"],
  ["reminders", "Appointment reminders"],
  ["feedback", "Feedback summariser"],
  ["warranty", "Warranty claims"],
  ["subscriptions", "Subscription changes"],
  ["gift-cards", "Gift card balance"],
  ["address", "Address changes"],
  ["invoices", "Invoice copies"],
  ["cancellations", "Order cancellations"],
  ["promotions", "Promo code help"],
];
const COMMON_FILES =
  /^(agent|bot|main|app|graph|crew|team|pipeline|workflow)\./;
const siblingEntry = (entry, id) => {
  const [path, fn] = String(entry).split(":");
  const parts = path.split("/");
  const file = parts[parts.length - 1];
  const slug = id.replace(/-/g, "_");
  const moved =
    parts.length >= 3 && COMMON_FILES.test(file)
      ? [...parts.slice(0, -2), slug, file].join("/")
      : [
          ...parts.slice(0, -1),
          `${slug}${file.slice(file.lastIndexOf("."))}`,
        ].join("/");
  return fn ? `${moved}:${fn}` : moved;
};
const withSiblings = (repo) => {
  if (!repo.agents.length || repo.agents.length >= MIN_AGENTS) return repo;
  const base = repo.agents[0];
  const taken = new Set(repo.agents.map((a) => a.id));
  const extra = SIBLINGS.filter(([id]) => !taken.has(id))
    .slice(0, MIN_AGENTS - repo.agents.length)
    .map(([id, name], i) => {
      const entry = siblingEntry(base.entry, id);
      const dir = entry.split(":")[0].split("/").slice(0, -1).join("/");
      return {
        id,
        name,
        framework: base.framework,
        ...(base.sdk && { sdk: base.sdk }),
        entry,
        prompts: [`${dir ? `${dir}/` : ""}prompts/${id.replace(/-/g, "_")}.md`],
        tools: 2 + (i % 4),
      };
    });
  return { ...repo, agents: [...repo.agents, ...extra] };
};

const RAW_REPOS = {
  github: [
    {
      name: "acme-inc/faq-assistant",
      language: "Python",
      agents: [
        {
          id: "faq",
          name: "Help-centre FAQ assistant",
          framework: "Direct API calls",
          sdk: "OpenAI SDK",
          entry: "app/chat.py:handle_message",
          prompts: ["app/prompts/faq_system.txt"],
          tools: 2,
        },
        {
          id: "summary",
          name: "Ticket summariser",
          framework: "Direct API calls",
          sdk: "Anthropic SDK",
          entry: "app/summarise.py:summarise_ticket",
          prompts: ["app/prompts/summary.txt"],
          tools: 1,
        },
      ],
    },
    {
      name: "acme-labs/research-agents",
      language: "Python",
      agents: [
        {
          id: "researcher",
          name: "Research assistant",
          framework: "OpenAI Agents SDK",
          entry: "agents/research.py",
          prompts: ["prompts/research.md"],
          tools: 4,
        },
      ],
    },
    { name: "acme-inc/web-frontend", language: "TypeScript", agents: [] },
    { name: "acme-labs/notebooks", language: "Jupyter Notebook", agents: [] },
  ],
  gitlab: [
    {
      name: "acme-inc/claims-voice",
      language: "Python",
      agents: [
        {
          id: "fnol",
          name: "First notice of loss line",
          framework: "LiveKit Agents",
          entry: "agents/fnol/agent.py",
          prompts: ["agents/fnol/prompts/system.md"],
          tools: 5,
        },
      ],
    },
    {
      name: "acme-inc/platform/support-graph",
      language: "Python",
      agents: [
        {
          id: "support-graph",
          name: "Support router",
          framework: "LangGraph",
          entry: "app/graph.py:graph",
          prompts: ["prompts/router.md", "prompts/refunds.md"],
          tools: 6,
        },
        {
          id: "support-dspy",
          name: "Ticket classifier (DSPy)",
          framework: "DSPy",
          entry: "classifier/program.py:Classifier",
          prompts: ["classifier/compiled.json"],
          tools: 1,
        },
      ],
    },
    { name: "acme-inc/infra-terraform", language: "HCL", agents: [] },
    {
      name: "acme-labs/haystack-rag",
      language: "Python",
      agents: [
        {
          id: "rag",
          name: "Benefits Q&A",
          framework: "Haystack",
          entry: "pipelines/qa.py",
          prompts: ["pipelines/templates/answer.jinja"],
          tools: 2,
        },
      ],
    },
  ],
  bitbucket: [
    {
      name: "acme/banking-assistant",
      language: "Python",
      agents: [
        {
          id: "banking",
          name: "Card services assistant",
          framework: "Pydantic AI",
          entry: "assistant/agent.py:card_agent",
          prompts: ["assistant/instructions.md"],
          tools: 6,
        },
      ],
    },
    {
      name: "acme/web-chat",
      language: "TypeScript",
      agents: [
        {
          id: "webchat",
          name: "Website chat",
          framework: "Vercel AI SDK",
          entry: "app/api/chat/route.ts",
          prompts: ["lib/system-prompt.ts"],
          tools: 4,
        },
      ],
    },
    {
      name: "acme-labs/smol-experiments",
      language: "Python",
      agents: [
        {
          id: "smol",
          name: "Shipping tracker",
          framework: "smolagents",
          entry: "agent.py",
          prompts: ["prompts.yaml"],
          tools: 3,
        },
      ],
    },
  ],
};

/** Sample repositories per host, with the agents a scan finds in each. */
export const SAMPLE_REPOS = Object.fromEntries(
  Object.entries(RAW_REPOS).map(([host, repos]) => [
    host,
    repos.map(withSiblings),
  ]),
);

export const repoBranches = (repo) => [
  "main",
  "develop",
  "release/2026.10",
  hash(repo?.name) % 2 ? "feat/handoff-rules" : "fix/refund-wording",
];

/* How long reading a repository for agents takes, and when it's a timeout. */
export const SCAN_MS = 1200;
export const SCAN_TIMEOUT_MS = 2600;
const FAILS_FIRST = {
  "acme-labs/notebooks": "error",
  "acme-inc/infra-terraform": "timeout",
};
/** "ok" | "error" | "timeout": how reading `repoName` goes on `attempt` (0 is the first). */
export const scanOutcomeOf = (repoName, attempt = 0) =>
  (attempt === 0 && FAILS_FIRST[String(repoName || "").toLowerCase()]) || "ok";

/** The folder an entry file lives in: "." for the repository root. */
export const dirOf = (path) =>
  String(path || "")
    .split(":")[0]
    .split("/")
    .slice(0, -1)
    .join("/") || ".";

const KEY_POOLS = {
  returns: ["SHOPIFY_ADMIN_TOKEN", "STRIPE_KEY"],
  billing: ["STRIPE_KEY", "PLAID_SECRET"],
  research: ["SERPAPI_KEY"],
};
const keyPoolFor = (text) => {
  const t = String(text || "").toLowerCase();
  if (/bill|disput|card|payment|bank/.test(t)) return KEY_POOLS.billing;
  if (/research|notebook/.test(t)) return KEY_POOLS.research;
  return KEY_POOLS.returns;
};

/** The environment value names an agent's code reads, as a scan reports them. */
export const credentialKeysOf = (repo, agent) => {
  const framework = agent?.framework || "Direct API calls";
  const tsRepo = repo?.language === "TypeScript";
  const model = `${framework} ${agent?.sdk || ""}`;
  const llm = /claude|anthropic/i.test(model)
    ? "ANTHROPIC_API_KEY"
    : /gemini|adk/i.test(model)
      ? "GOOGLE_API_KEY"
      : "OPENAI_API_KEY";
  const livekit = /livekit/i.test(framework);
  return [
    ...new Set([
      tsRepo ? "OPENAI_API_KEY" : llm,
      ...(livekit
        ? ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DEEPGRAM_API_KEY"]
        : []),
      ...keyPoolFor(`${repo?.name || ""} ${agent?.entry || ""}`),
      "LOG_LEVEL",
    ]),
  ];
};

/*
  The hosted platforms without a connector yet: the key's label, the ID the
  platform's dashboard shows, the channels one agent serves, and the chat
  channels a simulation can reach it on.
*/
export const HOSTED_SOON = {
  elevenlabs: {
    name: "ElevenLabs",
    keyLabel: "ElevenLabs API key",
    idLabel: "Agent ID",
    channels: ["voice", "chat"],
    chatChannels: [
      { id: "chat", label: "Chat" },
      { id: "whatsapp", label: "WhatsApp", icon: "simple-icons:whatsapp" },
    ],
    needsVersioning: true,
    versioningNote:
      "Branches need versioning switched on for the agent, which can't be undone. You switch it on.",
  },
  bland: {
    name: "Bland.ai",
    keyLabel: "Bland API key",
    idLabel: "Agent ID",
    channels: ["voice", "chat"],
    idNote: {
      voice:
        "Agents on Bland's agent model. A pathway agent connects once it has moved to that model.",
    },
    chatChannels: [{ id: "messaging", label: "Messaging" }],
  },
  telnyx: {
    name: "Telnyx",
    keyLabel: "Telnyx API key",
    idLabel: "Assistant ID",
    channels: ["voice"],
  },
  google_cx: {
    name: "Google CX Agent Studio",
    keyLabel: "Service account key",
    idLabel: "Agent resource name",
    channels: ["voice", "chat"],
    idNote: {
      voice:
        "Dialogflow CX agents. The resource name is on the agent's settings page.",
      chat: "Dialogflow CX agents. The resource name is on the agent's settings page.",
    },
    parts: "Flows",
  },
  voiceflow: {
    name: "Voiceflow",
    keyLabel: "Voiceflow API key",
    idLabel: "Project ID",
    channels: ["voice", "chat"],
    parts: "Workflows",
  },
  agentforce: {
    name: "Salesforce Agentforce",
    keyLabel: "Connected app credentials",
    idLabel: "Agent API name",
    channels: ["chat"],
    parts: "Topics",
  },
  copilot_studio: {
    name: "Microsoft Copilot Studio",
    keyLabel: "Entra app credentials",
    idLabel: "Agent schema name",
    channels: ["chat"],
    parts: "Topics",
  },
};

export const PLATFORM_KEY_HELPER =
  "Stored encrypted. Used only to read the agent, make test copies and place test calls. Never shown again, never sent to the improver.";

const members = (n) => Array.from({ length: n }, (_, i) => ({ id: `m${i}` }));

/* What listing agents with the key returns. A voice agent has the number it
   answers on; an untagged one serves both channels. */
const SAMPLE_ASSISTANTS = {
  elevenlabs: [
    {
      id: "agent_01jx8k2m4n",
      name: "Concierge",
      updated: "5 hours ago",
      phone: "+44 20 7946 0811",
      members: members(3),
    },
    {
      id: "agent_01jw3p9q7r",
      name: "Order status",
      updated: "3 days ago",
      phone: null,
    },
    {
      id: "agent_01jy5t2w8x",
      name: "WhatsApp concierge",
      updated: "6 hours ago",
      phone: "+44 7700 900412",
      channels: ["chat"],
    },
  ],
  bland: [
    {
      id: "agent_3e9d71",
      name: "Lead qualification",
      updated: "yesterday",
      phone: "+1 312 555 0164",
      channels: ["voice"],
    },
    {
      id: "agent_c04a28",
      name: "Delivery rescheduling",
      updated: "last week",
      phone: "+1 312 555 0108",
    },
    {
      id: "agent_m7f15b",
      name: "Appointment reminders (SMS)",
      updated: "2 days ago",
      phone: "+1 312 555 0152",
      channels: ["chat"],
    },
  ],
  telnyx: [
    {
      id: "assistant-7c1e9a42-ba0d",
      name: "Clinic scheduling",
      updated: "1 hour ago",
      phone: "+1 512 555 0123",
      channels: ["voice"],
    },
    {
      id: "assistant-1f0e33c8-77aa",
      name: "Order status line",
      updated: "3 days ago",
      phone: "+1 512 555 0186",
      channels: ["voice"],
    },
  ],
  google_cx: [
    {
      id: "projects/acme-support/locations/global/agents/5f2c1d9e",
      name: "Account support",
      updated: "yesterday",
      phone: "+1 650 555 0144",
      members: members(3),
    },
    {
      id: "projects/acme-support/locations/global/agents/a81b44f3",
      name: "Store locator",
      updated: "last week",
      phone: null,
      channels: ["chat"],
    },
  ],
  voiceflow: [
    {
      id: "66f2c1d9e7a0b1c2",
      name: "Booking assistant",
      updated: "2 hours ago",
      phone: "+1 312 555 0119",
      members: members(2),
    },
    {
      id: "66e0aa13b2c4d5e6",
      name: "Help-centre widget",
      updated: "5 days ago",
      phone: null,
      channels: ["chat"],
    },
  ],
  agentforce: [
    {
      id: "Service_Agent",
      name: "Service Agent",
      updated: "4 hours ago",
      phone: null,
      channels: ["chat"],
      members: members(3),
    },
    {
      id: "Returns_Agent",
      name: "Returns Agent",
      updated: "last week",
      phone: null,
      channels: ["chat"],
    },
  ],
  copilot_studio: [
    {
      id: "cr4f2_itHelpDesk",
      name: "IT help desk",
      updated: "yesterday",
      phone: null,
      channels: ["chat"],
      members: members(2),
    },
    {
      id: "cr4f2_hrPolicies",
      name: "HR policies",
      updated: "2 weeks ago",
      phone: null,
      channels: ["chat"],
    },
  ],
};

/** The sample agents a key lists for one channel. */
export const assistantsFor = (platform, channel) =>
  (SAMPLE_ASSISTANTS[platform] || []).filter(
    (a) => !a.channels || a.channels.includes(channel),
  );

/** "Workflow · 3 nodes", "Topics · 3": what an imported agent is made of. */
export const structureOf = (platform, assistant) => {
  const n = assistant?.members?.length || 0;
  if (!n) return null;
  const parts = HOSTED_SOON[platform]?.parts;
  return parts ? `${parts} · ${n}` : `Workflow · ${n} nodes`;
};

/** "+1 415 555 0142" as the country and the rest, for the contact fields. */
export const phoneParts = (phone) => {
  const m = /^(\+\d+)\s+(.*)$/.exec(String(phone || "").trim());
  if (!m) return { countryIso: "US", number: "" };
  return {
    countryIso:
      { "+1": "US", "+44": "GB", "+91": "IN", "+61": "AU" }[m[1]] || "US",
    number: m[2].replace(/\D/g, ""),
  };
};

/** An Observe project's slug, from the agent's name. */
export const observeSlugOf = (name) =>
  String(name || "agent")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "") || "agent";

/*
  Closed platforms don't expose their agents' settings, so they're test
  only: through their phone line, chat channel, or a conversation API where
  one exists. `api` names what the conversation API is called.
*/
export const CLOSED_PLATFORM_REACH = {
  sierra: {
    ways: ["phone", "chat"],
    chatPlaceholder: "https://acme.sierra.chat/agent",
  },
  decagon: {
    ways: ["phone", "chat", "api"],
    api: "Conversation API",
    apiPlaceholder: "https://api.decagon.ai/conversation",
    chatPlaceholder: "https://acme.decagon.ai/chat",
  },
  intercom_fin: {
    ways: ["chat", "api"],
    api: "Conversations API",
    apiPlaceholder: "https://api.intercom.io/conversations",
    chatPlaceholder: "https://acme.intercom.help/messenger",
  },
  zendesk: {
    ways: ["phone", "chat", "api"],
    api: "Sunshine Conversations API",
    apiPlaceholder: "https://api.smooch.io/v2/apps/…/conversations",
    chatPlaceholder: "https://acme.zendesk.com/chat",
  },
};
export const CLOSED_REACH_LABELS = {
  phone: "Phone line",
  chat: "Chat channel",
  api: "Conversation API",
};

/* Where to add a secondary webhook, per stack: a line each, not a guide. */
export const WEBHOOK_PROVIDERS = [
  {
    id: "vapi",
    label: "Vapi",
    steps:
      "Assistant → Advanced → Server URL. Add this as a second server URL; keep your own as the first.",
  },
  {
    id: "retell",
    label: "Retell",
    steps:
      "Agent → Webhook settings → Add webhook. Subscribe to call_started, call_ended and call_analyzed.",
  },
  {
    id: "bland",
    label: "Bland",
    steps:
      "Pathway or phone number → Webhooks → Add URL. Leave your existing webhook in place.",
  },
  {
    id: "elevenlabs",
    label: "ElevenLabs",
    steps:
      "Agent → Advanced → Post-call webhook. Add it alongside any existing one.",
  },
  {
    id: "server",
    label: "Your server",
    steps:
      "POST each turn's tool calls and results here as JSON, after your own handlers run. Fire and forget. We never respond with data.",
  },
];

/** A sample of the webhook URL an endpoint's production agent would mirror to. */
export const sampleWebhookUrlFor = (endpoint) =>
  `https://hooks.futureagi.com/v1/sim/${observeSlugOf(
    String(endpoint || "")
      .replace(/^\w+:\/\//, "")
      .split("/")[0],
  )}/${shortSha(endpoint)}`;
