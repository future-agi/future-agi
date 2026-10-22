import { environmentVersions } from "./versions";
import { migrateEnvVars, preflightRecord, providerOfKey } from "./secretRefs";

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

/*
  A secret is stored as a reference into the team's secret manager, never as
  its value (AC-6.12) — `ref` on a secret row, `value` on a plain one.
*/
export const DEFAULT_ENV_VARS = [
  { key: "OPENAI_API_KEY", ref: "vault://acme/sim/openai_api_key", secret: true, usedBy: "grader" },
  { key: "SEED_DATASET", value: "orders-v4", secret: false, usedBy: "environment" },
  { key: "LOG_LEVEL", value: "info", secret: false, usedBy: "environment" },
];

/** This environment's variables — saved on envState.envVars, else the defaults. Raw secrets never survive the read. */
export const envVarsOf = (envState) => migrateEnvVars(envState?.envVars) || DEFAULT_ENV_VARS;

/**
 * Point a secret variable at a new reference. Re-runs the access check and
 * makes the next credential set (credentialSetPatch) — which the environment's
 * history reads as a credentials-only version, v7 → v7.1 (envSnapshot.js).
 */
export const envVarRefPatch = (envState, key, ref) => ({
  envVars: envVarsOf(envState).map((v) => (v.key === key ? { key: v.key, secret: true, usedBy: v.usedBy, ref } : v)),
  secretChecks: { ...(envState?.secretChecks || {}), [`env:${key}`]: preflightRecord(ref, providerOfKey(key)) },
});

/** The last access check for a variable, if one has run. */
export const envVarCheckOf = (envState, key) => envState?.secretChecks?.[`env:${key}`] || null;

/*
  Credentials are versioned with what holds them (team decision — no
  separate credential versions to keep track of). This environment's secret
  references are kept as a log of sets; the environment's history reads each
  change as a credentials-only minor version (v7 → v7.1, envSnapshot.js), and
  each run records the set it used, so its environment version says which
  credentials it ran with — references only, never values. The agent's own
  key is versioned with the agent the same way (v5 → v5.1).
*/
export const secretsUsedOf = (envState) => ({
  ...Object.fromEntries(envVarsOf(envState).filter((v) => v.secret && v.ref).map((v) => [v.key, v.ref])),
  ...(envState?.credentialRefs || {}),
});

const sameRefs = (a = {}, b = {}) => {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
};

/* "STRIPE_KEY re-pointed · SLACK_TOKEN added" — what moved between two sets, by key. */
const refChanges = (before, after) => {
  if (!before) return [`First set · ${Object.keys(after).length} reference${Object.keys(after).length === 1 ? "" : "s"}`];
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  return keys.flatMap((k) => {
    if (!(k in before)) return [`${k} added`];
    if (!(k in after)) return [`${k} removed`];
    return before[k] !== after[k] ? [`${k} re-pointed`] : [];
  });
};

/**
 * Every credential-set version, oldest first: `{ label, at, refs, changes }`.
 * The stored ones, plus — when the references have moved since the last
 * stored one — the next one (no `at` yet: the next run or edit records it).
 */
export const credentialSetsOf = (envState) => {
  const stored = envState?.credentialSets || [];
  const refs = secretsUsedOf(envState);
  const last = stored[stored.length - 1];
  if (last && sameRefs(last.refs, refs)) return stored;
  return [...stored, { label: `v${stored.length + 1}`, at: null, refs, changes: refChanges(last?.refs, refs) }];
};

/** The credential set the next run uses. */
export const currentCredentialSet = (envState) => credentialSetsOf(envState).slice(-1)[0];

const recorded = (sets, at) => sets.map((s) => (s.at ? s : { ...s, at }));

/** `{ credentialSets }` with the current set recorded — what a run stamps against. */
export const recordCredentialSets = (envState, at = new Date().toISOString()) => ({
  credentialSets: recorded(credentialSetsOf(envState), at),
});

/**
 * Wrap a patch that may move a reference: the set as it stood is recorded
 * first, then the set after the patch — the next version when it differs.
 */
export const credentialSetPatch = (envState, next) => {
  const at = new Date().toISOString();
  const before = recordCredentialSets(envState, at).credentialSets;
  const after = credentialSetsOf({ ...envState, ...next, credentialSets: before });
  return { ...next, credentialSets: recorded(after, at) };
};

/** Access checks that failed on a secret still in use — said before a run, never blocking it. */
export const failedSecretChecksOf = (envState) => {
  const vars = new Set(envVarsOf(envState).filter((v) => v.secret).map((v) => `env:${v.key}`));
  return Object.entries(envState?.secretChecks || {})
    .filter(([id, c]) => c && c.ok === false && (!id.startsWith("env:") || vars.has(id)))
    .map(([id, c]) => ({ id: id.replace(/^env:/, ""), ref: c.ref, line: c.line }));
};

/*
  Model ids, feature flags and prompts change how the agent behaves, so they
  belong to the agent — a change there is a new agent version, and a run
  says which one it tested. Set here, they'd change the agent's behaviour
  without any version saying so.
*/
const AGENT_BEHAVIOUR_KEY = /(^|_)(MODEL|MODEL_ID|MODEL_NAME|TEMPERATURE|TOP_P|SYSTEM_PROMPT|PROMPT)($|_)|^(FEATURE|FLAG|FF|ENABLE|DISABLE|USE)_|_(FLAG|ENABLED)$/i;
export const isAgentBehaviourKey = (key) => AGENT_BEHAVIOUR_KEY.test(String(key || "").trim());
export const AGENT_BEHAVIOUR_MESSAGE = "That changes how the agent behaves — set it on the agent, where a change makes a new agent version, so every run says which one it tested.";

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
