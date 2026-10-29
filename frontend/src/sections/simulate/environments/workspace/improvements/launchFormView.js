// What the launch dialog says about a pick, from launch-options and the
// user's choices. Nothing is estimated: the budget and the time limit are what
// hold a fix, and it stops at the limit with the best it found.
import { formatCredits, formatMinutes } from "./fixRunView";

export const HELD_BACK_MINIMUM = 12;

const TOO_FEW =
  "Too few failing cases to hold some back. The fix will be proven on the picked cases only.";

export const heldBackCount = (picked) =>
  picked >= HELD_BACK_MINIMUM ? Math.floor(picked / 3) : 0;

export const heldBackLine = (picked) =>
  picked >= HELD_BACK_MINIMUM
    ? `About ${heldBackCount(picked)} held back to prove the fix`
    : TOO_FEW;

export function countsLine(picked, protectedCount) {
  const held = heldBackCount(picked);
  const parts = [`${picked - held} failing to fix`];
  if (held) parts.push(`${held} held back to prove it`);
  parts.push(`${protectedCount} must keep passing`);
  return parts.join(" · ");
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

export function launchError({ targets, protectedKeys, surfaces, limits }) {
  if (!targets.length) return "Pick at least one failing case to fix.";
  if (targets.length > limits.scenarios_max) {
    return `Pick at most ${limits.scenarios_max} cases to fix.`;
  }
  if (protectedKeys.some((k) => targets.includes(k))) {
    return "A case can't be both fixed and protected.";
  }
  if (!surfaces.length) return "Pick at least one part it may change.";
  return null;
}

export const scenarioStatus = (passed) => {
  if (passed === true) return "Passing";
  if (passed === false) return "Failing";
  return "Not measured";
};
