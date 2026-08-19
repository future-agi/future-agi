/**
 * Agent type catalog for the "Connect your agent" step.
 *
 * We are explicitly *not* limited to voice + chat any more. Each type declares
 * its own field schema, its own handshake steps and its own direction, because
 * connecting a SIP trunk and connecting a coding agent to a container share
 * almost nothing beyond the word "connect".
 *
 * `direction` drives the copy on the connect screen:
 *   inbound  — their agent calls us  (we hand out an address/number/endpoint)
 *   outbound — we call their agent   (they hand us an endpoint/credentials)
 *   both     — user picks
 */

export const FIELD = {
  TEXT: "text",
  SECRET: "secret",
  URL: "url",
  SELECT: "select",
  TEXTAREA: "textarea",
  SWITCH: "switch",
  KEYVALUE: "keyvalue",
  RADIO_CARD: "radio_card",
};

/** Copy-to-clipboard values we hand the user for inbound connections. */
export const issuedCredential = (kind) =>
  ({
    number: "+1 (415) 555-0182",
    sip: "sip:env-7f3a@sim.futureagi.com",
    webhook: "https://sim.futureagi.com/e/7f3a2c/inbound",
    mcp: "https://sim.futureagi.com/mcp/7f3a2c/sse",
    email: "env-7f3a2c@inbox.sim.futureagi.com",
    token: "fagi_sim_sk_9c2f4b7ae15d8306",
  })[kind];

export const AGENT_TYPES = [
  /* ─────────────────────────── Voice ─────────────────────────── */
  {
    id: "voice_platform",
    group: "Voice & chat",
    label: "Voice agent (platform)",
    blurb: "Vapi, Retell, Bland, ElevenLabs, LiveKit — connect with an API key.",
    icon: "solar:phone-calling-rounded-linear",
    color: "#7857FC",
    surfaces: ["voice"],
    direction: "both",
    handshake: ["Validating API key", "Fetching agent roster", "Reserving a test number", "Placing a silent probe call"],
    fields: [
      {
        key: "provider",
        label: "Provider",
        type: FIELD.SELECT,
        required: true,
        options: [
          { value: "vapi", label: "Vapi" },
          { value: "retell", label: "Retell AI" },
          { value: "bland", label: "Bland.ai" },
          { value: "elevenlabs", label: "ElevenLabs" },
          { value: "livekit", label: "LiveKit" },
          { value: "other", label: "Other / custom" },
        ],
      },
      { key: "apiKey", label: "API key", type: FIELD.SECRET, required: true, placeholder: "sk_live_…" },
      { key: "agentId", label: "Agent ID", type: FIELD.TEXT, required: true, placeholder: "asst_9f2c…", help: "We can list these once the key is valid." },
      {
        key: "callDirection",
        label: "Call direction",
        type: FIELD.RADIO_CARD,
        required: true,
        options: [
          { value: "inbound", label: "Inbound", desc: "Your agent answers the number we issue." },
          { value: "outbound", label: "Outbound", desc: "Your agent dials our simulated customer." },
        ],
      },
    ],
  },
  {
    id: "voice_sip",
    group: "Voice & chat",
    label: "Voice agent (raw SIP)",
    blurb: "Bring your own telephony. We register against your SIP trunk.",
    icon: "solar:server-minimalistic-linear",
    color: "#6D28D9",
    surfaces: ["voice"],
    direction: "inbound",
    handshake: ["Resolving SIP host", "Registering", "Negotiating codec (opus)", "Sending OPTIONS ping"],
    fields: [
      { key: "sipUri", label: "SIP URI", type: FIELD.TEXT, required: true, placeholder: "sip:agent@your-trunk.com" },
      { key: "username", label: "Username", type: FIELD.TEXT, required: true },
      { key: "password", label: "Password", type: FIELD.SECRET, required: true },
      { key: "codec", label: "Preferred codec", type: FIELD.SELECT, options: [
        { value: "opus", label: "Opus" }, { value: "pcmu", label: "PCMU (G.711µ)" }, { value: "pcma", label: "PCMA (G.711a)" },
      ] },
    ],
  },

  /* ─────────────────────────── Text / chat ─────────────────────────── */
  {
    id: "chat_webhook",
    group: "Voice & chat",
    label: "Chat agent (HTTP)",
    blurb: "Any endpoint that takes a message and returns a reply.",
    icon: "solar:chat-round-dots-linear",
    color: "#2563EB",
    surfaces: ["chat", "messaging"],
    direction: "outbound",
    handshake: ["Resolving endpoint", "Checking auth", "Sending probe message", "Mapping response shape"],
    fields: [
      { key: "endpoint", label: "Endpoint URL", type: FIELD.URL, required: true, placeholder: "https://api.yourapp.com/agent/chat" },
      { key: "auth", label: "Authentication", type: FIELD.SELECT, required: true, options: [
        { value: "bearer", label: "Bearer token" }, { value: "apikey", label: "API key header" },
        { value: "basic", label: "Basic auth" }, { value: "none", label: "No auth" },
      ] },
      { key: "token", label: "Token", type: FIELD.SECRET, dependsOn: { key: "auth", not: "none" } },
      { key: "headers", label: "Extra headers", type: FIELD.KEYVALUE },
      { key: "messagePath", label: "Reply JSON path", type: FIELD.TEXT, placeholder: "$.choices[0].message.content", help: "Where the reply text lives in your response." },
      { key: "sessionPath", label: "Session ID path", type: FIELD.TEXT, placeholder: "$.session_id", help: "Lets us keep multi-turn state." },
      { key: "streaming", label: "Response is streamed (SSE)", type: FIELD.SWITCH },
    ],
  },
  {
    id: "chat_openai",
    group: "Voice & chat",
    label: "OpenAI-compatible endpoint",
    blurb: "Anything speaking /v1/chat/completions — vLLM, Ollama, Together, your gateway.",
    icon: "solar:widget-add-linear",
    color: "#0EA5E9",
    surfaces: ["chat", "api"],
    direction: "outbound",
    handshake: ["Reaching /v1/models", "Confirming model id", "Probing a completion", "Checking tool-call support"],
    fields: [
      { key: "baseUrl", label: "Base URL", type: FIELD.URL, required: true, placeholder: "https://api.yourapp.com/v1" },
      { key: "apiKey", label: "API key", type: FIELD.SECRET, required: true },
      { key: "model", label: "Model", type: FIELD.TEXT, required: true, placeholder: "my-support-agent-v3" },
      { key: "systemPrompt", label: "System prompt override", type: FIELD.TEXTAREA, help: "Optional. Leave blank to use whatever your endpoint already does." },
    ],
  },

  /* ─────────────────────────── Computer use ─────────────────────────── */
  {
    id: "browser_agent",
    group: "Computer use",
    label: "Browser agent",
    blurb: "Your agent drives a browser we host. Full screen recording of every step.",
    icon: "solar:monitor-smartphone-linear",
    color: "#EA580C",
    surfaces: ["browser"],
    direction: "inbound",
    handshake: ["Booting sandbox VM", "Starting Chromium", "Opening CDP socket", "Streaming first frame"],
    issues: ["cdp", "token"],
    fields: [
      { key: "framework", label: "Framework", type: FIELD.SELECT, required: true, options: [
        { value: "playwright", label: "Playwright" }, { value: "puppeteer", label: "Puppeteer" },
        { value: "browser_use", label: "browser-use" }, { value: "stagehand", label: "Stagehand" },
        { value: "cua", label: "OpenAI computer-use" }, { value: "claude_cu", label: "Claude computer-use" },
        { value: "custom", label: "Custom / raw CDP" },
      ] },
      { key: "viewport", label: "Viewport", type: FIELD.SELECT, options: [
        { value: "1280x800", label: "1280 × 800 (desktop)" }, { value: "1920x1080", label: "1920 × 1080 (desktop HD)" },
        { value: "390x844", label: "390 × 844 (mobile)" },
      ] },
      { key: "recordVideo", label: "Record video of every task", type: FIELD.SWITCH, default: true },
    ],
  },
  {
    id: "computer_agent",
    group: "Computer use",
    label: "Desktop / OS agent",
    blurb: "A full desktop VM — file manager, spreadsheet, native apps.",
    icon: "solar:display-linear",
    color: "#B45309",
    surfaces: ["browser"],
    direction: "inbound",
    handshake: ["Provisioning desktop VM", "Installing seed apps", "Starting VNC", "Streaming first frame"],
    fields: [
      { key: "os", label: "Operating system", type: FIELD.SELECT, required: true, options: [
        { value: "ubuntu", label: "Ubuntu 24.04" }, { value: "windows", label: "Windows 11" }, { value: "macos", label: "macOS (beta)" },
      ] },
      { key: "apps", label: "Pre-installed apps", type: FIELD.TEXT, placeholder: "libreoffice, firefox, thunderbird" },
      { key: "resolution", label: "Resolution", type: FIELD.SELECT, options: [
        { value: "1280x800", label: "1280 × 800" }, { value: "1920x1080", label: "1920 × 1080" },
      ] },
    ],
  },

  /* ─────────────────────────── Tools / protocol ─────────────────────────── */
  {
    id: "mcp_agent",
    group: "Tools & protocol",
    label: "MCP agent",
    blurb: "We publish the environment as MCP tools; your agent connects and acts.",
    icon: "mdi:transit-connection-variant",
    color: "#DB2777",
    surfaces: ["mcp", "api"],
    direction: "inbound",
    handshake: ["Publishing tool manifest", "Waiting for client", "Negotiating protocol version", "Listing granted tools"],
    fields: [
      { key: "transport", label: "Transport", type: FIELD.SELECT, required: true, options: [
        { value: "sse", label: "HTTP + SSE" }, { value: "stdio", label: "stdio" }, { value: "ws", label: "WebSocket" },
      ] },
      { key: "clientName", label: "Client name", type: FIELD.TEXT, placeholder: "my-agent/1.0" },
      { key: "toolTimeout", label: "Per-tool timeout (s)", type: FIELD.TEXT, placeholder: "30" },
    ],
  },
  {
    id: "api_agent",
    group: "Tools & protocol",
    label: "API / workflow agent",
    blurb: "Task in, result out. Good for batch, RAG and back-office agents.",
    icon: "solar:code-square-linear",
    color: "#9333EA",
    surfaces: ["api"],
    direction: "outbound",
    handshake: ["Resolving endpoint", "Checking auth", "Submitting probe task", "Polling for result"],
    fields: [
      { key: "endpoint", label: "Task endpoint", type: FIELD.URL, required: true, placeholder: "https://api.yourapp.com/tasks" },
      { key: "method", label: "Method", type: FIELD.SELECT, options: [{ value: "POST", label: "POST" }, { value: "PUT", label: "PUT" }] },
      { key: "auth", label: "Authentication", type: FIELD.SELECT, options: [
        { value: "bearer", label: "Bearer token" }, { value: "apikey", label: "API key header" }, { value: "none", label: "No auth" },
      ] },
      { key: "token", label: "Token", type: FIELD.SECRET, dependsOn: { key: "auth", not: "none" } },
      { key: "mode", label: "Result delivery", type: FIELD.RADIO_CARD, options: [
        { value: "sync", label: "Synchronous", desc: "Result comes back in the response body." },
        { value: "poll", label: "Poll for result", desc: "We poll a status URL until it settles." },
        { value: "callback", label: "Callback", desc: "You POST back to a URL we issue." },
      ] },
    ],
  },

  /* ─────────────────────────── Code ─────────────────────────── */
  {
    id: "coding_agent",
    group: "Code",
    label: "Coding agent (CLI)",
    blurb: "Runs in a container with a seeded repo. We watch commands and diffs.",
    icon: "solar:command-linear",
    color: "#525252",
    surfaces: ["cli"],
    direction: "inbound",
    handshake: ["Building container image", "Cloning seed repo", "Installing dependencies", "Attaching to pty"],
    fields: [
      { key: "runtime", label: "Runtime", type: FIELD.SELECT, required: true, options: [
        { value: "docker", label: "Docker image" }, { value: "e2b", label: "E2B sandbox" }, { value: "ssh", label: "Your own host (SSH)" },
      ] },
      { key: "image", label: "Image / template", type: FIELD.TEXT, required: true, placeholder: "ghcr.io/acme/agent:latest" },
      { key: "command", label: "Entry command", type: FIELD.TEXT, required: true, placeholder: "my-agent --task \"$FAGI_TASK\"" },
      { key: "env", label: "Environment variables", type: FIELD.KEYVALUE },
      { key: "network", label: "Allow outbound network", type: FIELD.SWITCH, default: true },
    ],
  },
  {
    id: "framework_agent",
    group: "Code",
    label: "Framework agent (SDK)",
    blurb: "LangGraph, CrewAI, OpenAI Agents SDK, Google ADK — traced via our SDK.",
    icon: "solar:atom-linear",
    color: "#0D9488",
    surfaces: ["api", "mcp", "chat"],
    direction: "inbound",
    handshake: ["Issuing project token", "Waiting for first span", "Verifying trace schema", "Linking to environment"],
    fields: [
      { key: "framework", label: "Framework", type: FIELD.SELECT, required: true, options: [
        { value: "langgraph", label: "LangGraph" }, { value: "crewai", label: "CrewAI" },
        { value: "openai_agents", label: "OpenAI Agents SDK" }, { value: "adk", label: "Google ADK" },
        { value: "autogen", label: "AutoGen" }, { value: "custom", label: "Custom (OTel)" },
      ] },
      { key: "language", label: "Language", type: FIELD.SELECT, options: [{ value: "python", label: "Python" }, { value: "ts", label: "TypeScript" }] },
      { key: "entrypoint", label: "Graph / crew entrypoint", type: FIELD.TEXT, placeholder: "app.graph:support_agent" },
    ],
  },

  /* ──────────────────── Robotics & games ──────────────────── */
  {
    id: "sim_agent",
    group: "Robotics",
    label: "Embodied / policy agent",
    blurb: "Acts in a stepped physics world. Scored on reward, not conversation.",
    icon: "solar:cpu-bolt-linear",
    color: "#8B5CF6",
    surfaces: ["sim"],
    direction: "inbound",
    handshake: ["Starting physics engine", "Loading scene assets", "Opening step channel", "Sending first observation"],
    fields: [
      { key: "engine", label: "Simulator", type: FIELD.SELECT, required: true, options: [
        { value: "mujoco", label: "MuJoCo" }, { value: "newton", label: "Newton physics" },
        { value: "isaac", label: "Isaac Sim" }, { value: "pybullet", label: "PyBullet" },
      ] },
      { key: "policy", label: "Policy endpoint", type: FIELD.URL, required: true, placeholder: "http://localhost:8080/act", help: "We POST an observation and expect an action back." },
      { key: "controlHz", label: "Control rate (Hz)", type: FIELD.TEXT, placeholder: "20" },
      { key: "maxSteps", label: "Max steps per episode", type: FIELD.TEXT, placeholder: "500" },
      { key: "recordVideo", label: "Record video of every episode", type: FIELD.SWITCH, default: true },
    ],
  },
  {
    id: "game_agent",
    group: "Games & worldsims",
    label: "Game-playing agent",
    blurb: "Sees frames, presses buttons. For emulators and interactive worlds.",
    icon: "solar:gamepad-linear",
    color: "#C026D3",
    surfaces: ["sim"],
    direction: "inbound",
    handshake: ["Booting emulator", "Loading ROM / level", "Attaching frame capture", "Streaming first frame"],
    fields: [
      { key: "observation", label: "Observation type", type: FIELD.RADIO_CARD, required: true, options: [
        { value: "pixels", label: "Pixels", desc: "Raw frames, as a human would see them." },
        { value: "symbolic", label: "Symbolic", desc: "Structured game state instead of an image." },
      ] },
      { key: "policy", label: "Policy endpoint", type: FIELD.URL, required: true, placeholder: "http://localhost:8080/act" },
      { key: "frameSkip", label: "Frame skip", type: FIELD.TEXT, placeholder: "4" },
      { key: "allowSaveStates", label: "Allow save states", type: FIELD.SWITCH },
    ],
  },

  /* ─────────────────────────── Composite ─────────────────────────── */
  {
    id: "multi_agent",
    group: "Composite",
    label: "Multi-agent system",
    blurb: "A supervisor plus workers. We score handoffs as well as outcomes.",
    icon: "solar:users-group-rounded-linear",
    color: "#CA8A04",
    surfaces: ["multi", "api", "chat"],
    direction: "inbound",
    handshake: ["Issuing project token", "Discovering agent graph", "Mapping handoff edges", "Verifying trace schema"],
    fields: [
      { key: "topology", label: "Topology", type: FIELD.RADIO_CARD, required: true, options: [
        { value: "supervisor", label: "Supervisor", desc: "One router delegates to specialists." },
        { value: "swarm", label: "Swarm", desc: "Peers hand off to each other freely." },
        { value: "pipeline", label: "Pipeline", desc: "Fixed sequence of stages." },
      ] },
      { key: "entrypoint", label: "Entry agent", type: FIELD.TEXT, required: true, placeholder: "supervisor" },
      { key: "scoreHandoffs", label: "Evaluate handoff quality", type: FIELD.SWITCH, default: true },
    ],
  },
];

/**
 * Group order. This doubles as the section order on the Environments gallery —
 * environments are grouped by the agent type they need, so the two screens
 * present the same taxonomy in the same sequence.
 */
export const AGENT_TYPE_GROUPS = [
  "Voice & chat",
  "Computer use",
  "Robotics",
  "Games & worldsims",
  "Tools & protocol",
  "Code",
  "Composite",
];

export const getAgentType = (id) => AGENT_TYPES.find((t) => t.id === id);

/** Agent types that make sense for a given environment surface, best first. */
export const agentTypesForSurface = (surfaceId) => {
  const fits = AGENT_TYPES.filter((t) => t.surfaces.includes(surfaceId));
  const rest = AGENT_TYPES.filter((t) => !t.surfaces.includes(surfaceId));
  return { recommended: fits, others: rest };
};

/**
 * Groups where the environment genuinely *is* a set of actions, so it can be
 * published as MCP tools and the agent can drive it.
 *
 * Voice and chat are deliberately absent: there the environment is a caller on
 * the other end of a line, not a toolbox, and handing that agent an MCP server
 * would give it nothing to answer.
 */
export const MCP_GROUPS = [
  "Computer use", "Robotics", "Games & worldsims", "Tools & protocol", "Code", "Composite",
];

export const supportsMcp = (type) => !!type && MCP_GROUPS.includes(type.group);

const slugFor = (env) => (env?.name || "environment").toLowerCase().replace(/[^a-z0-9]+/g, "-");

/**
 * The config a user pastes into their MCP client.
 *
 * `Scenario-Id` is the part that makes this an eval rather than a sandbox: one
 * session per task, so we still control ordering and reset the world in
 * between. Without it an agent would connect once and wander, and nothing
 * would be comparable between runs.
 */
export const mcpConfig = (env, { scenarioScoped = true } = {}) => {
  const slug = slugFor(env);
  const headers = {
    Authorization: "Bearer <FAGI_API_KEY>",
    "Environment-Name": slug,
    ...(scenarioScoped ? { "Scenario-Id": "<SCENARIO_ID>" } : {}),
  };
  return JSON.stringify(
    { mcpServers: { [slug]: { url: "https://sim.futureagi.com/v1/mcp", headers } } },
    null,
    2,
  );
};

/** Same connection, expressed for people wiring it up in code. */
export const mcpPythonSnippet = (env) => {
  const slug = slugFor(env);
  return [
    "from fagi.sim import Environment",
    "",
    `env = Environment.connect(`,
    `    "${slug}",`,
    '    api_key=os.environ["FAGI_API_KEY"],',
    ")",
    "",
    "# One session per scenario — the world resets in between.",
    "for task in env.tasks():",
    "    async with env.session(task) as tools:",
    "        await your_agent.run(task.prompt, tools=tools)",
  ].join("\n");
};
