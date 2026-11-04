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
  scenarios: "scenarios",
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What a version build will do, from the changes it carries. */
export function versionBuildOf({
  label,
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

/** A step's words — older stored builds kept plain strings. */
export const stepLabel = (s) => (typeof s === "string" ? s : s.label);

/** Where a version build is: the step running now, and whether it's done. */
export function versionBuildProgress(build, now = Date.now()) {
  if (!build) return null;
  const elapsed = Math.max(0, now - build.at);
  const step = Math.floor(elapsed / STEP_MS);
  return {
    step: Math.min(step, build.steps.length - 1),
    fraction: (elapsed % STEP_MS) / STEP_MS,
    done: step >= build.steps.length,
    leftMs: Math.max(0, build.at + build.steps.length * STEP_MS - now),
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
  const state = prog.step > at ? "done" : prog.step === at ? "now" : "next";
  const shown =
    state === "done"
      ? total
      : state === "now"
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
  return { label: build.label, state, shown, total, ids, rowsOf };
}

/** What a finished build leaves to point out: { label, rules, tables, scenarios, unseen }. */
export function versionNewsOf(build) {
  const c = build?.changes;
  if (!c) return null;
  const kinds = ["rules", "tables", "scenarios"].filter((k) => c[k]?.length);
  if (!kinds.length) return null;
  return {
    label: build.label,
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
