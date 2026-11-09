// Data of the metric strip cards (§3.7): series over the last runs and the
// summary line `Latest #5A 85% +5.6 pts vs #1A · Best #2B 86.6%`. Pure.
import {
  getRunStatus,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";
import {
  LOW_SAMPLE_N,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import { fmtCost, fmtPct } from "src/sections/common/format/fmt";
import {
  STRIP_RUNS,
  aggValueOf,
  bestRunFor,
  costOf,
  isRunFinal,
  isUnrankedRun,
  latencyP50,
  latencyP99,
  stripRuns,
} from "./leaderboardModel";
import { costPerTask, fmtSecs1 } from "./leaderboardHeadline";
import { waitingPhraseOf } from "src/sections/common/Runs/runLedger";

/** URL view state of the strip: which score series, which percentile. */
export const CHART_VIEW_DEFAULTS = { chart_eval: "", chart_lat: "p50" };

// Validated with the dataviz palette checker (light: purple 500, orange 500,
// blue 500, green 500; dark: purple 400 + the same three). Fixed order.
export const SERIES_COLORS = {
  light: ["#7857FC", "#E9690C", "#348AEF", "#00A251"],
  dark: ["#856EFF", "#E9690C", "#348AEF", "#00A251"],
};
// One hue, light -> dark, for p50 / p99.
export const LATENCY_COLORS = {
  light: ["#78B4F9", "#276DBD"],
  dark: ["#559FF7", "#2E7CD6"],
};
export const MAX_SCORE_SERIES = 4;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** 0–100 on the shared Scores axis (pass rate, or a 0–1 score × 100). */
export function normalizedScore(run, def) {
  const v = aggValueOf(run, def);
  if (!isNum(v)) return null;
  return Math.round(v * 1000) / 10;
}

/** The evals the Scores card draws: the dataset evals first, at most 4. */
export function scoreDefs(metricDefs) {
  const evals = (metricDefs ?? []).filter((d) => d.kind === "eval");
  const own = evals.filter((d) => d.is_dataset_eval);
  return (own.length ? own : evals).slice(0, MAX_SCORE_SERIES);
}

export const errorRateOf = (run) => {
  const r = run?.metrics?.error_rate;
  return isNum(r) && (run?.progress?.done ?? 0) > 0 ? r : null;
};

/**
 * integration C5 (AG-09): the rows a run scored, for run-level numbers (Success,
 * cost, time, error rate): the most rows any of its metrics was measured on
 * (`aggregates[key].n`, the n= the eval cells show), else the rows done.
 */
export function scoredRowsOf(run) {
  const ns = Object.entries(run?.metrics?.aggregates ?? {})
    .filter(([k, a]) => !k.startsWith("human:") && isNum(a?.n))
    .map(([, a]) => a.n);
  if (ns.length) return Math.max(...ns);
  const done = run?.progress?.done;
  return isNum(done) ? done : null;
}

/**
 * `Low sample · n=20` when a number rests on fewer than LOW_SAMPLE_N rows:
 * the eval's own n with `def`, else the run's scored rows. Null otherwise.
 */
export function lowSampleNote(run, def) {
  const n = def ? run?.metrics?.aggregates?.[def.key]?.n : scoredRowsOf(run);
  return isNum(n) && n > 0 && n < LOW_SAMPLE_N ? `Low sample · n=${n}` : null;
}

/** Extra tooltip lines: running / partial / stopped / waiting / low sample. */
export function runNotes(run, def) {
  const notes = [];
  const status = getRunStatus(run);
  const p = runProgressOf(run);
  if (status.waiting) notes.push(`W${waitingPhraseOf(run).slice(1)}`);
  else if (status.active)
    notes.push(`${status.label} · ${p.done} of ${p.total} rows (partial)`);
  else if (status.key === "Cancelled")
    notes.push(`Stopped at ${p.done} of ${p.total} rows`);
  // integration C5: run-level points (Latency, Total cost, Error rate) flag on the
  // run's scored rows; a running run already says it is partial.
  const low =
    def || !(status.waiting || status.active) ? lowSampleNote(run, def) : null;
  if (low) notes.push(low);
  return notes;
}

/** The newest run with a value for `getter`, and the best one. */
export function summarize(runs, getter, { lowerIsBetter = false } = {}) {
  const withValue = (runs ?? []).filter((r) => isNum(getter(r)));
  if (!withValue.length) return null;
  const latest = withValue[withValue.length - 1];
  let best = withValue[0];
  withValue.forEach((r) => {
    const v = getter(r);
    const b = getter(best);
    if (lowerIsBetter ? v < b : v > b) best = r;
  });
  return { latest, best };
}

/** Best run on an eval honours the eval's direction. */
export function evalSummary(runs, def) {
  const withValue = (runs ?? []).filter((r) => isNum(aggValueOf(r, def)));
  if (!withValue.length) return null;
  return {
    latest: withValue[withValue.length - 1],
    best: bestRunFor(withValue, def) ?? withValue[0],
  };
}

export const latencyGetters = { p50: latencyP50, p99: latencyP99 };
export { costOf };

/**
 * EL#22: an auto-zoomed 0–100 axis for one score series: pad 5 points
 * around the data, snap to tens, never past 0 or 100. `zoomed` is true when
 * the axis is narrower than 0–100 (the card says so).
 */
export function zoomRange(
  values,
  { pad = 5, step = 10, lo = 0, hi = 100 } = {},
) {
  const v = (values ?? []).filter(
    (x) => typeof x === "number" && Number.isFinite(x),
  );
  if (!v.length) return { min: lo, max: hi, zoomed: false };
  const min = Math.max(lo, Math.floor((Math.min(...v) - pad) / step) * step);
  const max = Math.min(hi, Math.ceil((Math.max(...v) + pad) / step) * step);
  const safeMax = max - min < step ? Math.min(hi, min + step) : max;
  return { min, max: safeMax, zoomed: min > lo || safeMax < hi };
}

/** True for the task-success eval (the success rate of a task suite). */
export const isSuccessDef = (def) =>
  def?.key === "eval:task_success" || /^task success$/i.test(def?.name ?? "");

/** The eval the Scores card leads with (`chart_eval`, else the first). */
export function primaryScoreDef(metricDefs, scoreKey) {
  const defs = scoreDefs(metricDefs);
  if (!scoreKey || scoreKey === "all") return defs[0] ?? null;
  return defs.find((d) => d.key === scoreKey) ?? defs[0] ?? null;
}

/**
 * A2-01: the collapsed Charts section's one line, from the runs the cards
 * chart (the last STRIP_RUNS) and the same getters and formatters:
 * `best score #2D 91.0% · fastest #1A 6.0 s · cheapest #2A $0.0040 ·
 * errors 0%`. A clause without data is left out; null when none has data.
 * `cheapest` is per task (the grid's Cost per task: fair across partial
 * runs); `errors` is the highest error rate on the chart.
 */
export function chartsSummary(
  runs,
  metricDefs,
  { scoreKey, best, filtered } = {},
) {
  // IT-R5 G2 P6: the strip's best / cheapest / fastest rank voice runs
  // only (a text-only run is not ranked). L7c g5 (kept:so-far-headline):
  // and finished runs only, like the answer line: a run still playing
  // (#9A at 0/248, `cheapest $0.00026`) is never crowned on a partial number.
  const strip = stripRuns(runs, STRIP_RUNS).filter(
    (r) => !isUnrankedRun(r) && isRunFinal(r),
  );
  const parts = [];
  const primary = primaryScoreDef(metricDefs, scoreKey);
  const score = primary ? evalSummary(strip, primary) : null;
  // IT-R3 G2 P5: three facts — best · cheapest · fastest. IT-R3 G2 P3:
  // `best` is the leaderboard's one rule (bestLikeForLike: the best success
  // among runs on every task of the baseline's version); a score eval
  // names itself so it never reads as the Success rate.
  const every = Number.isFinite(filtered) ? ` (all ${filtered} runs)` : "";
  if (best?.label && (!primary || isSuccessDef(primary)))
    parts.push(`best${every} ${best.label} ${fmtPct(best.rate)}`);
  else if (score)
    parts.push(
      `best ${primary.name ?? "score"} ${score.best.label} ${formatValue(primary, aggValueOf(score.best, primary))}`,
    );
  const reported = (r) => {
    const c = costPerTask(r);
    return c > 0 ? c : null;
  };
  const cheap = summarize(strip, reported, { lowerIsBetter: true });
  if (cheap)
    parts.push(`cheapest ${cheap.best.label} ${fmtCost(reported(cheap.best))}`);
  const fast = summarize(strip, latencyP50, { lowerIsBetter: true });
  if (fast)
    parts.push(
      `fastest ${fast.best.label} ${fmtSecs1(latencyP50(fast.best) * 1000)}`,
    );
  return parts.length ? parts.join(" · ") : null;
}
