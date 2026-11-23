/*
  An epoch's own constraints: the agent's (Agents → Constraints), with what
  this one epoch may spend set inline (its budget and time, the skill it
  focuses on). A spend past the agent's limit applies to this epoch only
  unless the person saves it to the agent too. What it may change is never
  set per epoch — only on the agent.
*/
import { BUDGETS } from "../_mock/improvementStart";
import { areaStatesOf, configAreasFor } from "../agents/configAreas";
import { surfaceAvailability } from "../agents/agentLimits";

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

/*
  What an epoch may change, read from the agent's rules (Agents →
  Constraints) and named the way that tab names them — its areas, then Code
  for a code agent. The agent's rules are the only place this is set; an
  epoch's setup and its running view only say it.
    → ["Instructions", "Opening and closing", "Tools"]
*/
export const mayChangeAreasOf = (limits, agent = null) => {
  const can = agent ? surfaceAvailability(agent) : {};
  const states = areaStatesOf(limits, can);
  const names = configAreasFor(agent)
    .filter((a) => states[a.id])
    .map((a) => a.label);
  const code =
    (limits?.allowed || []).includes("code") && (!agent || can.code?.ok);
  return code ? [...names, "Code"] : names;
};

/* "Instructions, opening and closing, tools" — the areas as one list. */
export const mayChangeListOf = (limits, agent = null) => {
  const names = mayChangeAreasOf(limits, agent);
  return names.length
    ? names
        .map((n, i) => (i ? n.charAt(0).toLowerCase() + n.slice(1) : n))
        .join(", ")
    : "Nothing";
};

/* "Instructions, tools · Model locked" — the agent's rules in one line. */
export const mayChangeLineOf = (limits, agent = null) =>
  `${mayChangeListOf(limits, agent)} · ${
    limits?.model?.mode === "approved"
      ? "Model: approved list only"
      : "Model locked"
  }`;

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
    (k) =>
      edits[k] != null &&
      JSON.stringify(edits[k]) !== JSON.stringify(agentValueOf(limits, k)),
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
  /* What it may change and the files it must never edit, set in place. */
  ...(edits.allowed && { allowed: edits.allowed }),
  ...(edits.protected && { protected: edits.protected }),
  /* Which skill may change, and how much of it. */
  ...(edits.skillFocus && { skillFocus: edits.skillFocus }),
  ...(edits.skillDepth && { skillDepth: edits.skillDepth }),
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
