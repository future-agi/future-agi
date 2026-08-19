/**
 * Environment configuration, instances and files.
 *
 * Modelled on HUD's environment settings, with two deliberate departures:
 *
 *  1. HUD states that compute, timeouts and session limits live in the SDK,
 *     "not on the environment". That pushes the numbers that decide what a run
 *     costs into code nobody reads before pressing Run. We surface them here as
 *     run defaults, so the pre-flight can quote a real estimate.
 *
 *  2. Environments are versioned. A result is only reproducible if you know
 *     which build of the world produced it, so every run pins a version and the
 *     version history is a first-class thing you can look at.
 */

export const DEFAULT_ENV_VARS = [
  { key: "OPENAI_API_KEY", value: "sk-proj-9f2c…a41b", secret: true, usedBy: "grader" },
  { key: "SEED_DATASET", value: "orders-v4", secret: false, usedBy: "environment" },
  { key: "LOG_LEVEL", value: "info", secret: false, usedBy: "environment" },
];

export const DEFAULT_BUILD_ARGS = [
  { key: "BASE_IMAGE", value: "python:3.12-slim" },
  { key: "INSTALL_DEV", value: "false" },
];

/** Runtime defaults every run inherits unless the run overrides them. */
export const DEFAULT_RUNTIME = {
  concurrency: 4,
  taskTimeoutS: 300,
  stepBudget: 60,
  retries: 1,
  isolation: "per_task",
  fileTracking: true,
  recordVideo: true,
  region: "us-east-1",
};

export const ISOLATION_OPTIONS = [
  {
    value: "per_task",
    label: "Fresh copy per task",
    desc: "Every task starts from the same snapshot. Slowest, and the only setting that makes results comparable.",
  },
  {
    value: "per_run",
    label: "Fresh copy per run",
    desc: "Tasks share one instance. Faster, but earlier tasks can contaminate later ones.",
  },
  {
    value: "persistent",
    label: "Persistent",
    desc: "State carries across runs. For debugging only — never for scoring.",
  },
];

/** Version history — what a run pins itself to. */
export const ENV_VERSIONS = [
  { version: "v7", createdAt: "2026-08-14T09:20:00Z", note: "Added 40 discontinued SKUs", current: true, runs: 12 },
  { version: "v6", createdAt: "2026-07-30T14:05:00Z", note: "Refund threshold rule tightened", current: false, runs: 48 },
  { version: "v5", createdAt: "2026-07-11T11:42:00Z", note: "Seeded 200 more customers", current: false, runs: 31 },
  { version: "v4", createdAt: "2026-06-28T16:10:00Z", note: "Initial published build", current: false, runs: 96 },
];

/** Live and recent sandbox instances of this environment. */
export const INSTANCES = [
  { id: "inst-9f2c41", status: "running", task: "Refunds above $200 need supervisor approval", uptimeS: 42, region: "us-east-1", version: "v7", cpu: 38, mem: 61 },
  { id: "inst-7b1a08", status: "running", task: "Never confirm identity from the phone number alone", uptimeS: 31, region: "us-east-1", version: "v7", cpu: 24, mem: 55 },
  { id: "inst-3d88fe", status: "grading", task: "Return window is 30 days from delivery", uptimeS: 88, region: "us-east-1", version: "v7", cpu: 12, mem: 48 },
  { id: "inst-1c4d90", status: "passed", task: "Routine task using lookup_order", uptimeS: 64, region: "us-east-1", version: "v7", cpu: 0, mem: 0 },
  { id: "inst-55ab21", status: "failed", task: "Do not disclose another customer's order details", uptimeS: 71, region: "us-east-1", version: "v7", cpu: 0, mem: 0 },
];

/**
 * Files inside the environment. `diff` marks files an agent changed during a
 * run — the thing file tracking exists to show you.
 */
export const ENV_FILES = [
  { path: "/seed/orders.csv", size: "412 KB", kind: "seed", changed: false },
  { path: "/seed/customers.csv", size: "168 KB", kind: "seed", changed: false },
  { path: "/seed/returns.csv", size: "72 KB", kind: "seed", changed: false },
  { path: "/config/rules.yaml", size: "4 KB", kind: "config", changed: false },
  { path: "/config/tools.json", size: "9 KB", kind: "config", changed: false },
  { path: "/state/session.db", size: "1.2 MB", kind: "state", changed: true, diff: "+18 rows" },
  { path: "/state/audit.log", size: "86 KB", kind: "state", changed: true, diff: "+142 lines" },
];

export const FILE_KIND_COLOR = {
  seed: "#2563EB",
  config: "#7857FC",
  state: "#EA580C",
};
