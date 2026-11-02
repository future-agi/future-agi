/**
 * Workspace tools (GitHub and Coding agents) for Settings → Integrations.
 *
 * Mock data only: there is no backend for these yet. Ported from the
 * simulate-v2 prototype's _mock/integrations.js. Slack's connection is real
 * (src/api/integrations); only its per-event routing and sample messages
 * live here. A tiny external store, persisted to localStorage.
 */
import { useSyncExternalStore } from "react";

const KEY = "fagi.workspaceTools.v1";
const EMPTY = {
  github: null,
  githubRequests: [],
  githubRemoved: [],
  slack: null,
  slackRequest: null,
  slackRemoved: null,
  /* `approvals` stays an empty list: there is no approval step of ours — the
     team's own GitHub reviewers and branch protection decide. Kept only so
     older readers that look it up find nothing. */
  prs: [],
  slackLog: [],
  approvals: [],
  tracing: {},
  syncOnPush: {},
  scans: {},
  codingAgents: {},
  cloudAgents: {},
  improverSessions: [],
  briefHandoffs: [],
  demoShares: {},
  /* GitLab and Bitbucket — one connection per host: { account, tokenRef,
     access: "read" | "write", connectedAt }. */
  gitHosts: {},
  /* Sample rows shown once GitHub is installed (no backend yet). */
  connectedAgents: [],
  /* Slack routing kept here until the backend stores it; the connection itself is real. */
  slackDefaultChannel: null,
  slackRules: {},
};

/* Earlier saves held one GitHub install as { account, access, repos }. */
const migrate = (s) => {
  let next = s;
  /* Saves from when a fix could wait for a Slack approval: no step of ours
     sits before a PR any more, so pending asks and the setting go. */
  if (
    next.approvals?.length ||
    next.slack?.approvals ||
    next.codingAgentPolicy
  ) {
    const { approvals: _a, ...slack } = next.slack || {};
    const { codingAgentPolicy: _p, ...rest } = next;
    next = { ...rest, approvals: [], slack: next.slack ? slack : null };
  }
  if (next.github && !next.github.installations) {
    next = {
      ...next,
      github: {
        installations: [{ id: "inst_1", kind: "Organization", ...next.github }],
      },
    };
  }
  /* One removed installation used to be stored as a single object. */
  if (!Array.isArray(next.githubRemoved))
    next = {
      ...next,
      githubRemoved: next.githubRemoved ? [next.githubRemoved] : [],
    };
  /* Installs saved before access was split into read and write: read only,
     unless we already opened a PR there — that PR needed write, so keep it. */
  if (next.github?.installations?.some((i) => !i.githubAccess)) {
    const prRepos = new Set(
      (next.prs || [])
        .filter((p) => p.status !== "closed" && !p.by)
        .map((p) => p.repo),
    );
    next = {
      ...next,
      github: {
        ...next.github,
        installations: next.github.installations.map((i) =>
          i.githubAccess
            ? i
            : {
                ...i,
                githubAccess: [...prRepos].some((r) =>
                  String(r).startsWith(`${i.account}/`),
                )
                  ? "write"
                  : "read",
              },
        ),
      },
    };
  }
  /* Demo (2026-10-15): Pickup dispatch's code is shared with the app on the
     acme-inc install, so connecting the agent behind its Error Feed issues
     finds a repository that matches its traces. Once — removing it later
     keeps it removed. */
  if (
    !next.demoShares?.pickupDispatch &&
    next.github?.installations?.some((i) => i.account === "acme-inc")
  ) {
    next = {
      ...next,
      demoShares: { ...(next.demoShares || {}), pickupDispatch: true },
      github: {
        ...next.github,
        installations: next.github.installations.map((i) =>
          i.account !== "acme-inc" ||
          i.access === "all" ||
          (i.repos || []).includes("acme-inc/pickup-dispatch")
            ? i
            : { ...i, repos: [...(i.repos || []), "acme-inc/pickup-dispatch"] },
        ),
      },
    };
  }
  return next;
};

/* Mock connections are kept per organization: switching org shows that org's own. */
let scope = null;
const keyOf = () => (scope ? `${KEY}:${scope}` : KEY);

const load = () => {
  try {
    const raw = window.localStorage.getItem(keyOf());
    return raw ? migrate({ ...EMPTY, ...JSON.parse(raw) }) : EMPTY;
  } catch {
    return EMPTY;
  }
};

let state = typeof window === "undefined" ? EMPTY : load();
const listeners = new Set();
const set = (next) => {
  state = { ...state, ...next };
  try {
    window.localStorage.setItem(keyOf(), JSON.stringify(state));
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((l) => l());
};

export const subscribeIntegrations = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
export const getIntegrations = () => state;
export const useIntegrations = () =>
  useSyncExternalStore(subscribeIntegrations, getIntegrations, getIntegrations);

const now = () => new Date().toISOString();
const uid = (p) =>
  `${p}_${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;

/* ── GitHub ───────────────────────────────────────────────────────────────── */

/* Where the person can install. Only an organization's owners can install
   on it; a member's install becomes a request to the owners. */
export const GITHUB_ACCOUNTS = [
  { login: "acme-inc", kind: "Organization", role: "owner" },
  { login: "acme-labs", kind: "Organization", role: "member" },
  /* The seeded Airline agent's home — so its code fix can open a draft PR. */
  { login: "skyline-air", kind: "Organization", role: "owner" },
  { login: "you", kind: "Personal account", role: "owner" },
];

/*
  Demo: every repository with an agent holds at least sixteen, to try
  choosing one agent from a long list (search and scrolling). The extra
  ones are siblings of the repository's first agent — same framework, same
  file layout.
*/
const DEMO_MIN_AGENTS = 16;
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
  ["accessibility", "Accessibility requests"],
  ["handoff", "Human handoff"],
];
const COMMON_FILES =
  /^(agent|bot|main|app|graph|crew|team|pipeline|workflow)\./;
/* "agents/returns/agent.py" → "agents/shipping/agent.py"; "src/agents/rebooking.ts" → "src/agents/shipping.ts". */
const siblingEntry = (entry, id) => {
  const parts = String(entry).split(":")[0].split("/");
  const file = parts[parts.length - 1];
  const slug = id.replace(/-/g, "_");
  if (parts.length >= 3 && COMMON_FILES.test(file))
    return [...parts.slice(0, -2), slug, file].join("/");
  return [
    ...parts.slice(0, -1),
    `${slug}${file.slice(file.lastIndexOf("."))}`,
  ].join("/");
};
const withDemoSiblings = (repo) => {
  if (!repo.agents.length || repo.agents.length >= DEMO_MIN_AGENTS) return repo;
  const base = repo.agents[0];
  const taken = new Set(repo.agents.map((a) => a.id));
  const extra = SIBLINGS.filter(([id]) => !taken.has(id))
    .slice(0, DEMO_MIN_AGENTS - repo.agents.length)
    .map(([id, name], i) => {
      const entry = siblingEntry(base.entry, id);
      return {
        id,
        name,
        framework: base.framework,
        ...(base.sdk && { sdk: base.sdk }),
        entry,
        prompts: [
          `${entry.split("/").slice(0, -1).join("/") || "."}/prompts/${id.replace(/-/g, "_")}.md`.replace(
            /^\.\//,
            "",
          ),
        ],
        tools: 2 + (i % 4),
      };
    });
  return { ...repo, agents: [...repo.agents, ...extra] };
};

/** What a scan of each repository finds. Monorepos hold more than one agent. */
export const GITHUB_REPOS = [
  {
    name: "acme-inc/support-voice-agent",
    language: "Python",
    private: true,
    updated: "2 days ago",
    agents: [
      {
        id: "returns",
        name: "Returns & refunds agent",
        framework: "LiveKit Agents",
        entry: "agents/returns/agent.py",
        prompts: ["prompts/returns_system.md"],
        tools: 8,
      },
      /* Already traced: its code registers traceAI with this project. */
      {
        id: "triage",
        name: "Call triage agent",
        framework: "LiveKit Agents",
        entry: "agents/triage/agent.py",
        prompts: ["prompts/triage.md"],
        tools: 3,
        tracing: {
          project: "call-triage-agent",
          file: "agents/triage/tracing.py",
        },
      },
    ],
  },
  {
    name: "acme-inc/billing-copilot",
    language: "Python",
    private: true,
    updated: "5 hours ago",
    agents: [
      {
        id: "billing",
        name: "Billing copilot",
        framework: "LangGraph",
        entry: "app/graph.py:billing",
        prompts: ["prompts/billing.md"],
        tools: 6,
      },
      {
        id: "disputes",
        name: "Dispute handler",
        framework: "LangGraph",
        entry: "app/disputes.py",
        prompts: ["prompts/disputes.md"],
        tools: 4,
      },
    ],
  },
  {
    name: "acme-inc/travel-chat",
    language: "TypeScript",
    private: true,
    updated: "yesterday",
    agents: [
      {
        id: "rebooking",
        name: "Rebooking assistant",
        framework: "OpenAI Agents SDK",
        entry: "src/agents/rebooking.ts",
        prompts: ["src/agents/prompts/rebooking.md"],
        tools: 5,
      },
    ],
  },
  {
    /* Pickup dispatch's code — its traces (traceAI · LangGraph) land in the
       Pickup dispatch Observe project. Not shared with the app by default. */
    name: "acme-inc/pickup-dispatch",
    language: "Python",
    private: true,
    updated: "6 hours ago",
    agents: [
      {
        id: "dispatch",
        name: "Pickup dispatch agent",
        framework: "LangGraph",
        entry: "app/graph.py:dispatch",
        prompts: ["prompts/dispatch.md"],
        tools: 3,
        model: "gpt-4.1",
        tracing: { project: "pickup-dispatch", file: "app/tracing.py" },
      },
    ],
  },
  {
    name: "acme-inc/web-frontend",
    language: "TypeScript",
    private: false,
    updated: "3 hours ago",
    agents: [],
  },
  {
    name: "acme-inc/infra",
    language: "HCL",
    private: true,
    updated: "last week",
    agents: [],
  },
  {
    name: "acme-labs/research-agents",
    language: "Python",
    private: true,
    updated: "4 days ago",
    agents: [
      {
        id: "researcher",
        name: "Research assistant",
        framework: "OpenAI Agents SDK",
        entry: "agents/research.py",
        prompts: ["prompts/research.md"],
        tools: 4,
      },
      {
        id: "order-status",
        name: "Order status helper",
        framework: "OpenAI Agents SDK",
        entry: "agents/order_status.py",
        prompts: ["prompts/order_status.md"],
        tools: 3,
      },
    ],
  },
  {
    name: "acme-labs/notebooks",
    language: "Jupyter Notebook",
    private: true,
    updated: "2 weeks ago",
    agents: [],
  },
  {
    name: "skyline-air/rebooking-agent",
    language: "Python",
    private: true,
    updated: "today",
    agents: [
      {
        id: "rebooking",
        name: "Airline Rebooking Assistant",
        framework: "OpenAI Agents SDK",
        entry: "agent/main.py",
        prompts: ["agent/prompts/rebooking.md"],
        tools: 6,
      },
    ],
  },
  {
    /* No framework: the code calls the model APIs directly, so the person
       names where a conversation starts (the entry point). */
    name: "acme-inc/faq-assistant",
    language: "Python",
    private: true,
    updated: "3 days ago",
    agents: [
      {
        id: "faq",
        name: "Help-centre FAQ assistant",
        framework: "Direct API calls",
        sdk: "OpenAI SDK",
        entry: "app/chat.py:handle_message",
        prompts: ["app/prompts/faq_system.txt"],
        tools: 2,
        model: "gpt-4.1",
      },
      {
        id: "summary",
        name: "Ticket summariser",
        framework: "Direct API calls",
        sdk: "Anthropic SDK",
        entry: "app/summarise.py:summarise_ticket",
        prompts: ["app/prompts/summary.txt"],
        tools: 1,
        model: "claude-sonnet-4",
      },
    ],
  },
  {
    /* A voice pipeline and a Google ADK agent side by side. */
    name: "acme-inc/concierge-agents",
    language: "Python",
    private: true,
    updated: "8 hours ago",
    agents: [
      {
        id: "concierge-voice",
        name: "Hotel concierge (voice)",
        framework: "Pipecat",
        entry: "voice/bot.py",
        prompts: [
          "voice/prompts/concierge.md",
          "voice/flows/concierge_flow.json",
        ],
        tools: 4,
      },
      {
        id: "concierge-adk",
        name: "Concierge planner",
        framework: "Google ADK",
        entry: "planner/agent.py:root_agent",
        prompts: ["planner/root_agent.yaml"],
        tools: 5,
        model: "gemini-2.5-pro",
      },
      {
        id: "claims-crew",
        name: "Claims review crew",
        framework: "CrewAI",
        entry: "claims/crew.py:ClaimsCrew",
        prompts: ["claims/config/agents.yaml", "claims/config/tasks.yaml"],
        tools: 3,
      },
    ],
  },
  {
    /* The TypeScript-first frameworks. */
    name: "acme-inc/storefront-agents",
    language: "TypeScript",
    private: true,
    updated: "yesterday",
    agents: [
      {
        id: "orders-mastra",
        name: "Order help (Mastra)",
        framework: "Mastra",
        entry: "src/mastra/agents/orders.ts",
        prompts: ["src/mastra/agents/orders.instructions.md"],
        tools: 4,
      },
      {
        id: "returns-ai-sdk",
        name: "Returns chat (AI SDK)",
        framework: "Vercel AI SDK",
        entry: "app/api/chat/route.ts",
        prompts: ["lib/prompts/returns.ts"],
        tools: 3,
      },
    ],
  },
  {
    /* One agent per framework the platform team is trying out. */
    name: "acme-labs/agent-frameworks",
    language: "Python",
    private: true,
    updated: "2 days ago",
    agents: [
      {
        id: "strands",
        name: "Billing helper (Strands)",
        framework: "AWS Strands",
        entry: "strands_agent/agent.py",
        prompts: ["strands_agent/agent.yaml"],
        tools: 3,
      },
      {
        id: "pydantic",
        name: "Refund checker (Pydantic AI)",
        framework: "Pydantic AI",
        entry: "pydantic_agent/agent.py:refund_agent",
        prompts: ["pydantic_agent/instructions.md"],
        tools: 3,
      },
      {
        id: "claude",
        name: "Support agent (Claude Agent SDK)",
        framework: "Claude Agent SDK",
        entry: "claude_agent/main.py",
        prompts: [
          "claude_agent/CLAUDE.md",
          "claude_agent/.claude/agents/refunds.md",
        ],
        tools: 4,
        model: "claude-sonnet-4",
      },
      {
        id: "agno",
        name: "Research team (Agno)",
        framework: "Agno",
        entry: "agno_team/team.py:support_team",
        prompts: ["agno_team/instructions.md"],
        tools: 3,
      },
      {
        id: "haystack",
        name: "Policy Q&A (Haystack)",
        framework: "Haystack",
        entry: "haystack_agent/pipeline.py",
        prompts: ["haystack_agent/prompts/answer.jinja"],
        tools: 2,
      },
      {
        id: "llamaindex",
        name: "Docs agent (LlamaIndex)",
        framework: "LlamaIndex",
        entry: "llama_agent/workflow.py:DocsAgent",
        prompts: ["llama_agent/prompts/system.md"],
        tools: 3,
      },
      {
        id: "smol",
        name: "Order lookup (smolagents)",
        framework: "smolagents",
        entry: "smol_agent/agent.py",
        prompts: ["smol_agent/system_prompt.yaml"],
        tools: 3,
      },
      {
        id: "dspy",
        name: "Intent router (DSPy)",
        framework: "DSPy",
        entry: "dspy_program/router.py:IntentRouter",
        prompts: ["dspy_program/compiled/router.json"],
        tools: 2,
      },
      {
        id: "maf",
        name: "Escalation desk (Microsoft Agent Framework)",
        framework: "Microsoft Agent Framework",
        entry: "maf_agent/app.py",
        prompts: ["maf_agent/instructions.md"],
        tools: 3,
      },
    ],
  },
  {
    /* The platform monorepo: one agent per service — enough that the found
       list needs its search. */
    name: "acme-inc/agent-platform",
    language: "Python",
    private: true,
    updated: "1 hour ago",
    agents: [
      {
        id: "orders",
        name: "Order status agent",
        framework: "LangGraph",
        entry: "services/orders/graph.py:orders",
        prompts: ["services/orders/prompts/system.md"],
        tools: 5,
      },
      {
        id: "returns",
        name: "Returns agent",
        framework: "LangGraph",
        entry: "services/returns/graph.py:returns",
        prompts: ["services/returns/prompts/system.md"],
        tools: 6,
      },
      {
        id: "refunds",
        name: "Refund approvals",
        framework: "LangGraph",
        entry: "services/refunds/graph.py:refunds",
        prompts: ["services/refunds/prompts/system.md"],
        tools: 4,
      },
      {
        id: "shipping",
        name: "Shipping updates",
        framework: "LangGraph",
        entry: "services/shipping/graph.py:shipping",
        prompts: ["services/shipping/prompts/system.md"],
        tools: 3,
      },
      {
        id: "loyalty",
        name: "Loyalty points desk",
        framework: "OpenAI Agents SDK",
        entry: "services/loyalty/agent.py",
        prompts: ["services/loyalty/prompts/system.md"],
        tools: 4,
      },
      {
        id: "billing",
        name: "Billing questions",
        framework: "OpenAI Agents SDK",
        entry: "services/billing/agent.py",
        prompts: ["services/billing/prompts/system.md"],
        tools: 5,
      },
      {
        id: "payments",
        name: "Failed payments",
        framework: "OpenAI Agents SDK",
        entry: "services/payments/agent.py",
        prompts: ["services/payments/prompts/system.md"],
        tools: 3,
      },
      {
        id: "fraud",
        name: "Fraud review",
        framework: "CrewAI",
        entry: "services/fraud/crew.py:FraudCrew",
        prompts: ["services/fraud/prompts/system.md"],
        tools: 4,
      },
      {
        id: "kyc",
        name: "Identity checks (KYC)",
        framework: "CrewAI",
        entry: "services/kyc/crew.py:KycCrew",
        prompts: ["services/kyc/prompts/system.md"],
        tools: 3,
      },
      {
        id: "onboarding",
        name: "Merchant onboarding",
        framework: "Pydantic AI",
        entry: "services/onboarding/agent.py:onboarding",
        prompts: ["services/onboarding/prompts/system.md"],
        tools: 5,
      },
      {
        id: "catalog",
        name: "Catalog assistant",
        framework: "Pydantic AI",
        entry: "services/catalog/agent.py:catalog",
        prompts: ["services/catalog/prompts/system.md"],
        tools: 4,
      },
      {
        id: "pricing",
        name: "Pricing explainer",
        framework: "LlamaIndex",
        entry: "services/pricing/workflow.py:PricingAgent",
        prompts: ["services/pricing/prompts/system.md"],
        tools: 2,
      },
      {
        id: "policies",
        name: "Policy Q&A",
        framework: "LlamaIndex",
        entry: "services/policies/workflow.py:PolicyAgent",
        prompts: ["services/policies/prompts/system.md"],
        tools: 2,
      },
      {
        id: "escalations",
        name: "Escalations router",
        framework: "LangGraph",
        entry: "services/escalations/graph.py:router",
        prompts: ["services/escalations/prompts/system.md"],
        tools: 3,
      },
      {
        id: "voice-orders",
        name: "Order line (voice)",
        framework: "LiveKit Agents",
        entry: "voice/orders/agent.py",
        prompts: ["voice/orders/prompts/system.md"],
        tools: 5,
      },
      {
        id: "voice-returns",
        name: "Returns line (voice)",
        framework: "LiveKit Agents",
        entry: "voice/returns/agent.py",
        prompts: ["voice/returns/prompts/system.md"],
        tools: 6,
      },
      {
        id: "voice-billing",
        name: "Billing line (voice)",
        framework: "Pipecat",
        entry: "voice/billing/bot.py",
        prompts: ["voice/billing/prompts/system.md"],
        tools: 4,
      },
      {
        id: "survey",
        name: "After-call survey",
        framework: "Pipecat",
        entry: "voice/survey/bot.py",
        prompts: ["voice/survey/prompts/system.md"],
        tools: 1,
      },
    ],
  },
].map(withDemoSiblings);

export const GIT_HOST_LABELS = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket",
};
export const GIT_HOST_META = {
  gitlab: {
    label: "GitLab",
    icon: "mdi:gitlab",
    base: "https://gitlab.com",
    account: "Group",
    accountPlaceholder: "acme-inc",
    accounts: ["acme-inc", "acme-labs"],
    token: "Group access token",
    tokenSlot: "vault://acme/sim/gitlab_token",
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
    writeScopes: [
      {
        scope: "write_repository",
        access: "Read & write",
        why: "Push to a new branch we create (future-agi/…). Never to your release branch or anyone's branch.",
      },
      {
        scope: "api",
        access: "Read & write",
        why: "Open draft merge requests from that branch, with the proof in the description. We never merge.",
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
    tokenSlot: "vault://acme/sim/bitbucket_token",
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
    writeScopes: [
      {
        scope: "repository:write",
        access: "Read & write",
        why: "Push to a new branch we create (future-agi/…). Never to your release branch or anyone's branch.",
      },
      {
        scope: "pullrequest:write",
        access: "Read & write",
        why: "Open draft pull requests from that branch, with the proof in the description. We never merge.",
      },
    ],
  },
};

/** What the host calls a proposed change: GitLab's merge request, everyone else's pull request. */
export const reviewNounOf = (host) =>
  host === "gitlab"
    ? { long: "merge request", short: "MR", ref: "!" }
    : { long: "pull request", short: "PR", ref: "#" };

/*
  The install asks for read only — enough to scan for agents, sync on push
  and build environments. Opening a PR means pushing a branch first, and that
  needs Contents write (Pull requests write alone can only open a PR from a
  branch that exists), so write is asked for at the first draft PR instead
  (GITHUB_WRITE_PERMISSIONS). Copy patch works without it.
*/
export const GITHUB_PERMISSIONS = [
  {
    scope: "Contents",
    access: "Read",
    why: "Read the agent's code: prompts, tools, config and the entry point.",
  },
  {
    scope: "Metadata",
    access: "Read",
    why: "List the repositories you chose.",
  },
];
/* Asked for once, at the first draft PR — never at install. */
export const GITHUB_WRITE_PERMISSIONS = [
  {
    scope: "Contents",
    access: "Read & write",
    why: "Push to a new branch we create (future-agi/…). Never to your release branch or anyone's branch.",
  },
  {
    scope: "Pull requests",
    access: "Read & write",
    why: "Open draft PRs from that branch, with the proof in the description. We never merge.",
  },
  {
    scope: "Checks",
    access: "Read & write",
    why: "Post the re-run on the PRs we open.",
  },
];
/* Events GitHub sends us once installed. */
export const GITHUB_EVENTS = [
  "push",
  "pull_request",
  "installation",
  "installation_repositories",
];

export const repoUrlOf = (name) => `https://github.com/${name}`;
export const repoNameOf = (url) =>
  String(url || "")
    .replace(/^https?:\/\/github\.com\//, "")
    .replace(/\/+$/, "");

export const accountOf = (login) =>
  GITHUB_ACCOUNTS.find((a) => a.login === login);
export const githubInstalls = (s = state) => s.github?.installations || [];
export const installationFor = (account, s = state) =>
  githubInstalls(s).find((i) => i.account === account) || null;
/** "acme-inc" or "acme-inc and you" — for one-line summaries. */
/* "acme-inc, your account and acme-labs" — a personal install is "your account". */
export const githubAccountsLabel = (s = state) => {
  const names = githubInstalls(s).map((i) =>
    i.account === "you" ? "your account" : i.account,
  );
  return names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`
    : names[0] || "";
};
export const githubAccountCount = (s = state) => githubInstalls(s).length;

const withInstalls = (installations) =>
  installations.length ? { github: { installations } } : { github: null };

/** GitHub sent the person back with `setup_action=install` (or `update`). */
export const installGithub = ({
  account,
  access,
  repos,
  installedBy = "you",
}) => {
  const first = githubInstalls().length === 0;
  const others = githubInstalls().filter((i) => i.account !== account);
  const prev = installationFor(account);
  set({
    ...withInstalls([
      ...others,
      {
        id: prev?.id || uid("inst"),
        account,
        kind: accountOf(account)?.kind,
        access,
        repos,
        installedAt: prev?.installedAt || now(),
        installedBy: prev?.installedBy || installedBy,
        /* Read until the first draft PR asks for write; a reconfigure keeps it. */
        githubAccess: prev?.githubAccess || "read",
        ...(prev?.writeGrantedAt && { writeGrantedAt: prev.writeGrantedAt }),
      },
    ]),
    githubRequests: state.githubRequests.filter((r) => r.account !== account),
    githubRemoved: state.githubRemoved.filter((r) => r.account !== account),
    ...(first && {
      connectedAgents: SAMPLE_CONNECTED_AGENTS,
      prs: samplePrs(),
    }),
  });
};
export const setGithubRepos = ({ account, access, repos }) =>
  installGithub({ account, access, repos });

/** Uninstalled here, or removed on GitHub (the `installation.deleted` webhook). */
export const uninstallGithub = (account, { onGithub = false } = {}) => {
  const left = githubInstalls().filter((i) => i.account !== account);
  set({
    ...withInstalls(left),
    ...(onGithub && {
      githubRemoved: [
        { account, at: now() },
        ...state.githubRemoved.filter((r) => r.account !== account),
      ],
    }),
    /* The sample rows belong to the install; the last one going takes them. */
    ...(left.length === 0 && { connectedAgents: [], prs: [] }),
  });
};
export const dismissGithubRemoved = (account) =>
  set({
    githubRemoved: state.githubRemoved.filter((r) => r.account !== account),
  });

/** Repositories this workspace can see through the install. */
/** Repositories an account owns — an install only ever sees its own account's. */
export const reposOfAccount = (account) =>
  GITHUB_REPOS.filter((r) => r.name.startsWith(`${account}/`));

export const reposOfInstall = (i) => {
  const own = reposOfAccount(i.account);
  return i.access === "all"
    ? own
    : own.filter((r) => (i.repos || []).includes(r.name));
};
/** The source a scanned agent connects with — the same shape the Connect agent picker produces. */
export const scannedAgentSource = (repo, agent, extra = {}) => ({
  kind: "repo",
  value: repoUrlOf(repo.name),
  ref: "main",
  entry: agent.entry,
  githubApp: true,
  framework: agent.framework,
  name: agent.name,
  ...extra,
});

/** A repository's file tree was read for agents — shared by the install dialog and the page. */
export const markScanned = (name) =>
  set({ scans: { ...state.scans, [name]: now() } });

/** Every repository the workspace can see, across all its installations. */
export const installedRepos = (s = state) =>
  githubInstalls(s).flatMap(reposOfInstall);

/** The installation that covers a repository, or null. */
export const installationOfRepo = (name, s = state) => {
  const n = String(name || "")
    .replace(/^(https?:\/\/)?(www\.)?github\.com\//i, "")
    .replace(/\.git$|\/+$/g, "")
    .toLowerCase();
  return n
    ? githubInstalls(s).find((i) =>
        reposOfInstall(i).some((r) => r.name.toLowerCase() === n),
      ) || null
    : null;
};
/** "read" | "write" for a repository the app can see; null when it can't. */
export const githubAccessOf = (name, s = state) => {
  const inst = installationOfRepo(name, s);
  return inst ? inst.githubAccess || "read" : null;
};
/** Can we open a draft PR on this repository without asking first? */
export const canWriteTo = (name, s = state) =>
  githubAccessOf(name, s) === "write";
/** One line for an agent's Connections tab. */
export const githubAccessLabel = (access) =>
  access === "write"
    ? "Read and write (draft PRs)"
    : "Read only. Write is asked at the first draft PR";
/**
  The person granted write on GitHub at their first draft PR. GitHub
  permissions belong to the installation, so every repository it covers can
  take draft PRs from now on.
*/
export const grantGithubWrite = (name) => {
  const inst = installationOfRepo(name);
  if (!inst) return;
  set({
    ...withInstalls(
      githubInstalls().map((i) =>
        i.id === inst.id
          ? { ...i, githubAccess: "write", writeGrantedAt: now() }
          : i,
      ),
    ),
  });
};

/* ── pull requests ────────────────────────────────────────────────────────── */

/**
 * A PR we opened. Status moves open → merged | closed. `check` is the
 * simulation result we post on it — the reviewer sees the evidence where
 * they review, not only in our product.
 */
/*
  The simulation check in a line: "12 scenarios · 40% → 85%". The before →
  after part shows only when both rates are known — a check opened before its
  scores are in says how many scenarios it runs and no more.
*/
export const prCheckSummary = (check) => {
  if (!check) return "";
  const rates =
    typeof check.before === "number" && typeof check.after === "number"
      ? `${check.before}% → ${check.after}%`
      : "";
  return [check.scenarios ? `${check.scenarios} scenarios` : "", rates]
    .filter(Boolean)
    .join(" · ");
};

/* What the PR is, in a word, for Slack and the cards. */
export const prNoun = (pr) =>
  `${pr?.kind === "telemetry" ? "Tracing" : "Fix"} ${reviewNounOf(pr?.host).short}`;

export const openPr = (pr) => {
  const number = 140 + state.prs.length + 1;
  const record = {
    id: uid("pr"),
    number,
    status: "open",
    openedAt: now(),
    ...pr,
  };
  set({ prs: [record, ...state.prs] });
  postSlack({
    event: "pr",
    title: `${prNoun(pr)} opened · ${pr.repo} ${reviewNounOf(pr.host).ref}${number}`,
    lines: [
      pr.title,
      `${pr.files.length} file${pr.files.length === 1 ? "" : "s"} · ${prCheckSummary(pr.check) ? `simulation ${prCheckSummary(pr.check)}` : "simulation pending"}`,
    ],
    actions: [`View ${reviewNounOf(pr.host).long}`, "Open run"],
    links: pr.envId
      ? {
          "Open run": pr.fromRunId
            ? `/dashboard/simulate/environments/${pr.envId}/runs/${pr.fromRunId}`
            : `/dashboard/simulate/environments/${pr.envId}/runs`,
        }
      : {},
  });
  return record;
};
/*
  A draft PR the demo seed opens, once per `seedId`: found again on later
  loads, and not reopened after it's been removed. Null when there's no
  browser to keep it in.
*/
export const seedDemoPr = ({ seedId, ...pr }) => {
  if (typeof window === "undefined") return null;
  const have = state.prs.find((p) => p.seedId === seedId);
  if (have) return have;
  if (state.demoShares?.[`pr:${seedId}`]) return null;
  const record = {
    id: uid("pr"),
    number: 140 + state.prs.length + 1,
    status: "open",
    openedAt: now(),
    seedId,
    ...pr,
  };
  set({
    prs: [record, ...state.prs],
    demoShares: { ...(state.demoShares || {}), [`pr:${seedId}`]: true },
  });
  return record;
};
export const updatePr = (id, patch) =>
  set({ prs: state.prs.map((p) => (p.id === id ? { ...p, ...patch } : p)) });

/*
  A new commit on the PR's branch: the head moves and the verify check is
  queued again on that exact sha. `checks` is the PR's whole check history,
  newest last — prChecks.js builds it, this only stores it.
*/
export const pushToPr = (id, { head, checks }) =>
  updatePr(id, { head, checks });
/** Patch one run in a PR's check history. */
export const setPrCheck = (id, runId, patch) => {
  const pr = state.prs.find((p) => p.id === id);
  if (!pr?.checks) return;
  updatePr(id, {
    checks: pr.checks.map((c) => (c.id === runId ? { ...c, ...patch } : c)),
  });
};

/* What kind of file a proposal touches — prompt text or the agent's code. */
const CODE_KINDS = new Set(["Tool description", "Memory", "Architecture"]);

/** A proposal as a file in the agent's repository. */
export const prFilesOf = (env, proposals, fileFor) =>
  proposals
    .map((p) => {
      const f = fileFor(env, p);
      if (!f) return null;
      return {
        path: f.path.replace(new RegExp(`^${env?.id}/`), ""),
        kind: CODE_KINDS.has(p.kind) ? "code" : "prompt",
        title: p.title,
        adds: (p.diff || []).filter((d) => d.type === "add").length,
        dels: (p.diff || []).filter((d) => d.type !== "add").length,
      };
    })
    .filter(Boolean);

/* ── Slack ────────────────────────────────────────────────────────────────── */

export const SLACK_WORKSPACES = ["Acme Inc", "Acme Labs"];
/* Workspaces whose admins approve every new app first. */
export const SLACK_NEEDS_APPROVAL = ["Acme Labs"];
export const SLACK_CHANNELS = [
  "#agent-quality",
  "#eng-alerts",
  "#support-ai",
  "#releases",
  "#general",
];

export const SLACK_EVENTS = [
  {
    id: "run_finished",
    label: "Simulation finished",
    blurb: "Pass rate, failures and a link to the run.",
    on: true,
  },
  {
    id: "regression",
    label: "Regression against the previous version",
    blurb: "A full run scores lower than the version before it.",
    on: true,
  },
  {
    id: "release_blocker",
    label: "Must-pass scenario failing",
    blurb: "A scenario marked must-pass fails.",
    on: true,
  },
  {
    id: "pr",
    label: "Fix PR opened or merged",
    blurb: "Every pull request Future AGI opens, and when it merges.",
    on: true,
  },
  {
    id: "version",
    label: "New agent version",
    blurb: "A push or a sync saved a new version of an agent.",
    on: true,
  },
  {
    id: "error_feed",
    label: "New Error Feed issue or spike",
    blurb: "Production failures grouping into a new issue.",
    on: false,
  },
  {
    id: "alert",
    label: "Alert rule triggered",
    blurb: "Any alert rule that has Slack as a destination.",
    on: false,
  },
];

export const SLACK_PERMISSIONS = [
  "Post messages to channels Future AGI is added to",
  "Add buttons to messages that open the run, issue or pull request",
  "Read the channel list so you can pick where things go",
];

export const installSlack = ({ workspace, defaultChannel }) => {
  /* Removed in Slack, then added back to the same workspace: its routing
     comes back — events stay on or off, custom channels stay, and what went
     to the old default goes to the one just picked. `restoredFrom` lets the
     channel step (a second call in the same install) re-apply it. */
  const before =
    (state.slackRemoved?.workspace === workspace && state.slackRemoved.rules
      ? state.slackRemoved
      : null) ||
    (state.slack?.workspace === workspace ? state.slack.restoredFrom : null) ||
    null;
  const rules = before
    ? Object.fromEntries(
        SLACK_EVENTS.map((e) => {
          const to =
            e.id in before.rules
              ? before.rules[e.id]
              : e.on
                ? before.defaultChannel
                : null;
          return [
            e.id,
            to && to === before.defaultChannel ? defaultChannel : to,
          ];
        }),
      )
    : Object.fromEntries(
        SLACK_EVENTS.map((e) => [e.id, e.on ? defaultChannel : null]),
      );
  set({
    slackRequest: null,
    slackRemoved: null,
    slack: {
      workspace,
      defaultChannel,
      installedAt: now(),
      installedBy: "you",
      rules,
      ...(before && {
        restoredFrom: {
          rules: before.rules,
          defaultChannel: before.defaultChannel,
        },
      }),
    },
  });
};
export const updateSlack = (patch) =>
  set({ slack: { ...state.slack, ...patch } });
/** Disconnected here, or the app was removed in Slack (`app_uninstalled`). */
export const uninstallSlack = ({ inSlack = false } = {}) =>
  set({
    slack: null,
    /* Removed in Slack's side: its routing is kept for when it's added back. */
    ...(inSlack &&
      state.slack && {
        slackRemoved: {
          workspace: state.slack.workspace,
          at: now(),
          rules: state.slack.rules || {},
          defaultChannel: state.slack.defaultChannel,
        },
      }),
  });
/** The workspace needs an admin's OK before the app can be added. */
export const requestSlack = (workspace) =>
  set({ slackRequest: { workspace, status: "pending", requestedAt: now() } });
/** Slack tells the requester when an admin approves; they finish adding it. */
export const approveSlackRequest = () =>
  set({
    slackRequest: state.slackRequest && {
      ...state.slackRequest,
      status: "approved",
      approvedAt: now(),
    },
  });
export const cancelSlackRequest = () => set({ slackRequest: null });

/** Post to whichever channel the workspace routes this event to. No-op without Slack, or with the event turned off. */
/* `links` maps an action's label to the in-app route it opens — a message's
   buttons lead back to the run, issue or agent it's about. */
export const postSlack = ({
  event,
  title,
  lines = [],
  actions = [],
  links = {},
  channel,
}) => {
  const slack = state.slack;
  if (!slack) return null;
  /* An event added after Slack was installed follows its default. */
  const rules = slack.rules || {};
  const to =
    channel ||
    (event in rules
      ? rules[event]
      : SLACK_EVENTS.find((e) => e.id === event)?.on
        ? slack.defaultChannel
        : null);
  if (!to) return null;
  /* Only buttons that lead somewhere go out. A button with nothing behind
     it isn't posted. */
  const live = actions.filter((a) => links[a]);
  const msg = {
    id: uid("msg"),
    event,
    channel: to,
    title,
    lines,
    actions: live,
    links,
    at: now(),
  };
  set({ slackLog: [msg, ...state.slackLog].slice(0, 40) });
  return msg;
};

/* ── tracing (traceAI) ────────────────────────────────────────────────────── */

export const setTracing = (key, value) =>
  set({ tracing: { ...state.tracing, [key]: value } });

/* ── sync on push ─────────────────────────────────────────────────────────── */

export const setSyncOnPush = (key, on) =>
  set({ syncOnPush: { ...state.syncOnPush, [key]: on } });

/* ── Coding agents (bring your own improver) ─────────────────────────────── */

/*
  The customer's own coding agent, connected through our Claude Code plugin
  or our MCP server. It can read an issue's brief and submit candidates; it
  can never grade them, edit scenarios or evaluations, or create a version
  that activates itself — Future AGI runs every candidate and computes the
  verdict.

  Who connects what:
  - An editor agent (Claude Code, Codex, Cursor) runs on one developer's
    machine, in their checkout — so it's connected per person. "Send to …"
    follows the person looking at the page; a teammate without one sees Copy.
  - A cloud agent (Claude Code on the web, Codex cloud, Cursor background
    agents, Devin) works in its own sandbox against the agent's repository —
    an account the workspace connects once. Its key is stored only as a
    secret-manager reference. "Dispatch to …" hands it a brief in one click.
  - Every connection gets a scoped improver token. Coding agents are always
    allowed; the token's scope is what limits them.
*/
export const CODING_AGENTS = [
  {
    id: "claude_code",
    label: "Claude Code",
    vendor: "Anthropic",
    ways: ["plugin", "mcp"],
  },
  { id: "codex", label: "Codex", vendor: "OpenAI", ways: ["mcp"] },
  { id: "cursor", label: "Cursor", vendor: "Anysphere", ways: ["mcp"] },
];

/*
  Cloud coding agents — dispatched with one click from an issue, no setup on
  the customer's machine. They work in their own sandbox against the agent's
  repository and submit through the same gate as everyone else.
*/
export const CLOUD_CODING_AGENTS = [
  {
    id: "claude_code_web",
    label: "Claude Code on the web",
    vendor: "Anthropic",
    cloud: true,
  },
  { id: "codex_cloud", label: "Codex cloud", vendor: "OpenAI", cloud: true },
  {
    id: "cursor_background",
    label: "Cursor background agents",
    short: "Cursor",
    vendor: "Anysphere",
    cloud: true,
  },
  { id: "devin", label: "Devin", vendor: "Cognition", cloud: true },
];
/** The vendor key's default slot in the secret manager — never the key itself. */
export const cloudKeySlotOf = (id) =>
  `vault://acme/coding-agents/${id}_api_key`;
export const codingAgentById = (id) =>
  [...CODING_AGENTS, ...CLOUD_CODING_AGENTS].find((a) => a.id === id) || null;

/* The three tools an improver connection gets — and nothing else. */
export const IMPROVER_TOOLS = [
  {
    name: "get_brief",
    desc: "The issue, root cause, evidence, visible failing scenarios and constraints",
  },
  {
    name: "submit_candidate",
    desc: "A pushed commit or archive for code, a settings patch for a hosted agent, a draft version for one built in Future AGI, tested on a copy, never the live agent",
  },
  {
    name: "get_verdict",
    desc: "Whether passes are improving against today's agent and its last attempt, per-scenario results on visible scenarios, pass or fail on must-pass, and the constraint checks",
  },
];

/*
  What an improver token may and may not do. Connecting a coding agent
  issues one of these instead of handing it a workspace key — so held-back
  scenarios stay hidden from it, not just frozen.
*/
export const IMPROVER_SCOPES = [
  {
    id: "briefs:read",
    label: "Read briefs",
    desc: "The issue, root cause, evidence and constraints",
    allowed: true,
  },
  {
    id: "scenarios:visible:read",
    label: "Read visible scenarios",
    desc: "The failing scenarios a brief names",
    allowed: true,
  },
  {
    id: "candidates:submit",
    label: "Submit candidates",
    desc: "A pushed commit, an archive or a local SDK run",
    allowed: true,
  },
  {
    id: "verdicts:read",
    label: "Read verdicts",
    desc: "Its own candidates' results, as Future AGI computed them",
    allowed: true,
  },
  {
    id: "scenarios:held_back:read",
    label: "Read held-back scenarios",
    desc: "Hidden, so it can't tune to them",
    allowed: false,
  },
  {
    id: "evaluations:read",
    label: "Read evaluation configs",
    desc: "Judges, rubrics and thresholds stay internal",
    allowed: false,
  },
  {
    id: "versions:write",
    label: "Create or activate versions",
    desc: "A passing candidate becomes a version someone on your team switches to",
    allowed: false,
  },
  {
    id: "workspace:admin",
    label: "Change workspace settings",
    desc: "Integrations, keys, members and budgets",
    allowed: false,
  },
];

const tokenId = () =>
  `fagi_imp_${Array.from({ length: 24 }, () => Math.floor(Math.random() * 16).toString(16)).join("")}`;
const scopedToken = () => ({
  id: tokenId(),
  scopes: IMPROVER_SCOPES.filter((s) => s.allowed).map((s) => s.id),
  createdAt: now(),
});

/* The person looking — the prototype has one. */
export const ME = "you";

/* Connecting issues the agent its scoped token on first sign-in — for you. */
export const connectCodingAgent = (id, via) =>
  set({
    codingAgents: {
      ...state.codingAgents,
      [id]: {
        via,
        connectedAt: now(),
        connectedBy: "you",
        userId: ME,
        token: scopedToken(),
      },
    },
    ...withSampleHandoffs(id),
  });

/*
  A cloud agent: connected once for the workspace. The vendor's API key is
  recorded as a secret-manager reference only; dispatches carry a scoped
  improver token of their own, so held-back scenarios stay hidden.
*/
export const connectCloudAgent = (id, { keyRef, repos = "agent" }) =>
  set({
    cloudAgents: {
      ...(state.cloudAgents || {}),
      [id]: {
        keyRef,
        repos,
        connectedAt: now(),
        connectedBy: ME,
        token: scopedToken(),
      },
    },
    ...withSampleHandoffs(id, { dispatch: true }),
  });
export const disconnectCloudAgent = (id) => {
  const { [id]: _gone, ...rest } = state.cloudAgents || {};
  set({ cloudAgents: rest });
};
/** The cloud agents this workspace connected. */
export const connectedCloudAgents = (s = state) =>
  CLOUD_CODING_AGENTS.filter((a) => s.cloudAgents?.[a.id]).map((a) => ({
    ...a,
    ...s.cloudAgents[a.id],
  }));

/** A new token for a connected agent; the old one stops working at once. Also upgrades a connection made with a workspace key. */
export const rotateImproverToken = (id) => {
  const c = state.codingAgents?.[id];
  if (!c) return;
  set({
    codingAgents: {
      ...state.codingAgents,
      [id]: { ...c, token: scopedToken() },
    },
  });
};
/** "fagi_imp_…7c2e" — never the whole token. */
export const improverTokenLabel = (token) =>
  token?.id ? `fagi_imp_…${token.id.slice(-4)}` : null;
/**
  Whether held-back scenarios are hidden from this agent: true for a
  connection on a scoped token (and for cloud agents, which are dispatched
  with one), false for an older connection still on a workspace key.
*/
export const heldBackHiddenFrom = (agentId, s = state) => {
  if (codingAgentById(agentId)?.cloud) return true;
  const scopes = s.codingAgents?.[agentId]?.token?.scopes || [];
  return scopes.length > 0 && !scopes.includes("scenarios:held_back:read");
};
export const disconnectCodingAgent = (id) => {
  const { [id]: _gone, ...rest } = state.codingAgents || {};
  set({ codingAgents: rest });
};
/* Yours — a connection saved before connections were per person counts as yours. */
const isMine = (c) => !c?.userId || c.userId === ME;
/** Your editor agents — the ones "Send to …" can use for you. */
export const myCodingAgents = (s = state) =>
  CODING_AGENTS.filter(
    (a) => s.codingAgents?.[a.id] && isMine(s.codingAgents[a.id]),
  ).map((a) => ({ ...a, ...s.codingAgents[a.id] }));
/* The coding agent "Send to …" hands to: your first one. */
export const connectedCodingAgent = (s = state) => myCodingAgents(s)[0] || null;

/*
  A coding agent working on one improvement — started by Send to Claude Code
  or by a pasted brief's first submission. Its attempts show on the
  improvement page as trials, counted against the improvement's budget.
*/
export const startImproverSession = ({
  agentId,
  envId,
  optId,
  where = "future-agi",
}) => {
  const existing = (state.improverSessions || []).find(
    (x) => x.optId === optId && x.envId === envId,
  );
  if (existing) return existing.id;
  const id = uid("imp");
  set({
    improverSessions: [
      ...(state.improverSessions || []),
      { id, agentId, envId, optId, where, startedAt: now() },
    ],
  });
  return id;
};
export const improverSessionFor = (envId, optId, s = state) =>
  (s.improverSessions || []).find(
    (x) => x.envId === envId && x.optId === optId,
  ) || null;

/*
  Send to Claude Code: the brief is parked for your agent, which pulls it
  with get_brief the next time you ask it to. Kept short — the settings page lists the latest.
*/
export const handBriefTo = ({
  agentId,
  target = null,
  brief = "",
  dispatch = false,
}) => {
  const id = uid("brief");
  const title =
    (
      String(brief)
        .split("\n")
        .find((l) => l.startsWith("# ")) || ""
    )
      .slice(2)
      .trim() || "Brief";
  set({
    briefHandoffs: [
      {
        id,
        agentId,
        target,
        title,
        brief,
        at: now(),
        ...(dispatch && { dispatch: true }),
      },
      ...(state.briefHandoffs || []),
    ].slice(0, 20),
  });
  return id;
};

/*
  One-click dispatch to a cloud agent: the brief goes to the vendor's API
  with the agent's repository and a scoped improver token. The agent works
  in its own sandbox and comes back through submit_candidate like any other
  improver — Future AGI runs the candidate and computes the verdict. An
  improvement target also starts an improver session, so its attempts land
  there as trials.
*/
export const dispatchToCloudAgent = ({
  agentId,
  target = null,
  brief = "",
}) => {
  const id = handBriefTo({ agentId, target, brief, dispatch: true });
  if (target?.kind === "improvement") {
    startImproverSession({
      agentId,
      envId: target.envId,
      optId: target.optId,
      where: "future-agi",
    });
  }
  return id;
};

/* ── sample rows (no backend yet) ─────────────────────────────────────────── */

const hoursAgo = (h) => new Date(Date.now() - h * 3600e3).toISOString();

/* Agents connected from the sample repositories, with the Observe project their traces land in. */
const SAMPLE_CONNECTED_AGENTS = [
  {
    id: "agt_returns",
    name: "Returns & refunds agent",
    repo: "acme-inc/support-voice-agent",
    ref: "main",
    entry: "agents/returns/agent.py",
    framework: "LiveKit Agents",
    projects: ["returns-line"],
    versionCount: 4,
    usedIn: [{ envName: "Customer Support Line" }],
    connectedAt: hoursAgo(72),
  },
  {
    id: "agt_billing",
    name: "Billing copilot",
    repo: "acme-inc/billing-copilot",
    ref: "main",
    entry: "app/graph.py:billing",
    framework: "LangGraph",
    projects: [],
    versionCount: 2,
    usedIn: [],
    connectedAt: hoursAgo(30),
  },
];

/* One open and one merged fix PR, opened on the sample repositories. */
const samplePrs = () => [
  {
    id: "pr_sample_open",
    number: 142,
    status: "open",
    host: "github",
    kind: "fix",
    repo: "acme-inc/support-voice-agent",
    branch: "future-agi/escalate-refunds-over-200",
    title: "Escalate refunds above $200 before issuing them",
    agentName: "Returns & refunds agent",
    files: [
      {
        path: "prompts/returns_system.md",
        kind: "prompt",
        title: "Ask a supervisor before refunds above $200",
        adds: 6,
        dels: 1,
      },
      {
        path: "agents/returns/tools.py",
        kind: "code",
        title: "Block issue_refund above the limit without approval",
        adds: 9,
        dels: 2,
      },
    ],
    check: { scenarios: 12, before: 84, after: 97 },
    openedAt: hoursAgo(5),
  },
  {
    id: "pr_sample_merged",
    number: 139,
    status: "merged",
    host: "github",
    kind: "fix",
    repo: "acme-inc/billing-copilot",
    branch: "future-agi/confirm-dispute-reason",
    title: "Confirm the dispute reason before opening a case",
    agentName: "Billing copilot",
    files: [
      {
        path: "prompts/disputes.md",
        kind: "prompt",
        title: "Read the reason back to the caller",
        adds: 4,
        dels: 0,
      },
    ],
    check: { scenarios: 8, before: 71, after: 92 },
    openedAt: hoursAgo(50),
    mergedAt: hoursAgo(26),
  },
];

/* Briefs handed to coding agents, shown under Recent hand-offs. */
const withSampleHandoffs = (agentId, { dispatch = false } = {}) =>
  (state.briefHandoffs || []).length
    ? {}
    : {
        briefHandoffs: [
          {
            id: "brief_sample_1",
            agentId,
            target: null,
            title: "Refunds above $200 issued without a supervisor",
            brief: "",
            at: hoursAgo(2),
            ...(dispatch && { dispatch: true }),
          },
          {
            id: "brief_sample_2",
            agentId,
            target: null,
            title: "Agent promised a delivery date it couldn't confirm",
            brief: "",
            at: hoursAgo(20),
            ...(dispatch && { dispatch: true }),
          },
        ],
      };

/* Messages Future AGI would have posted, for the Slack page's Recent messages. */
const SAMPLE_SLACK_LOG = [
  {
    id: "msg_sample_run",
    event: "run_finished",
    channel: "#agent-quality",
    at: hoursAgo(1),
    title: "Customer Support Line · run 4 finished",
    lines: [
      "64 of 68 scenarios passed (94%) · 3 failed · 1 not measured",
      "Agent v4 · environment v1",
    ],
    actions: ["View run"],
    links: {},
  },
  {
    id: "msg_sample_regression",
    event: "regression",
    channel: "#agent-quality",
    at: hoursAgo(9),
    title: "Regression · Returns & refunds agent v4 is down 12 pts",
    lines: [
      "Refund scenarios dropped from 96% to 84% against v3 in Customer Support Line",
      "7 scenarios that passed on v3 now fail",
    ],
    actions: ["Compare runs", "View failures"],
    links: {},
  },
  {
    id: "msg_sample_pr",
    event: "pr",
    channel: "#agent-quality",
    at: hoursAgo(5),
    title: "Fix PR opened · acme-inc/support-voice-agent #142",
    lines: [
      "Escalate refunds above $200 before issuing them",
      "2 files · simulation 84% → 97%",
    ],
    actions: ["View pull request", "Open run"],
    links: {},
  },
];
const withSampleLog = (s) =>
  s.slackLog?.length ? s : { ...s, slackLog: SAMPLE_SLACK_LOG };
state = withSampleLog(state);

/** Read a different organization's mock connections. `silent` is for a render that is already reading. */
export const setIntegrationsScope = (id, { silent = false } = {}) => {
  const next = id || null;
  if (next === scope) return;
  scope = next;
  state = withSampleLog(typeof window === "undefined" ? EMPTY : load());
  if (!silent) listeners.forEach((l) => l());
};

/* ── Slack routing (mock until the backend stores it) ─────────────────────── */

/** Where each event goes: an explicit choice, else the default channel when the event is on by default. */
export const slackRulesOf = (
  s = state,
  defaultChannel = s.slackDefaultChannel,
) =>
  Object.fromEntries(
    SLACK_EVENTS.map((e) => [
      e.id,
      e.id in (s.slackRules || {})
        ? s.slackRules[e.id]
        : e.on
          ? defaultChannel || null
          : null,
    ]),
  );
export const setSlackRule = (event, channel) =>
  set({ slackRules: { ...(state.slackRules || {}), [event]: channel } });
export const setSlackDefaultChannel = (channel) =>
  set({ slackDefaultChannel: channel });
/** After a disconnect, adding Slack again starts from one default channel. */
export const resetSlackRouting = () =>
  set({ slackDefaultChannel: null, slackRules: {} });

/** Tests only: back to the empty start. */
export const resetIntegrations = () =>
  set({ ...EMPTY, slackLog: SAMPLE_SLACK_LOG });
