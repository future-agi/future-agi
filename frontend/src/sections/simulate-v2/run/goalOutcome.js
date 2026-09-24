import { isMeasured } from "../_mock/failures";
/**
 * Goal outcome — the business-level "did this call achieve its
 * purpose?" signal. Sits above pass/fail: an eval that passes on a
 * call that never converted is still a lost sale.
 *
 * Per-env definition (Contract-level). Falls back to a sensible
 * default derived from the surface + use case if nothing is set.
 *
 * Values:
 *   "achieved"  — the pitch landed / task complete (sale, refund, etc.)
 *   "abandoned" — caller hung up / stopped engaging mid-way
 *   "declined"  — caller heard the pitch and said no
 *   "n/a"       — no goal defined or task didn't reach the pitch
 */

export const GOAL_OUTCOMES = ["achieved", "abandoned", "declined", "n/a"];

export const GOAL_OUTCOME_LABELS = {
  achieved:  "Goal achieved",
  abandoned: "Abandoned mid-call",
  declined:  "Pitch declined",
  "n/a":     "N/A",
};

export const GOAL_OUTCOME_COLORS = {
  achieved:  "#16A34A",
  abandoned: "#F59E0B",
  declined:  "#DC2626",
  "n/a":     "#94A3B8",
};

/**
 * Human-readable name for the goal itself — depends on the env's
 * surface / domain. In production this would be read from the
 * env's Contract; here we derive a sensible default.
 */
export function goalName(env) {
  const domain = env?.domain || env?.contract?.domain || "";
  if (/refund|return/i.test(env?.name || "") || /refund/i.test(domain)) return "Refund resolved";
  if (/sale|lead|sales/i.test(env?.name || "") || /sales/i.test(domain)) return "Sale made";
  if (/support|help/i.test(env?.name || ""))     return "Issue resolved";
  if (/booking|appointment/i.test(env?.name || "")) return "Appointment booked";
  if (env?.surface === "voice") return "Call resolved";
  return "Task completed";
}

/**
 * Derive goal outcome for a task. In production the agent / env
 * would emit this directly; here we approximate from the existing
 * signals so every panel that talks about goal outcome has data
 * to render.
 *
 * Heuristic:
 *   - passing + not escalated                    → achieved
 *   - error / timeout / turn-limit reached       → abandoned
 *   - failed + escalated                          → abandoned (they gave up on the pitch)
 *   - explicitly failed on task_success grader   → declined
 *   - anything else                               → declined (default losing state)
 */
export function deriveGoalOutcome(task) {
  if (!task) return "n/a";
  if (task.goalOutcome && GOAL_OUTCOMES.includes(task.goalOutcome)) return task.goalOutcome;
  /* No verdict — the environment, connection, simulated caller or grader
     broke before the agent could be judged. Not a declined pitch. */
  if (!isMeasured(task)) return "n/a";
  if (task.status === "passed" && !task.escalated) return "achieved";
  if (task.status === "error") return "abandoned";
  const turns = task.steps?.length || 0;
  if (turns >= 12) return "abandoned";
  if (task.escalated) return "abandoned";
  return "declined";
}

/** Aggregate goal-outcome counts + rate for a task list. */
export function deriveGoalMetrics(tasks) {
  const total = (tasks || []).length;
  const byOutcome = { achieved: 0, abandoned: 0, declined: 0, "n/a": 0 };
  (tasks || []).forEach((t) => { byOutcome[deriveGoalOutcome(t)] += 1; });
  const measured = total - byOutcome["n/a"];
  const rate = measured ? Math.round((byOutcome.achieved / measured) * 100) : 0;
  return { total, measured, byOutcome, achievedRate: rate };
}
