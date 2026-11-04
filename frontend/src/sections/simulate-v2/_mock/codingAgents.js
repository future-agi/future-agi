/*
  Coding agents under test (clarified plan, Week 2 — T48): Claude Code or
  Codex, run with the configuration in the user's repository and the user's
  own key, inside our sandbox, graded by checks the agent can't see. A fix
  changes that configuration and comes back as a pull request.

  What a repository's set-up reads as is mocked here, steady per repository.
*/

export const CODING_AGENTS = {
  "claude-code": {
    label: "Claude Code",
    instructions: "CLAUDE.md",
    folder: ".claude",
    keyName: "ANTHROPIC_API_KEY",
    keyLabel: "Anthropic API key",
    model: "claude-sonnet-5-5",
  },
  codex: {
    label: "Codex",
    instructions: "AGENTS.md",
    folder: ".codex",
    keyName: "OPENAI_API_KEY",
    keyLabel: "OpenAI API key",
    model: "gpt-5-codex",
  },
};

const hash = (s) =>
  [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

const SKILLS = [
  "triage-ticket",
  "write-migration",
  "review-pr",
  "release-notes",
  "fix-flaky-test",
];
const MCP = [
  { name: "github", secret: null },
  { name: "linear", secret: "LINEAR_API_KEY" },
  { name: "postgres", secret: "DATABASE_URL" },
  { name: "sentry", secret: "SENTRY_TOKEN" },
];
const HELPERS = ["code-reviewer", "test-writer", "planner"];

/**
 * The coding agent's set-up found in a repository at a branch:
 * { instructions, skills, mcp: [{ name, secret }], helpers, permissions, model }.
 * A repository with none of it runs the plain coding agent on its code.
 */
export function codingSetupOf(agentId, repo) {
  const meta = CODING_AGENTS[agentId];
  if (!meta || !repo) return null;
  const h = hash(`${agentId}|${repo}`);
  if (h % 7 === 0) {
    return { empty: true, model: meta.model };
  }
  return {
    empty: false,
    instructions: meta.instructions,
    skills: SKILLS.slice(0, 1 + (h % SKILLS.length)).map(
      (s) => `${meta.folder}/skills/${s}`,
    ),
    mcp: MCP.slice(0, h % (MCP.length + 1)),
    helpers: HELPERS.slice(0, h % (HELPERS.length + 1)),
    permissions:
      h % 2
        ? "Edits files · runs tests · asks before shell commands"
        : "Edits files · runs any command",
    model: meta.model,
  };
}
