/**
 * What connecting a coding agent takes, per agent and way — the commands,
 * config files and instructions file the connect dialog shows. Kept out of
 * the .jsx so the dialog stays a component-only module.
 */

export const IMPROVE_MCP_URL = "https://mcp.futureagi.com/improve";
export const PLUGIN_INSTALL = "claude plugin install future-agi";

/* The two ways in. Only Claude Code has the plugin. */
export const WAYS = {
  plugin: {
    label: "Plugin",
    short: "plugin",
    blurb:
      "Skills and the MCP server in one install. Claude Code knows the loop out of the box.",
  },
  mcp: {
    label: "MCP server",
    short: "MCP server",
    blurb: "Just the three tools. Your agent's own prompts drive it.",
  },
};

/* Where each agent keeps its MCP servers — Codex's config is TOML. */
const MCP_CONFIG = {
  claude_code: {
    file: ".mcp.json",
    snippet: JSON.stringify(
      {
        mcpServers: {
          "futureagi-improve": { type: "http", url: IMPROVE_MCP_URL },
        },
      },
      null,
      2,
    ),
  },
  cursor: {
    file: "~/.cursor/mcp.json",
    snippet: JSON.stringify(
      { mcpServers: { "futureagi-improve": { url: IMPROVE_MCP_URL } } },
      null,
      2,
    ),
  },
  codex: {
    file: "~/.codex/config.toml",
    snippet: `[mcp_servers.futureagi-improve]\nurl = "${IMPROVE_MCP_URL}"`,
  },
};
export const mcpConfigFor = (agentId) =>
  MCP_CONFIG[agentId] || MCP_CONFIG.claude_code;

/* Without the plugin's skills, the agent learns the loop from a file in the repo. */
const INSTRUCTIONS_FILE = {
  codex: "AGENTS.md",
  cursor: ".cursor/rules/future-agi.mdc",
  claude_code: "CLAUDE.md",
};
export const instructionsFileFor = (agentId) =>
  INSTRUCTIONS_FILE[agentId] || "AGENTS.md";

export const INSTRUCTIONS = [
  "## Fixing issues from Future AGI",
  "",
  "When asked to fix a Future AGI issue or improvement:",
  "1. Call `get_brief` with the id you were given. Read the root cause, evidence and constraints before changing anything.",
  "2. Make the smallest change that addresses the root cause. Stay inside the constraints in the brief.",
  '3. Call `submit_candidate` the way the brief\'s "Submit a candidate" step says: a commit for code in a repository, a settings patch for a hosted agent (Vapi, Retell), a draft version for an agent built in Future AGI. Never edit the live agent.',
  "4. Call `get_verdict` and report it as-is. Do not edit scenarios or evaluations, and do not claim a pass Future AGI didn't return.",
].join("\n");

/* What the server refuses, whatever the agent or its prompt asks for. */
export const GUARDRAILS = [
  {
    title: "Edit scenarios or evaluations",
    body: "They are read-only to it. It changes your agent, never what your agent is measured against.",
  },
  {
    title: "Grade its own candidate",
    body: "Future AGI runs every candidate and computes the verdict. The agent only reads it.",
  },
  {
    title: "Create a version that activates itself",
    body: "A candidate that passes becomes a version. Someone on your team switches to it.",
  },
  {
    title: "Spend beyond the attempt budget",
    body: "Each improvement has an attempt budget. Submissions past it are refused.",
  },
];

/*
  Every coding-agent connection — editor or cloud — gets its own scoped
  improver token (IMPROVER_SCOPES), so held-back scenarios stay hidden from
  it, not just frozen. The proof can't be faked either way: scenarios, evals
  and thresholds are frozen and Future AGI computes every verdict. An older
  connection still on a workspace key shows "Switch to a scoped token".
*/
export const INTEGRITY_NOTE =
  "An improver token can't read held-back scenarios, and the proof can't be faked: scenarios, evals and thresholds are frozen, and Future AGI computes every verdict.";

/* Where a hand-off came from, for the recent list. */
export const targetLabel = (target) => {
  if (!target) return "Brief";
  if (target.kind === "issue") return "Error Feed issue";
  if (target.kind === "run") return "Run diagnosis";
  if (target.kind === "improvement") return "Improvement";
  return "Brief";
};
