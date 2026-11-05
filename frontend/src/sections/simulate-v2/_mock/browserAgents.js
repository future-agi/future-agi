/*
  Browser agents under test (plan connect.md section 8, D12, T36): the user's
  own agent code drives a browser we start in the sandbox. Each environment
  sets which sites that browser may open; anything else is blocked and
  reported in the trial. Simulated websites come later.

  What a repository's browser agent reads as is mocked here, steady per repo.
*/

export const BROWSER_FRAMEWORKS = {
  browser_use: { label: "browser-use", entry: "agent.py" },
  playwright: { label: "Playwright", entry: "agent/run.ts" },
  stagehand: { label: "Stagehand", entry: "src/agent.ts" },
  claude_cu: { label: "Claude computer use", entry: "computer_use.py" },
  openai_cua: { label: "OpenAI computer use", entry: "cua_agent.py" },
};

/* The key each model needs, by provider. */
export const MODEL_KEYS = {
  anthropic: { name: "ANTHROPIC_API_KEY", label: "Anthropic API key" },
  openai: { name: "OPENAI_API_KEY", label: "OpenAI API key" },
};

export const VIEWPORTS = [
  { id: "1280x800", label: "Desktop · 1280 × 800" },
  { id: "1920x1080", label: "Desktop HD · 1920 × 1080" },
  { id: "390x844", label: "Mobile · 390 × 844" },
];

const hash = (s) =>
  [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

const PICKS = [
  ["browser_use", "claude-sonnet-5-5", "anthropic"],
  ["playwright", "gpt-5", "openai"],
  ["stagehand", "claude-sonnet-5-5", "anthropic"],
  ["claude_cu", "claude-sonnet-5-5", "anthropic"],
  ["openai_cua", "computer-use-preview", "openai"],
];

/**
 * What we found in a repository's browser agent:
 * { framework, entry, model, provider, startUrl }. Null without a source.
 */
export function browserSetupOf(src) {
  if (!src) return null;
  const h = hash(src);
  const [framework, model, provider] = PICKS[h % PICKS.length];
  return {
    framework,
    entry: BROWSER_FRAMEWORKS[framework].entry,
    model,
    provider,
    startUrl: h % 3 === 0 ? null : "https://app.acme-admin.com/login",
  };
}

/* ── the sites an environment's browser may open ─────────────────────────── */

const DEFAULT_SITES = {
  "env-browser": ["play.2048.io", "app.taskly.dev"],
};

/** "app.acme-admin.com" — a bare host, with an optional *. for subdomains. */
export const isSite = (s) =>
  /^(\*\.)?([a-z0-9-]+\.)+[a-z]{2,}$/i.test(String(s || "").trim());

/** A typed URL or host → its host, lower case. */
export const siteOf = (s) =>
  String(s || "")
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "")
    .toLowerCase();

/** The sites an environment's browser may open. */
export const allowedSitesOf = (env, envState) =>
  envState?.allowedSites ?? DEFAULT_SITES[env?.id] ?? [];

const BLOCKED = [
  { site: "accounts.google.com", why: "a sign-in page" },
  { site: "cdn.ads-tracker.net", why: "an ad script" },
  { site: "www.reddit.com", why: "a search result link" },
];

/**
 * The sites a trial's browser tried to open and was refused, by the step they
 * followed: [{ after, site, why }]. About one trial in three has one.
 */
export const blockedAttemptsOf = (task, sites = []) => {
  const steps = task?.steps || [];
  if (steps.length < 3) return [];
  const h = hash(`${task?.scenarioId || task?.id}:blocked`);
  if (h % 3 !== 0) return [];
  const b = BLOCKED[(h >>> 3) % BLOCKED.length];
  if (sites.includes(b.site)) return [];
  return [{ after: 1 + ((h >>> 5) % (steps.length - 2)), ...b }];
};
