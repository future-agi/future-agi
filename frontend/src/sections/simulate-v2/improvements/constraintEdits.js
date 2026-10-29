/*
  An improvement's own constraints: the agent's (Agents → Constraints), with
  whatever this one improvement changes inline. A change applies to this
  improvement only unless the person saves it to the agent too — then every
  later improvement for that agent is held to it.
*/
import { BUDGETS } from "../_mock/improvementStart";

export const BUDGET_STEPS = BUDGETS;
export const MINUTE_STEPS = [15, 30, 60, 120, 150];
export const COST_STEPS = [0, 2, 5, 10, 20];
export const LATENCY_STEPS = [0, 5, 10, 20];

export const minutesLabel = (n) =>
  n < 60
    ? `${n} minutes`
    : n % 60
      ? `${Math.floor(n / 60)} h ${n % 60} min`
      : `${n / 60} hour${n === 60 ? "" : "s"}`;

/* The agent's own value for an editable constraint. */
export const agentValueOf = (limits, key) => {
  if (key === "dollars") return limits?.budget?.dollars;
  if (key === "minutes") return limits?.budget?.minutes;
  if (key === "costCeiling")
    return limits?.costMode === "abs" ? null : limits?.costCeiling;
  return limits?.[key];
};

/* The keys this improvement set differently from the agent. */
export const changedKeysOf = (limits, edits = {}) =>
  Object.keys(edits).filter(
    (k) => edits[k] != null && edits[k] !== agentValueOf(limits, k),
  );

/* The agent's constraints with this improvement's edits applied. */
export const withEdits = (limits, edits = {}) => ({
  ...limits,
  ...(edits.costCeiling != null && {
    costMode: "pct",
    costCeiling: edits.costCeiling,
  }),
  ...(edits.latencyCeiling != null && {
    latencyCeiling: edits.latencyCeiling,
  }),
  budget: {
    ...limits.budget,
    ...(edits.dollars != null && { dollars: edits.dollars }),
    ...(edits.minutes != null && { minutes: edits.minutes }),
  },
});

/* What saving one edit to the agent writes. */
export const agentPatchFor = (limits, key, value) => {
  if (key === "dollars" || key === "minutes")
    return { budget: { ...limits.budget, [key]: value } };
  if (key === "costCeiling") return { costMode: "pct", costCeiling: value };
  return { [key]: value };
};

/* Steps for a picker, keeping a value set elsewhere selectable. */
export const stepsWith = (steps, ...values) =>
  [...new Set([...steps, ...values.filter((v) => v != null)])].sort(
    (a, b) => a - b,
  );
