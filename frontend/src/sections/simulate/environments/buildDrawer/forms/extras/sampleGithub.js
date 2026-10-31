/*
  Sample data for the GitHub App picker, which has no API yet: the accounts
  an install covers, their repositories and what a scan of each finds. Two
  repositories stand in for a first read that fails or times out.
*/

export const SAMPLE_GITHUB_ACCOUNTS = ["acme-inc", "acme-labs"];

const agent = (id, name, framework, entry, prompts, tools) => ({
  id,
  name,
  framework,
  entry,
  prompts,
  tools,
});

export const SAMPLE_GITHUB_REPOS = [
  {
    name: "acme-inc/support-voice-agent",
    language: "Python",
    agents: [
      agent(
        "returns",
        "Returns & refunds agent",
        "LiveKit Agents",
        "agents/returns/agent.py",
        ["prompts/returns_system.md"],
        8,
      ),
      agent(
        "triage",
        "Call triage agent",
        "LiveKit Agents",
        "agents/triage/agent.py",
        ["prompts/triage.md"],
        3,
      ),
      agent(
        "shipping",
        "Shipping updates",
        "LiveKit Agents",
        "agents/shipping/agent.py",
        ["prompts/shipping.md"],
        4,
      ),
      agent(
        "loyalty",
        "Loyalty points desk",
        "LiveKit Agents",
        "agents/loyalty/agent.py",
        ["prompts/loyalty.md"],
        2,
      ),
      agent(
        "warranty",
        "Warranty claims",
        "LiveKit Agents",
        "agents/warranty/agent.py",
        ["prompts/warranty.md"],
        5,
      ),
      agent(
        "handoff",
        "Human handoff",
        "LiveKit Agents",
        "agents/handoff/agent.py",
        [],
        1,
      ),
      agent(
        "survey",
        "After-call survey",
        "LiveKit Agents",
        "agents/survey/agent.py",
        ["prompts/survey.md"],
        2,
      ),
    ],
  },
  {
    name: "acme-inc/billing-copilot",
    language: "Python",
    agents: [
      agent(
        "billing",
        "Billing copilot",
        "LangGraph",
        "app/graph.py:billing",
        ["prompts/billing.md"],
        6,
      ),
      agent(
        "disputes",
        "Dispute handler",
        "LangGraph",
        "app/disputes.py",
        ["prompts/disputes.md"],
        4,
      ),
    ],
  },
  {
    name: "acme-inc/travel-chat",
    language: "TypeScript",
    agents: [
      agent(
        "rebooking",
        "Rebooking assistant",
        "OpenAI Agents SDK",
        "src/agents/rebooking.ts",
        ["src/agents/prompts/rebooking.md"],
        5,
      ),
    ],
  },
  { name: "acme-inc/web-frontend", language: "TypeScript", agents: [] },
  { name: "acme-inc/infra", language: "HCL", agents: [] },
  {
    name: "acme-labs/research-agents",
    language: "Python",
    agents: [
      agent(
        "researcher",
        "Research assistant",
        "OpenAI Agents SDK",
        "agents/research.py",
        ["prompts/research.md"],
        4,
      ),
      agent(
        "order-status",
        "Order status helper",
        "OpenAI Agents SDK",
        "agents/order_status.py",
        ["prompts/order_status.md"],
        3,
      ),
    ],
  },
  { name: "acme-labs/notebooks", language: "Jupyter Notebook", agents: [] },
];

export const SAMPLE_BRANCHES = ["main", "develop", "release/2026.10"];

export const sampleRepoOf = (name) =>
  SAMPLE_GITHUB_REPOS.find((r) => r.name === name) || null;

/* How long a sample read takes, and how long before it's called a timeout. */
export const SCAN_MS = 1200;
export const SCAN_TIMEOUT_MS = 2600;

const FAILS_FIRST = {
  "acme-labs/notebooks": "error",
  "acme-inc/infra": "timeout",
};

/** "ok" | "error" | "timeout": how reading `repoName` goes on `attempt` (0 is the first). */
export const scanOutcomeOf = (repoName, attempt = 0) =>
  (attempt === 0 && FAILS_FIRST[repoName]) || "ok";
