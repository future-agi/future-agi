/**
 * One definition of what happened to a task, shared by every analytics
 * panel, the KPI strip, the regression banner and the widget engine.
 *
 *   passed      — every sample passed
 *   flaky       — some samples passed (passShare between 0 and 1)
 *   failed      — no sample passed
 *   unmeasured  — something upstream of the agent broke (environment,
 *                 transport, simulator, grader); there is no verdict, and
 *                 it is never counted as a failure of the agent
 *
 * Rates are the mean passShare over measured tasks — the same number the
 * runs table shows — so the analytics tab and the runs list cannot disagree.
 */
import { attribute, DOMAINS, flakySource, isMeasured } from "../_mock/failures";

export { isMeasured };

export const OUTCOMES = ["passed", "flaky", "failed", "unmeasured"];

export const OUTCOME_LABELS = {
  passed: "Passed",
  flaky: "Flaky",
  failed: "Failed",
  unmeasured: "Not measured",
};

export function outcomeOf(t) {
  if (!isMeasured(t)) return "unmeasured";
  if (t?.status === "passed") return "passed";
  if (t?.status === "flaky") return "flaky";
  return "failed";
}

/** Share of samples that passed; null when there is no verdict. */
export function passShareOf(t) {
  if (!isMeasured(t)) return null;
  if (typeof t?.passShare === "number") return t.passShare;
  return t?.status === "passed" ? 1 : 0;
}

export const measuredOnly = (tasks = []) => tasks.filter(isMeasured);

/** Mean passShare over measured tasks, 0–100; null when nothing was measured. */
export function passRateOf(tasks = []) {
  const shares = tasks.map(passShareOf).filter((v) => v != null);
  return shares.length ? (shares.reduce((a, b) => a + b, 0) / shares.length) * 100 : null;
}

/** Counts per outcome, plus how many samples (calls / chats) were placed. */
export function outcomeTally(tasks = []) {
  const tally = { passed: 0, flaky: 0, failed: 0, unmeasured: 0 };
  tasks.forEach((t) => { tally[outcomeOf(t)] += 1; });
  return { ...tally, measured: tasks.length - tally.unmeasured, total: tasks.length };
}

/**
 * Which layer owns a non-passing task. Unmeasured tasks carry their fault
 * domain; a flaky task whose inconsistency came from the simulated caller
 * drifting is the simulator's, not the agent's; everything else measured is
 * the agent's. Passed tasks have no owner (null).
 */
export function attributionOf(t) {
  const outcome = outcomeOf(t);
  if (outcome === "passed") return null;
  if (outcome === "unmeasured") return attribute(t);
  if (outcome === "flaky" && flakySource(t) === "simulator") return DOMAINS.simulator;
  return DOMAINS.agent;
}

/** Samples actually placed for a task (a scenario runs `repeats` times). */
export const samplesOf = (t) => Math.max(1, Number(t?.repeats) || 1);

/* ── derived categories (one formula, used by every chart and copy) ── */

const hashId = (id) => {
  const s = String(id || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
};

/* Sentiment and end reason come from the evaluator/platform when a task
   carries them; the prototype's tasks don't, so a stable stand-in is derived.
   Unmeasured tasks have none — the caller never reached a real conversation. */
export function sentimentOf(t) {
  if (t?.sentiment) return t.sentiment;
  const outcome = outcomeOf(t);
  if (outcome === "unmeasured") return null;
  if (outcome === "passed") return hashId(t.id) % 5 === 0 ? "neutral" : "positive";
  return hashId(t.id) % 3 === 0 ? "negative" : "neutral";
}

export function endReasonOf(t) {
  if (t?.endReason) return t.endReason;
  if (t?.fault?.transport) return "dropped";
  if (t?.fault?.environment || t?.fault?.simulator || t?.fault?.grading) return "error";
  if (outcomeOf(t) === "passed") return "complete";
  const h = hashId(t?.id) % 3;
  if (h === 0) return "timeout";
  if (h === 1) return "escalated";
  return "incomplete";
}
