/*
  Must-pass — one rule for every environment an improvement checks.

  A scenario gates a candidate only when it was measured and passed every
  baseline repeat today. Marked must-pass (or regression memory) doesn't
  change that: a marked scenario that's flaky or failing today would reject
  real fixes on its own noise, so it's named — "marked must-pass but flaky
  today, so it isn't counted" — and not counted until it passes every repeat
  again. The setup's counts, the main environment's set and each other
  environment's set all read it from here.
*/
import { passShareOf } from "./optimize";

export const passesEveryRepeat = (t) => !!t && t.status !== "unmeasured" && passShareOf(t) >= 1;
export const flakyToday = (t) => !!t && t.status !== "unmeasured" && passShareOf(t) > 0 && passShareOf(t) < 1;

/**
 * `tasks`: a baseline's scenarios. `marked(t)`: marked must-pass or kept by
 * regression memory (defaults to the scenario's own mark).
 *   → { gated: [task], markedNotCounted: [{ id, title, flaky | failing }] }
 */
export const mustPassOf = (tasks = [], { marked = (t) => !!t.critical } = {}) => ({
  gated: (tasks || []).filter(passesEveryRepeat),
  markedNotCounted: (tasks || [])
    .filter((t) => marked(t) && t.status !== "unmeasured" && !passesEveryRepeat(t))
    .map((t) => ({ id: t.id, title: t.title, ...(flakyToday(t) ? { flaky: true } : { failing: true }) })),
});

/* "Refund over limit is marked must-pass but flaky today, so it isn't counted." */
export const markedNotCountedLine = (list = []) => {
  if (!list.length) return null;
  const flaky = list.every((t) => t.flaky) ? "flaky" : list.every((t) => t.failing) ? "failing" : "flaky or failing";
  if (list.length === 1) return `${list[0].title || list[0].id} is marked must-pass but ${flaky} today, so it isn't counted.`;
  const names = list.slice(0, 2).map((t) => t.title || t.id).join(", ");
  const more = list.length > 2 ? ` and ${list.length - 2} more` : "";
  return `${names}${more} are marked must-pass but ${flaky} today, so they aren't counted.`;
};
