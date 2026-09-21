/**
 * Surfaces = the channel an environment exposes to the agent under test.
 *
 * An environment is a *composite*: surface (how the agent talks to the world)
 * + domain (what world it is talking to — mock data, tools, business rules).
 * This file owns the surface half; `environments.js` composes the two.
 *
 * `stage` picks which live-run visualiser renders during a simulation, so
 * adding a surface here is what makes a new agent kind "watchable".
 */

export const SURFACES = {
  VOICE: "voice",
  CHAT: "chat",
  BROWSER: "browser",
  EMAIL: "email",
  MESSAGING: "messaging",
  API: "api",
  MCP: "mcp",
  CLI: "cli",
  SIM: "sim",
  MULTI: "multi",
};

export const SURFACE_LIST = [
  {
    id: SURFACES.VOICE,
    label: "Voice",
    short: "Voice line",
    icon: "solar:phone-calling-rounded-linear",
    color: "#7857FC",
    stage: "voice",
    blurb: "Inbound / outbound telephony. We place or answer the call.",
    transports: ["SIP trunk", "WebRTC", "Phone number"],
  },
  {
    id: SURFACES.CHAT,
    label: "Chat",
    short: "Chat widget",
    icon: "solar:chat-round-dots-linear",
    color: "#2563EB",
    stage: "chat",
    blurb: "Text conversation over a webhook or streaming endpoint.",
    transports: ["HTTP webhook", "SSE stream", "WebSocket"],
  },
  {
    id: SURFACES.BROWSER,
    label: "Browser",
    short: "Web app",
    icon: "solar:monitor-smartphone-linear",
    color: "#EA580C",
    stage: "browser",
    blurb: "A real web app in a sandboxed VM. The agent drives the screen.",
    transports: ["CDP", "Playwright", "noVNC"],
  },
  {
    id: SURFACES.EMAIL,
    label: "Email",
    short: "Mailbox",
    icon: "solar:letter-linear",
    color: "#0891B2",
    stage: "email",
    blurb: "A live mailbox the agent reads from and replies into.",
    transports: ["IMAP/SMTP", "Inbound webhook"],
  },
  {
    id: SURFACES.MESSAGING,
    label: "Messaging",
    short: "SMS / WhatsApp",
    icon: "solar:smartphone-linear",
    color: "#16A34A",
    stage: "chat",
    blurb: "SMS, WhatsApp or Slack threads with delivery timing simulated.",
    transports: ["Twilio", "WhatsApp Cloud", "Slack app"],
  },
  {
    id: SURFACES.API,
    label: "API",
    short: "HTTP service",
    icon: "solar:code-square-linear",
    color: "#9333EA",
    stage: "tools",
    blurb: "The agent is an HTTP service. We drive it request by request.",
    transports: ["REST", "GraphQL", "gRPC"],
  },
  {
    id: SURFACES.MCP,
    label: "MCP tools",
    short: "Tool sandbox",
    icon: "mdi:transit-connection-variant",
    color: "#DB2777",
    stage: "tools",
    blurb: "We expose the domain as MCP tools and watch every call.",
    transports: ["MCP stdio", "MCP HTTP", "OpenAPI"],
  },
  {
    id: SURFACES.CLI,
    label: "Terminal",
    short: "Shell sandbox",
    icon: "solar:command-linear",
    color: "#525252",
    stage: "terminal",
    blurb: "A container with a repo and a shell. The agent runs commands.",
    transports: ["Docker exec", "SSH", "E2B"],
  },
  {
    id: SURFACES.SIM,
    label: "Simulator",
    short: "Physics / game sim",
    icon: "solar:gamepad-linear",
    color: "#8B5CF6",
    stage: "sim",
    blurb: "A stepped world — the agent observes, acts, and is scored on reward.",
    transports: ["Gym API", "MuJoCo", "Emulator"],
  },
  {
    id: SURFACES.MULTI,
    label: "Multi-channel",
    short: "Multi-channel",
    icon: "solar:layers-linear",
    color: "#CA8A04",
    stage: "multi",
    blurb: "One task spans channels — call, then email, then a dashboard.",
    transports: ["Composed"],
  },
];

export const getSurface = (id) =>
  SURFACE_LIST.find((s) => s.id === id) || SURFACE_LIST[0];

/** Domains = the business world an environment mocks out. */
export const DOMAINS = [
  { id: "ecommerce", label: "E-commerce", icon: "solar:cart-large-linear" },
  { id: "support", label: "Customer support", icon: "solar:headphones-round-linear" },
  { id: "fintech", label: "Financial services", icon: "solar:card-linear" },
  { id: "healthcare", label: "Healthcare", icon: "solar:health-linear" },
  { id: "travel", label: "Travel & hospitality", icon: "solar:plane-linear" },
  { id: "itops", label: "IT & DevOps", icon: "solar:server-square-linear" },
  { id: "insurance", label: "Insurance", icon: "solar:shield-check-linear" },
  { id: "software", label: "Software engineering", icon: "solar:code-linear" },
  { id: "research", label: "Research & analysis", icon: "solar:magnifer-linear" },
  { id: "operations", label: "Back-office operations", icon: "solar:clipboard-list-linear" },
  { id: "data", label: "Data & analytics", icon: "solar:chart-square-linear" },
  { id: "ml", label: "ML research", icon: "solar:cpu-bolt-linear" },
  { id: "hardware", label: "Hardware & chip design", icon: "solar:cpu-linear" },
  { id: "robotics", label: "Robotics", icon: "solar:cpu-bolt-linear" },
  { id: "gaming", label: "Games & worldsims", icon: "solar:gamepad-linear" },
];

export const getDomain = (id) => DOMAINS.find((d) => d.id === id);
