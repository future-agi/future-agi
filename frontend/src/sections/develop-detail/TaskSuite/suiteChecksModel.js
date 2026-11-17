// W6 C1 (W6-4) · suite-wide checks, pure.
//
// A task suite keeps the checks that apply to "Every task in the suite" (in
// every run started after the version is saved) with its settings: `environment.suite_checks`, a list of checks
// in the same shape the check editor builds for a row (`buildCheck`). They
// are written as the change set's `set_environment` op, so they wait as an
// unsaved change and the next version records them, like the environment's
// image or reset command. The mock world judges them on every task next to
// the task's own checks (fixtures/world/taskSuite.js suiteChecksOfDataset).

export const APPLIES_TO = Object.freeze([
  { id: "task", label: "This task" },
  { id: "suite", label: "Every task in the suite" },
]);

/** The suite-wide checks of an environment (saved or with its pending change). */
export function suiteChecksOfEnvironment(environment) {
  const list = environment?.suite_checks;
  return Array.isArray(list) ? list.filter((c) => c && c.key) : [];
}

/** Add `check`, or replace the suite check with its key (order kept). */
export function upsertSuiteCheck(list, check) {
  const rest = list ?? [];
  if (!check?.key) return rest;
  const at = rest.findIndex((c) => c.key === check.key);
  if (at === -1) return [...rest, check];
  return rest.map((c, i) => (i === at ? check : c));
}

/** The suite checks without the one keyed `key`. */
export const removeSuiteCheck = (list, key) =>
  (list ?? []).filter((c) => c.key !== key);

/**
 * The `set_environment` op that sets `suite_checks` to `next`. A pending
 * environment change (image, MCP, reset command) is kept: the change set
 * holds one environment op, so this one carries that patch too. `before` is
 * the saved environment, as the environment drawer sends it; the image stays
 * the saved one unless a pending change sets another, so the pending bar's
 * environment line names the image the suite keeps.
 */
export function suiteChecksOp({ saved = null, pendingOp = null, next = [] }) {
  const {
    op: _op,
    id: _id,
    at: _at,
    before: _before,
    ...pending
  } = pendingOp ?? {};
  return {
    op: "set_environment",
    ...(saved?.image ? { image: saved.image } : {}),
    ...pending,
    suite_checks: next,
    before: saved,
  };
}

/**
 * Keys a new check may not take: the task's own checks and the suite's
 * (except the one being edited, `editing`).
 */
export function takenKeys({ taskKeys = [], suite = [], editing = null }) {
  return [...taskKeys, ...suite.map((c) => c.key)].filter(
    (k) => k && k !== editing,
  );
}

/** The snackbar line after a suite-wide save or removal. */
export function suiteChangeText(key, { removed = false, count } = {}) {
  if (removed) return `${key} no longer applies to every task · unsaved`;
  const n = Number.isFinite(count) ? ` (${count} suite-wide)` : "";
  return `${key} applies to every task in the suite${n} · unsaved`;
}
