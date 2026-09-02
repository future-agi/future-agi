/**
 * What an episode was worth.
 *
 * The product is an RL environment, and an environment's output is not a pass
 * rate — it is a return. Until now reward existed only as a table on the RL
 * interface page: a promise about what training would see, with nothing
 * anywhere else in the product computing it. That is the wrong way round. If
 * the reward spec is real, every episode already has a return, and it should be
 * on the screen next to the verdict it was derived from.
 *
 * Nothing new is invented here. The reward spec is the one the RL panel
 * publishes (`rewardTable`), and every term is settled from evidence the run
 * already recorded: which checks settled, whether a hard rule broke, whether a
 * claim went unsupported by the call log, how many steps it took. That is the
 * property worth protecting — the thing being optimised is the thing being
 * tested, so a run cannot score well on one and badly on the other.
 */

import { rewardTable } from "./rl";
import { isMeasured } from "./failures";

const TERM = Object.fromEntries(rewardTable().map((t) => [t.id, t]));

/**
 * One episode's return, and where it came from.
 *
 * Returned as the terms rather than a single number, because a return with no
 * decomposition is a score nobody can argue with — and arguing with it is how
 * a reward spec gets fixed.
 */
export const episodeReturn = (task, checklist) => {
  /* An episode nothing could be measured on has no return. Zero would be a
     lie: zero is what an agent scores by doing nothing, and this agent was
     never given the chance to do anything. */
  if (!task || !isMeasured(task)) return null;

  const steps = task.steps?.length || 0;
  const settled = (checklist?.steps || []).filter((s) => s.status === "addressed" && s.tool).length;
  const failedRule = (task.evalResults || []).some((r) => !r.passed && /policy|compliance|rule/i.test(r.name));
  const ungrounded = !!task.callLog?.unsupportedClaim;
  const passed = task.status === "passed";
  /* The reference solution's length, as the efficiency term is defined against
     it — a run that beat it did so measurably. */
  const reference = (task.callLog?.expected?.length || 3) * 3;

  const terms = [
    settled > 0 && { ...TERM.sub_goal, count: settled, value: TERM.sub_goal.value * settled },
    passed && { ...TERM.task, count: 1, value: TERM.task.value },
    failedRule && { ...TERM.policy, count: 1, value: TERM.policy.value },
    ungrounded && { ...TERM.ungrounded, count: 1, value: TERM.ungrounded.value },
    steps > 0 && { ...TERM.step, count: steps, value: TERM.step.value * steps },
    passed && steps < reference && { ...TERM.efficiency, count: 1, value: TERM.efficiency.value },
  ].filter(Boolean);

  const total = terms.reduce((a, t) => a + t.value, 0);
  return { total: Math.round(total * 100) / 100, terms };
};

/** Mean return across the episodes a run could measure. */
export const meanReturn = (tasks = [], checklists = new Map()) => {
  const returns = tasks
    .map((t) => episodeReturn(t, checklists.get(t.id))?.total)
    .filter((v) => v != null);
  if (!returns.length) return null;
  return Math.round((returns.reduce((a, v) => a + v, 0) / returns.length) * 100) / 100;
};

/**
 * The reward spec as a sentence, for the places that quote it.
 *
 * Worth stating wherever a return is shown: a number with no spec behind it is
 * not comparable between two environments, and someone will try.
 */
export const rewardSpecLine = () => {
  const t = rewardTable();
  return `${t.filter((x) => x.kind === "dense").length} dense terms, ${t.filter((x) => x.kind === "terminal").length} terminal, ${t.filter((x) => x.kind === "penalty").length} penalties — the same checks that grade the run.`;
};
