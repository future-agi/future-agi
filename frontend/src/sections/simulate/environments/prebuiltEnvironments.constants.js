// Grouping metadata for the Prebuilt Environments browse page. Templates are
// bucketed under the agent-type group they need, in the same order the connect
// screen lists those groups; empty groups drop out.
export const AGENT_TYPE_GROUPS = [
  "Voice & chat",
  "Computer use",
  "Code",
  "Robotics",
  "Games & worldsims",
  "Tools & protocol",
  "Composite",
];

export const GROUP_BLURBS = {
  "Voice & chat": "Conversational agents on a phone line or a chat endpoint, graded on outcome, policy and tone.",
  "Computer use": "Agents that drive real software by looking at the screen.",
  Robotics: "Physical AI in a physics engine: policies that act and get scored.",
  "Games & worldsims": "Agents that play, explore and beat interactive worlds.",
  "Tools & protocol": "Agents acting through tool APIs against live-looking systems.",
  Code: "Agents that read, write and ship code, graded by real toolchains.",
  Composite: "Multi-agent systems, scored on handoffs as well as outcomes.",
};

// The agent-type group each prebuilt template's agentType belongs to. Mirrors
// the designer's AGENT_TYPES registry, narrowed to the ids the fixture uses.
export const PREBUILT_AGENT_TYPE_GROUP = {
  voice_platform: "Voice & chat",
  chat_webhook: "Voice & chat",
  browser_agent: "Computer use",
  coding_agent: "Code",
  sim_agent: "Robotics",
  game_agent: "Games & worldsims",
  mcp_agent: "Tools & protocol",
  api_agent: "Tools & protocol",
};

// Glyph that anchors a tile, keyed by the environment's surface.
export const SURFACE_ICON = {
  voice: "solar:phone-linear",
  chat: "solar:chat-round-linear",
  code: "solar:code-linear",
  api: "solar:cloud-linear",
};

// Copy for the master/detail browse. A template is a world that already
// exists, so the browse folds the old separate "use template" screen in as an
// inline detail pane — pick a row on the left, its build panel opens on the right.
export const BROWSE_COPY = {
  backTooltip: "Back to how you want to start",
  title: "Use our template",
  subtitle:
    "Prebuilt worlds with seeded state, tools, and rules. Pick one, then build it (here or locally).",
  searchPlaceholder: "Search templates…",
  emptyLibrary: "No prebuilt environments yet.",
  noMatch: (query) => `No templates match "${query}". Try a different search.`,
  popular: "Popular",
};
