// Omega Hub constants (SPEC §2, §7). The rules texts (admission, content,
// checklist, counts, trending, "What review means") are NOT here: every
// surface renders them from GET /omega-hub/v1/rules (SPEC §6.8).

export const DISCORD_URL = "https://discord.com/invite/n2tCUKBkAw";

export const HUB_ROOT = "/dashboard/hub";

const seg = (value) => encodeURIComponent(String(value ?? ""));

export const hubPaths = {
  browse: HUB_ROOT,
  entry: (publisher, name, tab) =>
    `${HUB_ROOT}/${seg(publisher)}/${seg(name)}${tab && tab !== "card" ? `/${seg(tab)}` : ""}`,
  publishers: `${HUB_ROOT}/publishers`,
  publisher: (handle) => `${HUB_ROOT}/publishers/${seg(handle)}`,
  publish: `${HUB_ROOT}/publish`,
  submissions: `${HUB_ROOT}/submissions`,
  submission: (id) => `${HUB_ROOT}/submissions/${seg(id)}`,
  settings: `${HUB_ROOT}/settings`,
  verifyEmail: `${HUB_ROOT}/verify-email`,
  review: `${HUB_ROOT}/review`,
  reviewItem: (id) => `${HUB_ROOT}/review/${seg(id)}`,
  reviewReports: `${HUB_ROOT}/review/reports`,
  reviewPicks: `${HUB_ROOT}/review/picks`,
  reviewPublishers: `${HUB_ROOT}/review/publishers`,
  reviewBlocklist: `${HUB_ROOT}/review/blocklist`,
  reviewAudit: `${HUB_ROOT}/review/audit`,
};

// The 7 entry kinds (`KINDS` in @omega/hub), in browse order.
export const KINDS = [
  { value: "agent", label: "Agents" },
  { value: "team", label: "Teams" },
  { value: "app", label: "Apps" },
  { value: "workflow", label: "Workflows" },
  { value: "mcp", label: "MCP servers" },
  { value: "skill", label: "Skills" },
  { value: "plugin", label: "Plugins" },
];

export const KIND_LABEL = {
  agent: "Agent",
  team: "Team",
  app: "App",
  workflow: "Workflow",
  mcp: "MCP server",
  skill: "Skill",
  plugin: "Plugin",
};

// Trust tiers (SPEC §2.4). The tier is identity, not quality.
export const TIERS = {
  official: { label: "Official", short: "Official" },
  verified: { label: "Verified publisher", short: "Verified" },
  community: { label: "Community reviewed", short: "Community" },
};

export const TIER_TOOLTIP =
  "Every version on the Hub is checked automatically and approved by a Future AGI maintainer. Review is not a code audit: an entry runs with your permissions.";

export const SORTS = [
  { value: "trending", label: "Trending" },
  { value: "new", label: "New" },
  { value: "likes", label: "Most liked" },
  { value: "installs", label: "Most installed" },
];

// Report reasons (SPEC §6.6).
export const REPORT_REASONS = [
  { value: "security", label: "Security: malware, exfiltration or secrets" },
  { value: "licence", label: "Licence problem" },
  { value: "impersonation", label: "Impersonation" },
  { value: "broken", label: "Broken: it does not work" },
  { value: "spam", label: "Spam" },
  { value: "other", label: "Something else" },
];

// Moderation vocabularies (SPEC §3.3, §5.5).
export const REMOVAL_CATEGORIES = [
  "security issue",
  "licence issue",
  "breaks Hub rules",
  "publisher's request",
];
export const UNRANK_REASONS = [
  "rules change not yet met",
  "quality",
  "misleading listing",
  "under investigation",
  "other",
];
export const REPORT_ACTIONS = [
  { value: "dismiss", label: "Dismiss" },
  { value: "ask_publisher", label: "Ask the publisher" },
  { value: "unrank_entry", label: "Unrank the entry" },
  { value: "yank_version", label: "Yank a version" },
  { value: "remove_entry", label: "Remove the entry" },
  { value: "suspend_publisher", label: "Suspend the publisher" },
  { value: "restore", label: "Restore" },
];

// Keep confirmations aligned with services/moderation.py across entry and
// review screens. Yank reasons are public; suspension includes unlisted entries.
export const MODERATION_CONSEQUENCES = {
  remove_entry:
    "The entry is removed and its versions can no longer be downloaded.",
  yank_version:
    "This version is marked as yanked and excluded from latest and new installs by default. It remains available by its exact version. Your reason is shown publicly and emailed to the publisher.",
  suspend_publisher:
    "The publisher can no longer publish. All their entries are removed and all their publishing tokens are revoked.",
  unrank_entry:
    "The entry leaves browse lists but remains available by its exact reference.",
};

// community.resolve_report sends the note as the public yank reason or the
// ask-publisher email. Other resolutions retain it privately (moderation.py).
export const MODERATION_NOTE_FIELDS = {
  yank_version: {
    label: "Public reason",
    helperText:
      "Shown publicly on the entry page and on downloads of this version, and emailed to the publisher.",
  },
  ask_publisher: {
    label: "Message to the publisher",
    helperText: "Sent to the publisher by email",
  },
  private: {
    label: "Note (private)",
    helperText: "Not sent to the publisher or shown publicly",
  },
  unsuspend_publisher: {
    label: "Note (not saved)",
    helperText:
      "Required to unsuspend. This note is not saved or sent to the publisher.",
  },
};

// Upload limits (SPEC §4.2 step 1, §7.5).
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;
export const UPLOAD_EXTENSIONS = [".tar.gz", ".tgz", ".zip"];
export const NOTES_MAX = 500;
export const REASON_MIN = 20;
export const REASON_MAX = 2000;
export const CHANGE_NOTES_MAX = 30;
export const PICK_WHY = [20, 140];
export const HANDLE_RE = /^[a-z][a-z0-9-]{1,38}$/;
