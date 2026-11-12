/**
 * @module @omega/hub/schema
 *
 * The constants of the omega-hub/v1 entry format: enums, patterns and limits. The JSON Schema in
 * `schema/omega.hub.schema.json` states the same shape for editors; the validator in this package is
 * the judge, and `test/schema.test.mjs` keeps the two in step on the fixtures.
 */

export const SCHEMA = "omega-hub/v1";
export const CATALOG_SCHEMA = "omega-hub-catalog/v1";
export const LOCK_SCHEMA = "omega-hub-lock/v1";
export const MANIFEST_FILE = "omega.hub.json";
export const README_FILE = "README.md";
export const ENV_EXAMPLE_FILE = ".env.example";
export const HUB_DIR = "hub";
export const REPO = "future-agi/omega";
export const MAINTAINER_ACCOUNT = "future-agi";
export const PLACEHOLDER_AUTHOR = "your-github-handle";

export const KINDS = ["app", "agent", "team", "skill", "plugin", "mcp", "workflow"];
export const COLORS = ["red", "yellow", "green", "blue", "indigo", "purple", "pink", "gray"];
export const DIFFICULTIES = ["starter", "intermediate", "advanced"];
export const LICENSES = ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "CC0-1.0", "Unlicense"];
export const KEYLESS = ["scripted", "recorded", "echo", "none"];
export const AUTHOR_KINDS = ["maintainer", "community"];
export const KEY_PURPOSES = ["live", "service"];
export const INSTALL_KINDS = ["plugin", "skill", "mcp"];

export const ID = /^[a-z][a-z0-9-]{2,39}$/;
export const TAG = /^[a-z0-9][a-z0-9-]{1,23}$/;
export const GITHUB = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
export const KEY_NAME = /^[A-Z][A-Z0-9_]{1,63}$/;
export const MODEL = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._@/:-]{0,99}$/;
export const DATE = /^\d{4}-\d{2}-\d{2}$/;
export const NODE_RANGE = /^>=\d+(?:\.\d+){0,2}$/;
export const BIN = /^[a-z0-9][a-z0-9._-]{0,39}$/;

/**
 * Lengths: `[min, max]` characters.
 * @type {Record<"title" | "tagline" | "authorName" | "keyWhy" | "caption" | "featuredWhy", [number, number]>}
 */
export const LENGTHS = {
  title: [4, 60],
  tagline: [20, 120],
  authorName: [1, 60],
  keyWhy: [10, 100],
  caption: [20, 140],
  featuredWhy: [20, 140]
};

/**
 * Counts: `[min, max]` items.
 * @type {Record<"tags" | "live" | "redact" | "maxCalls", [number, number]>}
 */
export const COUNTS = { tags: [1, 6], live: [0, 8], redact: [0, 8], maxCalls: [1, 200] };

/** Size limits of an entry folder (R3, R8, R9). Code files are `.ts`, `.mts`, `.mjs`, `.js`. */
export const LIMITS = {
  files: 60,
  totalBytes: 512 * 1024,
  textBytes: 64 * 1024,
  imageBytes: 256 * 1024,
  codeLines: 400,
  readmeBytes: 12 * 1024,
  castBytes: 64 * 1024,
  castLines: 400,
  castWidth: 100,
  castHeight: 40,
  idleSeconds: 1.0,
  testTimeoutMs: 180_000
};

export const CODE_EXTENSIONS = [".ts", ".mts", ".mjs", ".js", ".tsx"];
export const TEXT_EXTENSIONS = [...CODE_EXTENSIONS, ".json", ".md", ".txt", ".csv", ".html", ".css", ".svg", ".cast", ".yml", ".yaml", ".example", ".gitignore"];
export const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".svg"];

/** README H2 sections, in order (R8). `Run it with your key` is left out when `models.live` is empty. */
export const README_SECTIONS = ["What it does", "Run it with no key", "Run it with your key", "How it works", "Make it yours"];

/** The caption shown under a recording when `demo.caption` is absent, by `models.keyless`. */
export const DEFAULT_CAPTIONS = {
  scripted: "Scripted models play the agents; the runtime and the checks are real.",
  recorded: "Scripted models play the agents; the runtime and the checks are real.",
  echo: "The echo model repeats its input; everything else is real.",
  none: "No model runs here; every line is real output."
};

/**
 * Adoption and guarantee claims an entry may not make (ported from the gallery's positioning rules).
 * @type {[string, RegExp][]}
 */
export const BANNED_CLAIMS = [
  ["used by", /\bused\s+by\b/i],
  ["customers", /\bcustomers\b/i],
  ["production-ready", /\bproduction[\s-]ready\b/i],
  ["enterprise-grade", /\benterprise[\s-]grade\b/i],
  ["the only", /\bthe\s+only\b/i],
  ["guarantees", /\bguarantee[sd]?\b/i]
];

/** Pull request labels (P2-P5). */
export const LABELS = {
  onBehalf: "hub-on-behalf",
  featured: "hub-featured",
  moderation: "hub-moderation",
  maintainer: "hub-maintainer"
};

/** Every rule id with its short code, in the order the validator reports them. */
export const RULES = {
  R1: "shape",
  R2: "id-folder",
  R3: "files",
  R4: "env-example",
  R5: "self-contained",
  R6: "keys",
  R7: "prose",
  R8: "readme",
  R9: "cast",
  R10: "remix",
  R11: "source",
  R12: "dates",
  R13: "license",
  R14: "test-run",
  R15: "cast-fresh",
  P1: "one-entry-per-pr",
  P2: "author-is-submitter",
  P3: "featured-needs-label",
  P4: "hidden-needs-label",
  P5: "maintainer-needs-label"
};

/** Today's date, `YYYY-MM-DD`, in UTC. */
export function todayUtc(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

/** A real calendar date in `YYYY-MM-DD`. */
export function isCalendarDate(value) {
  if (typeof value !== "string" || !DATE.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** A plain object (not null, not an array). */
export function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** One finding: `{ rule, code, file, message, fix }`. */
export function finding(rule, file, message, fix) {
  return { rule, code: RULES[rule] ?? rule, file, message, fix: fix ?? "" };
}
