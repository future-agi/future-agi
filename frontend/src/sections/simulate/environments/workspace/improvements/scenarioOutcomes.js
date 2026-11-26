// What one candidate did to each scenario: the baseline check runs against the
// candidate's, compared per scenario. A side passes a scenario when every
// measured call of it passed. It reads the judge's result, the one the verdict
// uses: a failed platform eval makes a call's outcome inconclusive without
// changing whether the judge passed it.
//
// Only the verdict says what broke. A drop it counted is Broke; a drop it
// called normal noise, or did not judge at all, is named for what it was and
// never reads as Broke.
// Broke first: it is what must be read before anything ships.
const ORDER = [
  "broke",
  "dropped",
  "noise",
  "fixed",
  "still-failing",
  "same",
  "not-measured",
];

const measured = (t) =>
  t.harnessOutcomeStatus === "passed" || t.harnessOutcomeStatus === "failed";

// The scenarios a verdict's must-pass list names. Each entry is
// "<scenario key>: <check>", so one scenario can be named once per check.
export const mustPassScenarioKeys = (checks = []) =>
  new Set(
    checks.map((check) => {
      const at = check.indexOf(": ");
      return at < 0 ? check : check.slice(0, at);
    }),
  );

// Every call of each scenario, with how many of its measured calls passed.
function byScenario(tasks) {
  const byKey = new Map();
  tasks.forEach((t) => {
    const prev = byKey.get(t.sourceScenarioKey) || {
      calls: [],
      measured: 0,
      passing: 0,
    };
    prev.calls.push(t);
    if (measured(t)) {
      prev.measured += 1;
      if (t.harnessOutcomeStatus === "passed") prev.passing += 1;
    }
    byKey.set(t.sourceScenarioKey, prev);
  });
  return byKey;
}

const passedAll = (side) =>
  side?.measured ? side.passing === side.measured : null;

function outcomeOf(key, before, after, verdict) {
  if (verdict.counted.has(key)) return "broke";
  if (before === null || after === null) return "not-measured";
  if (!before && after) return "fixed";
  if (before && !after) return verdict.noise.has(key) ? "noise" : "dropped";
  if (!before && !after) return "still-failing";
  return "same";
}

const trialCount = (n) => `${n} ${n === 1 ? "trial" : "trials"}`;

// The candidate's trials of a scenario, in plain words: a slash only when every
// one was measured.
export function trialsText({ passed, of, ran }) {
  const unmeasured = ran - of;
  if (!unmeasured) return `${passed}/${trialCount(of)}`;
  if (!of) return `${trialCount(ran)} not measured`;
  return `${passed} of ${of} measured, ${unmeasured} not measured`;
}

// A scenario the epoch's read has no stored name for. Its key is never shown.
const UNNAMED_SCENARIO = "Unnamed scenario";

// One row per scenario the candidate called, measured or not. `verdict` is the
// candidate's: its must-pass lists say which drops count. `names` is the read's
// `scenario_names`, the stored name by key that the server's words use too.
export function scenarioOutcomes(
  beforeTasks,
  afterTasks,
  verdict = null,
  names = {},
) {
  const before = byScenario(beforeTasks);
  const lists = {
    counted: mustPassScenarioKeys(verdict?.must_pass_regressions),
    noise: mustPassScenarioKeys(verdict?.must_pass_within_noise),
  };
  const rows = [];
  byScenario(afterTasks).forEach((a, key) => {
    const b = before.get(key);
    const was = passedAll(b);
    const is = passedAll(a);
    const outcome = outcomeOf(key, was, is, lists);
    const afterTrials = {
      passed: a.passing,
      of: a.measured,
      ran: a.calls.length,
    };
    rows.push({
      key,
      name: names?.[key] || UNNAMED_SCENARIO,
      before: was === null ? null : was ? "passed" : "failed",
      after: is === null ? null : is ? "passed" : "failed",
      outcome,
      // The calls on each side, for the before-and-after compare.
      beforeCalls: b?.calls || [],
      afterCalls: a.calls,
      afterTrials,
      trials:
        outcome === "not-measured" && a.measured
          ? "not measured before the change"
          : trialsText(afterTrials),
    });
  });
  return rows.sort(
    (x, y) => ORDER.indexOf(x.outcome) - ORDER.indexOf(y.outcome),
  );
}

// The set a scenario was in, as the request names it. The read has no list of
// the must-pass scenarios, only their count, so a protected case is the one
// must-pass scenario it can name; the rest are in no set here.
export function scenarioSetOf(request, key) {
  if (request?.held_back_scenario_keys?.includes(key)) return "held";
  if (request?.target_scenario_keys?.includes(key)) return "target";
  if (request?.protected_scenario_keys?.includes(key)) return "protected";
  return null;
}

// Held-back scenarios that failed before the change, and how many of them the
// winner fixed. A failing one the winner never measured counts as not fixed,
// so it stays in the total.
export function heldBackTally(beforeTasks, afterTasks, heldKeys) {
  const before = byScenario(beforeTasks);
  const after = byScenario(afterTasks);
  const failing = heldKeys.filter(
    (key) => passedAll(before.get(key)) === false,
  );
  const fixed = failing.filter(
    (key) => passedAll(after.get(key)) === true,
  ).length;
  return { fixed, failing: failing.length };
}
