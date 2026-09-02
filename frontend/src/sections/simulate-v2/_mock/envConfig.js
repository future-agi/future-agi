import { environmentVersions } from "./versions";

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

/* ── lineage ──────────────────────────────────────────────────────────────
 *
 * "Every task starts from the same snapshot" is the claim that makes two runs
 * comparable, and until now it was a sentence in a settings label. Here it is a
 * shape instead: one frozen master, built when the environment was published,
 * and a copy per task that is written to and then destroyed.
 *
 * Derived from the environment rather than hand-written, so the master quotes
 * this environment's own tables and the copies run its own tasks.
 */

const shortHash = (s = "") => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36).slice(0, 6).padEnd(6, "0");
};

/** The read-only snapshot every instance is copied from. */
export const masterSnapshot = (env, envState) => {
  const tables = env?.seed?.tables || [];
  const rows = tables.reduce((a, t) => a + (t.rows || 0), 0);
  /* The version the rest of the app shows — a master pinned to a version
     nothing else mentions would undo the point of pinning it. */
  const versions = environmentVersions(env, envState);
  const current = versions.find((v) => v.current) || versions[0];
  return {
    id: `master-${shortHash(env?.id || "env")}`,
    version: current.label,
    builtAt: current.createdAt,
    tables: tables.length,
    rows,
    sizeMB: Math.max(1, Math.round(rows / 900)),
    /* Nothing ever writes here. That is the entire point of the record. */
    readOnly: true,
  };
};

/**
 * The copies. Two still running, one grading, two finished and destroyed —
 * enough to show the shape of a batch mid-flight rather than a tidy end state.
 */
const COPY_PLAN = [
  { status: "running", uptimeS: 42, cpu: 38, mem: 61 },
  { status: "running", uptimeS: 31, cpu: 24, mem: 55 },
  { status: "grading", uptimeS: 88, cpu: 12, mem: 48 },
  { status: "passed", uptimeS: 64, cpu: 0, mem: 0 },
  { status: "failed", uptimeS: 71, cpu: 0, mem: 0 },
];

/**
 * The copies a run made.
 *
 * `active` is what a run in flight looks like — some copies still executing,
 * some grading, some already torn down. A run that finished has none of that:
 * every copy is gone, along with everything it wrote, which is the claim this
 * panel exists to demonstrate. Showing three "Running" instances for a run that
 * ended yesterday contradicts it in the same screen that makes it.
 */
export const instancesFor = (env, { active = false } = {}) => {
  const master = masterSnapshot(env);
  const rules = env?.rules || [];
  const tools = env?.tools || [];
  const tasks = [
    rules[0],
    rules[1],
    rules[2],
    tools[0] && `Routine task using ${tools[0].name}`,
    rules[3] || (tools[1] && `Routine task using ${tools[1].name}`),
  ];

  return COPY_PLAN.map((c, i) => {
    const live = active && ["running", "grading"].includes(c.status);
    const status = live ? c.status : c.status === "failed" ? "failed" : "passed";
    return {
      ...c,
      status,
      id: `inst-${shortHash(`${env?.id || "env"}-${i}`)}`,
      from: master.id,
      task: tasks[i] || `Task ${i + 1}`,
      region: DEFAULT_RUNTIME.region,
      version: master.version,
      /* Writes land in the copy and die with it. */
      wroteRows: live ? 4 + i * 3 : 9 + i * 6,
      destroyed: !live,
    };
  });
};

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
