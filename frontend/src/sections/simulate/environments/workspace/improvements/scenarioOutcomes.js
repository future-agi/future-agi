// What one trial did to each scenario: the baseline check run against the
// trial's, compared per scenario. A scenario passes when every measured trial
// of it passed; unmeasured results are left out. It reads the judge's result,
// the one the verdict uses: a failed platform eval makes a call's outcome
// inconclusive without changing whether the judge passed it.
// Broke first: it is what must be read before anything ships.
const ORDER = ["broke", "fixed", "still-failing", "same"];

const measured = (t) =>
  t.harnessOutcomeStatus === "passed" || t.harnessOutcomeStatus === "failed";

// Every call of each scenario, with how many of its measured calls passed.
function byScenario(tasks) {
  const byKey = new Map();
  tasks.forEach((t) => {
    const prev = byKey.get(t.sourceScenarioKey) || {
      name: t.sourceScenario,
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

function verdicts(tasks) {
  const byKey = new Map();
  byScenario(tasks).forEach((s, key) => {
    if (s.measured) byKey.set(key, { ...s, passed: s.passing === s.measured });
  });
  return byKey;
}

const outcomeOf = (before, after) => {
  if (!before && after) return "fixed";
  if (before && !after) return "broke";
  if (!before && !after) return "still-failing";
  return "same";
};

export function scenarioOutcomes(beforeTasks, afterTasks) {
  const before = verdicts(beforeTasks);
  const after = verdicts(afterTasks);
  const rows = [];
  after.forEach((a, key) => {
    const b = before.get(key);
    if (!b) return;
    rows.push({
      key,
      name: a.name,
      before: b.passed ? "passed" : "failed",
      after: a.passed ? "passed" : "failed",
      outcome: outcomeOf(b.passed, a.passed),
      // The calls on each side, for the before-and-after compare.
      beforeCalls: b.calls,
      afterCalls: a.calls,
      afterTrials: { passed: a.passing, of: a.measured },
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
  const before = verdicts(beforeTasks);
  const after = verdicts(afterTasks);
  const failing = heldKeys.filter((key) => before.get(key)?.passed === false);
  const fixed = failing.filter((key) => after.get(key)?.passed === true).length;
  return { fixed, failing: failing.length };
}
