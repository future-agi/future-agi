// What the launch dialog says about a pick, from launch-options and the
// user's choices. Nothing is estimated: the budget and the time limit are what
// hold a fix, and it stops at the limit with the best it found.
import { formatCredits, formatMinutes } from "./fixRunView";

export const HELD_BACK_MINIMUM = 12;

export const heldBackCount = (picked) =>
  picked >= HELD_BACK_MINIMUM ? Math.floor(picked / 3) : 0;

// Fewer failing cases than a fix needs: it first writes variations of each.
export const needsVariations = (picked, limits) =>
  picked > 0 && picked < (limits?.cases_min ?? 0);

export function countsLine(picked, protectedCount, limits) {
  const held = heldBackCount(picked);
  let failing = `${picked} failing to fix`;
  if (held) {
    failing = `${picked} failing: ${picked - held} to fix, ${held} held back to prove it`;
  } else if (needsVariations(picked, limits)) {
    failing = `${picked} failing, and up to ${picked * limits.variations_per_case} variations of them`;
  }
  return `${failing} · ${protectedCount} protected`;
}

// With so few, too few cases can be held back to verify the fix, however well it
// does: it ends with Limited validation, and a GitHub agent's pull request is a draft.
export function variationsNote(picked, limits, agentKind) {
  if (!needsVariations(picked, limits)) return null;
  const note = `An epoch needs ${limits.cases_min} failing cases. It first writes ${limits.variations_per_case} variations of each, then fixes the ones the current version also fails.`;
  if (picked >= limits.verified_cases_min) return note;
  const draft = agentKind === "github" ? " and opens a draft pull request" : "";
  return `${note} With fewer than ${limits.verified_cases_min} failing cases it can only end with Limited validation${draft}.`;
}

// Whether Start is disabled only by the environment's unsaved scenario changes, which
// the variations would be written on top of: the user can save or discard them.
export function blockedByDraft(options, picked) {
  return (
    !options.blocked &&
    needsVariations(picked, options.limits) &&
    options.variations_blocked?.code === "fix_suite_draft_open"
  );
}

// Why Start is disabled by the platform: the fix itself, or the variations it
// would write first.
export function blockedReason(options, picked) {
  if (options.blocked) return options.blocked.message;
  if (needsVariations(picked, options.limits)) {
    return options.variations_blocked?.message || null;
  }
  return null;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function listOf(words) {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

export function reviewSentence({
  targets,
  protectedCount,
  surfaces,
  all,
  budget,
  minutes,
}) {
  const labels = all
    .filter((s) => surfaces.includes(s.id))
    .map((s) => s.label.toLowerCase());
  return `Fix ${plural(targets, "failing case", "failing cases")} without breaking ${plural(
    protectedCount,
    "protected case",
    "protected cases",
  )}, changing ${listOf(labels)} only, up to ${formatCredits(budget)} or ${formatMinutes(minutes)}.`;
}

const BUDGET_STEPS = [500, 1000, 1500, 2500, 5000, 10000, 25000, 50000];
const MINUTE_STEPS = [15, 30, 60, 120, 150, 240, 480, 1440];

function stepsWithin(base, value, max) {
  return [...new Set([...base, value])]
    .filter((n) => n > 0 && n <= max)
    .sort((a, b) => a - b);
}

export const budgetSteps = (value, limits) =>
  stepsWithin(BUDGET_STEPS, value, limits.budget_credits_max);

export const minuteSteps = (value, limits) =>
  stepsWithin(MINUTE_STEPS, value, limits.max_minutes_max);

// How much slower a candidate may reply, or how much more its agent may spend
// per conversation, than the current version, in percent.
const MARGIN_STEPS = [10, 25, 50, 100];
const MARGIN_MAX = 1000;

// 0 holds a candidate to the current version's reply time or spend: no increase.
export const marginSteps = (value) => [
  0,
  ...stepsWithin(MARGIN_STEPS, value, MARGIN_MAX),
];

// The models the run's spans show the agent called: the lock a fix starts from.
export const seesModels = (options) =>
  options.defaults.allowed_models.length > 0;

// Whether the run exported the agent's model calls at all, named or not.
export const isTraced = (options) =>
  seesModels(options) || options.defaults.agent_cost_metered;

export const UNTRACED_NOTE =
  "This agent is not traced, so an epoch cannot see the models it calls or what they cost. The model lock and the cost limit will show “Unknown”.";

// Shown when the platform could not read the run's traces: the launch is refused
// until it can, so the form does not call the agent untraced.
export const tracesUnreadable = (options) =>
  options.blocked?.code === "fix_traces_unreadable";

export const NO_MODELS_NOTE =
  "This agent's traces name no model, so the model lock will show “Unknown”. Its cost is still checked.";

// Every model call the run exported was one Epoch could not measure, so none of
// them has a cost: the fix page says the same of such a call (fixRunView.js unknownWhy).
export const callsUnmeasured = (options) =>
  options.defaults.agent_calls_unmeasured === true;

export const UNMEASURED_NOTE =
  "This agent calls its model in a way Epoch can't measure, such as another SDK or a direct HTTP request. The model lock and the cost limit will show “Unknown”. Route its calls through the OpenAI or Anthropic SDK, or add traceAI tracing.";

// The limit on the agent's spend per conversation, in US dollars: null when
// left empty, NaN when it is not a number above 0.
export function costLimitOf(text) {
  const trimmed = String(text ?? "").trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value > 0 ? value : NaN;
}

export function launchError({
  targets,
  protectedKeys,
  surfaces,
  limits,
  models = [],
  seenModels = [],
  costLimit = "",
}) {
  if (!targets.length) return "Pick at least one failing case to fix.";
  if (targets.length < limits.scenarios_min) {
    return `Needs at least ${limits.scenarios_min} failing cases: with ${limits.variations_per_case} variations of each, they can reach the ${limits.cases_min} an epoch needs.`;
  }
  if (targets.length > limits.scenarios_max) {
    return `Pick at most ${limits.scenarios_max} cases to fix.`;
  }
  if (protectedKeys.some((k) => targets.includes(k))) {
    return "A case can't be both fixed and protected.";
  }
  if (!surfaces.length) return "Pick at least one part it may change.";
  if (seenModels.length && !models.length) return "Allow at least one model.";
  // The platform compares models without their provider, as the engine's lock does.
  const keyOf = (name) => name.toLowerCase().split("/").pop();
  const kept = new Set(models.map(keyOf));
  const leftOut = seenModels.filter((name) => !kept.has(keyOf(name)));
  if (leftOut.length) {
    return `Allow every model this run called: ${leftOut.join(", ")}.`;
  }
  if (Number.isNaN(costLimitOf(costLimit))) {
    return "The cost limit must be a number of dollars above 0.";
  }
  return null;
}

export const scenarioStatus = (passed) => {
  if (passed === true) return "Passing";
  if (passed === false) return "Failing";
  return "Not measured";
};
