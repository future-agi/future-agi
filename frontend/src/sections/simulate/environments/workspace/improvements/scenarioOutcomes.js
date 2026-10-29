// What one trial did to each scenario: the baseline check run against the
// trial's, compared per scenario. A scenario passes when every measured trial
// of it passed; unmeasured results are left out.
const ORDER = ["fixed", "broke", "still-failing", "same"];

function verdicts(tasks) {
  const byKey = new Map();
  tasks.forEach((t) => {
    if (t.status !== "passed" && t.status !== "failed") return;
    const prev = byKey.get(t.sourceScenarioKey);
    const passed = t.status === "passed" && (prev ? prev.passed : true);
    byKey.set(t.sourceScenarioKey, { name: t.sourceScenario, passed });
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
    });
  });
  return rows.sort(
    (x, y) => ORDER.indexOf(x.outcome) - ORDER.indexOf(y.outcome),
  );
}

export const OUTCOME_ORDER = ORDER;
