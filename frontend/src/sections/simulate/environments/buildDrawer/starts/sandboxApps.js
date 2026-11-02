/*
  The apps a SaaS sandbox can stand in for, from Vel's twin catalogue. Each is
  answered by a seeded sandbox the agent reaches through the app's real SDK.
  `iconDark` is the mark for a dark theme where the colour mark is near black.
  Sample data: there is no sandbox API yet.
*/

const CATEGORIES = [
  { id: "productivity", label: "Productivity & docs" },
  { id: "comms", label: "Communication" },
  { id: "crm", label: "CRM & sales" },
  { id: "devtools", label: "Developer tools" },
  { id: "finance", label: "Finance & billing" },
  { id: "data", label: "Data & storage" },
];

export const SANDBOX_APPS = [
  {
    id: "notion",
    name: "Notion",
    category: "productivity",
    apiLevel: "api+ui",
    icon: "logos:notion-icon",
    iconDark: "simple-icons:notion",
    blurb:
      "Pages, databases, blocks. Reads and writes settle into a real state.",
  },
  {
    id: "google-docs",
    name: "Google Docs",
    category: "productivity",
    apiLevel: "api+ui",
    icon: "thesvg-color:google-docs",
    blurb:
      "Documents, comments, revision history. Suggestions and accept/reject flow.",
  },
  {
    id: "google-sheets",
    name: "Google Sheets",
    category: "productivity",
    apiLevel: "api+ui",
    icon: "thesvg-color:google-sheets",
    blurb:
      "Spreadsheets, formulas evaluate, batch updates preserve range semantics.",
  },
  {
    id: "google-drive",
    name: "Google Drive",
    category: "productivity",
    apiLevel: "api+ui",
    icon: "logos:google-drive",
    blurb:
      "Files, folders, sharing. Downloads and uploads land in a virtual bucket.",
  },
  {
    id: "slack",
    name: "Slack",
    category: "comms",
    apiLevel: "api+ui",
    icon: "logos:slack-icon",
    blurb:
      "Workspaces, channels, threads, DMs, users. Messages have delivery semantics.",
  },
  {
    id: "gmail",
    name: "Gmail",
    category: "comms",
    apiLevel: "api+ui",
    icon: "logos:google-gmail",
    blurb:
      "Inbox, threads, labels, drafts. Send/reply preserve threading correctly.",
  },
  {
    id: "discord",
    name: "Discord",
    category: "comms",
    apiLevel: "api+ui",
    icon: "logos:discord-icon",
    blurb: "Servers, channels, DMs, users. Bots can post and react.",
  },
  {
    id: "salesforce",
    name: "Salesforce",
    category: "crm",
    apiLevel: "api",
    icon: "logos:salesforce",
    blurb:
      "Accounts, contacts, opportunities. SOQL queries return the state you seeded.",
  },
  {
    id: "hubspot",
    name: "HubSpot",
    category: "crm",
    apiLevel: "api",
    icon: "logos:hubspot",
    iconDark: "simple-icons:hubspot",
    blurb:
      "Contacts, companies, deals, tickets. Property updates are stateful.",
  },
  {
    id: "linear",
    name: "Linear",
    category: "crm",
    apiLevel: "api+ui",
    icon: "simple-icons:linear",
    blurb:
      "Teams, projects, issues, cycles. Status transitions follow real workflow rules.",
  },
  {
    id: "github",
    name: "GitHub",
    category: "devtools",
    apiLevel: "api+ui",
    icon: "logos:github-icon",
    iconDark: "simple-icons:github",
    blurb:
      "Repositories, PRs, issues, files, actions. Branches and merges preserve history.",
  },
  {
    id: "jira",
    name: "Jira",
    category: "devtools",
    apiLevel: "api",
    icon: "logos:jira",
    blurb:
      "Projects, issues, workflows. JQL queries return the state your scenario seeded.",
  },
  {
    id: "stripe",
    name: "Stripe",
    category: "finance",
    apiLevel: "api",
    icon: "logos:stripe",
    iconDark: "simple-icons:stripe",
    blurb:
      "Customers, charges, subscriptions, refunds. Webhooks fire on state transitions.",
  },
  {
    id: "quickbooks",
    name: "QuickBooks",
    category: "finance",
    apiLevel: "api",
    icon: "thesvg-color:quickbooks",
    blurb:
      "Invoices, customers, transactions. Books balance after every write.",
  },
  {
    id: "dropbox",
    name: "Dropbox",
    category: "data",
    apiLevel: "api+ui",
    icon: "logos:dropbox",
    blurb:
      "Files, folders, sharing. Content-hash checks keep the state honest.",
  },
  {
    id: "box",
    name: "Box",
    category: "data",
    apiLevel: "api",
    icon: "logos:box",
    blurb: "Files, folders, collaborators, comments.",
  },
];

export const sandboxAppById = (id) => SANDBOX_APPS.find((a) => a.id === id);

/** The apps by category, in catalogue order. */
export const sandboxAppsByCategory = () =>
  CATEGORIES.map((c) => ({
    ...c,
    items: SANDBOX_APPS.filter((a) => a.category === c.id),
  })).filter((c) => c.items.length > 0);

/** What a sandbox is called when it isn't named. */
export function defaultSandboxName(ids) {
  if (ids.length === 1) {
    const app = sandboxAppById(ids[0]);
    return app ? `${app.name} sandbox` : "SaaS sandbox";
  }
  return `${ids.length}-app sandbox`;
}

/** The starting-state placeholder, tuned to the first app picked. */
export function seedPromptExample(ids) {
  const first = sandboxAppById(ids[0]);
  if (!first) return "Describe the state the sandbox should start with…";
  if (first.id === "slack") {
    return "Slack workspace with #support-urgent (3 overdue tickets), #general (5 recent messages), and a DM thread with a customer waiting for a refund status.";
  }
  if (first.id === "notion") {
    return "Notion workspace with a Roadmap database (three overdue launch tasks) and a Pricing QA page shared with the team.";
  }
  if (first.id === "gmail") {
    return "Inbox with 4 unread emails: two customer refund requests, one from legal, one meeting invite. Two labels: Support, Escalated.";
  }
  return `Describe the state ${first.name} should start with: what pages, channels, records, or messages should already be there when a run begins.`;
}
