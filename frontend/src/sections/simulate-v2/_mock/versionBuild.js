/*
  A new environment version being built after approved changes (a source's
  proposal, for now). Stored on the environment's state as
  { label, from, at, steps: [{ kind, label, total }], changes }, and read
  back against the clock, so leaving the page and coming back shows the same
  progress — and a build that finished while away finishes on the next visit.

  Only what changes streams in: the tables a file seeds, the rules it adds,
  the scenarios it proposes. Everything else carries over unchanged.
*/

const STEP_MS = 3500;

/* The tab each kind of change shows on. */
export const CHANGE_TAB = {
  rules: "contract",
  tables: "world",
  tools: "world",
  scenarios: "scenarios",
};

/* The tab a first build shows while each step runs — it follows the build
   until the person picks a tab (builder.md §4 "Inspect the build"). */
export const FOLLOW_TAB = {
  ...CHANGE_TAB,
  world: "world",
  "world-check": "world",
  "scenario-check": "scenarios",
  release: "overview",
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What a version build will do, from the changes it carries. */
export function versionBuildOf({
  label,
  shownLabel = null,
  from,
  rules = [],
  tables = [],
  scenarios = [],
  now,
}) {
  const sourceNames = [...new Set(tables.map((t) => t.source).filter(Boolean))];
  const steps = [
    rules.length && {
      kind: "rules",
      label: `Adding ${plural(rules.length, "rule")} to the contract`,
      total: rules.length,
    },
    tables.length && {
      kind: "tables",
      label: `Seeding the world from ${sourceNames.join(", ") || "your records"}`,
      total: tables.reduce((n, t) => n + (t.rows || 0), 0),
    },
    scenarios.length && {
      kind: "scenarios",
      label: `Writing and checking ${plural(scenarios.length, "scenario")}`,
      total: scenarios.length,
    },
    { kind: "check", label: "Checking the new version starts", total: 1 },
  ].filter(Boolean);
  return {
    label,
    /* The number the person saw on Save ("v2"); `label` is the stored one. */
    shownLabel,
    from,
    at: now || Date.now(),
    steps,
    changes: {
      rules,
      tables: tables.map(({ name, rows }) => ({ name, rows })),
      scenarios,
    },
    scenarios: scenarios.length,
  };
}

/*
  The first build's steps, as the build engine numbers them. Step 1,
  Understanding the agent, ran when the agent was connected and wrote the
  contract that was reviewed and approved; the build runs 2–12 from that
  contract, shown as three phases.
*/
export const BUILD_PHASES = [
  { id: "world", label: "Writing the world", short: "the world" },
  {
    id: "scenarios",
    label: "Writing and checking scenarios",
    short: "the scenarios",
  },
  { id: "checks", label: "Running the release checks", short: "the checks" },
];
export const BUILD_STEP_TOTAL = 12;
/* Older stored first builds kept no phase on their steps. */
const PHASE_OF_KIND = {
  rules: "world",
  world: "world",
  tools: "world",
  "world-check": "world",
  tables: "world",
  scenarios: "scenarios",
  "scenario-check": "scenarios",
  release: "checks",
};
export const phaseOfStep = (s) =>
  (s && typeof s === "object" && (s.phase || PHASE_OF_KIND[s.kind])) || null;

/**
 * The first build of an approved design (builder.md §3–4): everything is new,
 * so every part streams in — the world, its records, the scenarios — and the
 * release checks run as its last phase, before Save is offered (§4:
 * "validates them before offering Save release"). `first` keeps it apart
 * from a later version's build: no "New in v1" labels, no "what changed".
 */
export function firstBuildOf({
  tables = [],
  tools = [],
  rules = [],
  scenarios = [],
  now,
  stop = null,
}) {
  const rowsTotal = tables.reduce((n, t) => n + (t.rows || 0), 0);
  const nTools = tools.length;
  const nScn = scenarios.length;
  const step = (no, phase, kind, label, detail, total, ms) => ({
    no,
    phase,
    kind,
    label,
    detail,
    total: Math.max(1, total || 0),
    ...(ms && { ms }),
  });
  const steps = [
    /* The contract's rules are what the world is worked out from. */
    step(
      2,
      "world",
      rules.length ? "rules" : "world",
      "Generating environment",
      rules.length
        ? `the world from ${plural(rules.length, "rule")}`
        : "the world the tools act on",
      rules.length,
      2500,
    ),
    step(
      3,
      "world",
      "tools",
      "Building environment",
      `${plural(nTools, "tool")} on real state`,
      nTools,
      2500,
    ),
    step(
      4,
      "world",
      "world-check",
      "Validating environment",
      "every tool answers truthfully",
      nTools,
      2500,
    ),
    step(
      5,
      "world",
      "tables",
      "Generating data",
      `${plural(tables.length, "table")}, ${rowsTotal.toLocaleString()} rows`,
      rowsTotal || tables.length,
      3000,
    ),
    step(
      6,
      "scenarios",
      "scenarios",
      "Generating scenarios",
      `${plural(nScn, "scenario")} with sub-goals`,
      nScn,
      3500,
    ),
    step(
      7,
      "scenarios",
      "scenario-check",
      "Validating scenarios",
      "ready, solvable, not vacuous",
      nScn,
      2500,
    ),
    step(8, "checks", "release", "Connecting agent", "", 1, 1200),
    step(9, "checks", "release", "Running", "", nScn, 1600),
    step(10, "checks", "release", "Grading", "", nScn, 1200),
    step(11, "checks", "release", "Uploading artifacts", "", 1, 1000),
    step(12, "checks", "release", "Cleaning up", "", 1, 800),
  ];
  return {
    first: true,
    label: "v1",
    shownLabel: "v1",
    from: null,
    at: now || Date.now(),
    steps,
    /* Where this build stops (a demo case), or null: { case, no }. */
    stop,
    changes: {
      rules,
      tables: tables.map(({ name, rows }) => ({ name, rows })),
      tools: tools.map((t) => t.name),
      scenarios,
    },
    scenarios: scenarios.length,
  };
}

/** The kind of step a build is on now, or null once it's done. */
export function stepKindNow(build, now = Date.now()) {
  const prog = versionBuildProgress(build, now);
  if (!prog || prog.done || prog.stopped) return null;
  return build.steps[prog.step]?.kind || null;
}

/** A step's words — older stored builds kept plain strings. */
export const stepLabel = (s) => (typeof s === "string" ? s : s.label);

const msOf = (s) => (s && typeof s === "object" && s.ms) || STEP_MS;
/* How far into a stopped step the build got before it stopped. */
const STOP_AT = 0.45;

/** Where a stopping build stops: { index, atMs } — or null. */
export function stopPointOf(build) {
  if (!build?.stop) return null;
  const index = build.steps.findIndex((s) => s.no === build.stop.no);
  if (index < 0) return null;
  const before = build.steps.slice(0, index).reduce((n, s) => n + msOf(s), 0);
  return { index, atMs: before + msOf(build.steps[index]) * STOP_AT };
}

/**
 * Where a version build is: the step running now, whether it's done, and —
 * for a build that stopped — `stopped`, frozen on the step that stopped.
 */
export function versionBuildProgress(build, now = Date.now()) {
  if (!build) return null;
  const elapsed = Math.max(0, now - build.at);
  const stop = stopPointOf(build);
  if (stop && elapsed >= stop.atMs)
    return {
      step: stop.index,
      fraction: STOP_AT,
      done: false,
      stopped: true,
      leftMs: 0,
    };
  const total = build.steps.reduce((n, s) => n + msOf(s), 0);
  let left = elapsed;
  let step = 0;
  while (step < build.steps.length && left >= msOf(build.steps[step])) {
    left -= msOf(build.steps[step]);
    step += 1;
  }
  const done = step >= build.steps.length;
  return {
    step: Math.min(step, build.steps.length - 1),
    fraction: done ? 1 : left / msOf(build.steps[step]),
    done,
    stopped: false,
    leftMs: Math.max(0, build.at + total - now),
  };
}

/**
 * How far one kind of change has got: null when this build doesn't touch it,
 * else { label, state: "next" | "now" | "done", shown, total, ids }.
 * For tables, `rowsOf(name)` gives each table's rows written so far.
 */
export function changeOf(build, kind, now = Date.now()) {
  if (!build?.changes) return null;
  const at = build.steps.findIndex((s) => s.kind === kind);
  if (at < 0) return null;
  const prog = versionBuildProgress(build, now);
  if (prog.done) return null;
  const { total } = build.steps[at];
  /* A build that stopped: what it finished is done; the rest wasn't written. */
  const state =
    prog.step > at
      ? "done"
      : prog.stopped
        ? "stopped"
        : prog.step === at
          ? "now"
          : "next";
  const shown =
    state === "done"
      ? total
      : state === "now" || (state === "stopped" && prog.step === at)
        ? Math.floor(prog.fraction * total)
        : 0;
  const ids =
    kind === "tables"
      ? build.changes.tables.map((t) => t.name)
      : build.changes[kind] || [];
  const rowsOf = (name) => {
    let left = shown;
    for (const t of build.changes.tables) {
      const n = Math.min(t.rows || 0, left);
      if (t.name === name) return n;
      left -= n;
    }
    return 0;
  };
  return {
    label: build.shownLabel || build.label,
    state,
    shown,
    total,
    ids,
    rowsOf,
  };
}

/** What a finished build leaves to point out: { label, rules, tables, scenarios, unseen }. */
export function versionNewsOf(build) {
  /* A first build made everything — nothing to point out as new. */
  if (build?.first) return null;
  const c = build?.changes;
  if (!c) return null;
  const kinds = ["rules", "tables", "scenarios"].filter((k) => c[k]?.length);
  if (!kinds.length) return null;
  return {
    label: build.shownLabel || build.label,
    rules: c.rules,
    tables: c.tables.map((t) => t.name),
    scenarios: c.scenarios,
    unseen: kinds.map((k) => CHANGE_TAB[k]),
  };
}

/** The tabs with a change building or not yet looked at. */
export function changedTabsOf(envState) {
  const tabs = new Set(envState?.versionNews?.unseen || []);
  (envState?.versionBuild?.steps || []).forEach((s) => {
    if (CHANGE_TAB[s.kind]) tabs.add(CHANGE_TAB[s.kind]);
  });
  return tabs;
}
