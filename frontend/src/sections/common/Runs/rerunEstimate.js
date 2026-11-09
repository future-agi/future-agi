// IT-R5 G6 P5: one rerun estimator. The Rerun menu (rerunPlan.js) and the
// Rerun-with-changes dialog both read it, with one vocabulary:
//   menu  `Failed tasks (49)` → `49 tasks · 147 tries · ~$1.79 · ~2 min`
//   dialog                      `49 tasks · 147 tries · ~$1.79 · ~2 min`
// Cost and time come from the run's own tries (cost and wall time per try,
// run `concurrency` at a time). A seeded run recorded outside Future AGI
// (CI, upload) was paid where it ran: its estimate reads
// `cost not tracked by Future AGI`.
// Pure.
import { fmtCost, fmtCount } from "src/sections/common/format/fmt";
import { provenanceOf } from "./provenanceModel";

const finite = (v) =>
  v !== null && v !== "" && Number.isFinite(Number(v)) ? Number(v) : null;

/** True when the run executes on the user's CI / upload runner. */
export function runnerPays(run) {
  if (!run) return false;
  const c = run.config ?? {};
  if (c.execution?.mode === "ci" || c.code?.mode === "upload") return true;
  if ((c.kind ?? run.kind) === "upload") return true;
  try {
    return run.config ? provenanceOf(run)?.where?.mode === "ci" : false;
  } catch {
    return false;
  }
}

/** `~2 min` / `~1 h 10 min` / `under a minute` (null when unknown). */
export function aboutMinutes(ms) {
  const v = finite(ms);
  if (v == null || v <= 0) return null;
  const min = Math.round(v / 60000);
  if (min < 1) return "under a minute";
  if (min < 60) return `~${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `~${h} h${m ? ` ${m} min` : ""}`;
}

/**
 * @param {object} p
 * @param {number} p.rows         rows (tasks) in scope
 * @param {number} p.trials       tries per row
 * @param {number} [p.costPerTry] the run's own mean cost per try (USD)
 * @param {number} [p.msPerTry]   the run's own mean wall time per try
 * @param {number} [p.concurrency=1]  tries run this many at a time
 * @param {boolean} [p.runnerPaid]    the user's runner pays (CI / upload)
 * @param {number} [p.maxCostPerTry]  a per-try cost cap: a try stops there,
 *   so it never costs more (options-truth L5 G2)
 * @returns {{rows, trials, tries, cost: number|null, ms: number|null,
 *   runnerPaid: boolean}}
 */
export function rerunEstimate({
  rows,
  trials = 1,
  costPerTry,
  msPerTry,
  concurrency = 1,
  runnerPaid = false,
  maxCostPerTry,
} = {}) {
  const r = Math.max(0, Math.round(finite(rows) ?? 0));
  const t = Math.max(1, Math.round(finite(trials) ?? 1));
  const tries = r * t;
  // per try = min(the model's estimate, the per-try cap), then × tries.
  const cap = finite(maxCostPerTry);
  const est = finite(costPerTry);
  const c = est != null && cap != null && cap > 0 ? Math.min(est, cap) : est;
  const m = finite(msPerTry);
  const at = Math.max(1, Math.round(finite(concurrency) ?? 1));
  return {
    rows: r,
    trials: t,
    tries,
    cost: runnerPaid || c == null ? null : c * tries,
    ms: m == null ? null : (m * tries) / at,
    runnerPaid: Boolean(runnerPaid),
  };
}

/**
 * Several runs rerun side by side: rows and tries add up, cost adds up
 * (unknown when a run's is unknown), time is the slowest run's.
 */
export function sumEstimates(list) {
  const xs = (list ?? []).filter(Boolean);
  const paidHere = xs.filter((e) => !e.runnerPaid);
  const costs = paidHere.map((e) => e.cost);
  return {
    rows: xs.reduce((a, e) => a + e.rows, 0),
    trials: null,
    tries: xs.reduce((a, e) => a + e.tries, 0),
    cost:
      paidHere.length && costs.every((v) => v != null)
        ? costs.reduce((a, b) => a + b, 0)
        : null,
    ms: xs.some((e) => e.ms != null)
      ? Math.max(0, ...xs.map((e) => e.ms ?? 0))
      : null,
    runnerPaid: xs.length > 0 && xs.every((e) => e.runnerPaid),
    someRunnerPaid: xs.some((e) => e.runnerPaid),
  };
}

/**
 * `49 tasks · 147 tries · ~$1.79 · ~2 min`; a run paid outside Future AGI
 * reads `… · cost not tracked by Future AGI`. Unknown parts drop.
 */
export function estimateText(e, noun = "row", { rows = true } = {}) {
  if (!e) return "";
  const cost = e.runnerPaid
    ? "cost not tracked by Future AGI"
    : e.cost != null && e.cost > 0
      ? `~${fmtCost(e.cost)}${e.someRunnerPaid ? " + untracked cost" : ""}`
      : null;
  return [
    rows ? fmtCount(e.rows, noun) : null,
    fmtCount(e.tries, "try", "tries"),
    cost,
    aboutMinutes(e.ms),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Picked row labels split into matched / unmatched (pure). */
export function matchPicked(picked = [], labels = []) {
  const known = new Map(labels.map((l) => [String(l).toLowerCase(), l]));
  const byNo = new Map(
    labels.map((l) => [/(\d+)\s*$/.exec(String(l))?.[1], l]).filter(([n]) => n),
  );
  const matched = [];
  const unmatched = [];
  picked.forEach((x) => {
    const k = String(x).trim();
    if (!k) return;
    const hit =
      known.get(k.toLowerCase()) ??
      (/^\D{0,4}\d+$/.test(k) ? byNo.get(/(\d+)$/.exec(k)[1]) : null);
    if (hit && !matched.includes(hit)) matched.push(hit);
    else if (!hit) unmatched.push(k);
  });
  return { matched, unmatched };
}
