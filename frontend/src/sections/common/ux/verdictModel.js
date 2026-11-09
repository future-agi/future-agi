// Verdict sentence (R3 G0 §B2, SVP-P0-1a): the answer first, in one line.
// Pure: no React. VerdictLine renders it; lanes never write their own.
//
//   #2D the coding harness Code beats baseline #2B: +25.4 pts Task success across 30
//   tasks · improved 8 · worse 1 · 19× cost · 64 s slower. Confidence:
//   medium (30 tasks × 3 tries).
//
// IT-R1 G2 P1: one sentence shape on every surface. `worse` is a term (tasks
// whose success dropped — not the `Any eval worse` filter); `improved` /
// `worse` come from runNumbers.compareRuns when the caller has it (the
// matrix scope, verdictScope.scopedVerdict); the scope (`across 30 tasks`)
// is a part of its own (`role: "scope"`). Time within 5% (or 500 ms) of the
// baseline reads `same time`; cost within 5% reads `same cost`, so two
// sources of a p50 never print `same time` here and `100 ms faster` there.
//
// Rules
// - pts = the API's `delta` when sent, else the difference of the two
//   displayed 1-dp percents (fmt.ptsBetween), so the numbers add up on screen.
// - significant = (integration C3) the paired 95% CI of the difference
//   (`success.ci95Pts`, results-math compareRuns) does not hold 0; without
//   it the API's `significant` when sent; else the two Wilson 95% intervals
//   (runStats.wilson) do not overlap. `interval` carries {lo, hi} either way.
// - tone: `better` (success up, significant, no quality metric significantly
//   worse); `worse` (success down, significant); `tie` (not significant);
//   `mixed` (success up and significant, but a quality metric in `others`
//   is significantly worse — verb `is mixed vs` — OR cost ≥ 1.5× / time
//   ≥ +50%: a trade-off, verb stays `beats`, the dot turns amber).
// - confidence counts TASKS: high = significant and ≥ 50 tasks; medium =
//   significant and < 50; low = not significant.
import { wilson } from "src/sections/common/Analysis/runStats";
import {
  fmtDeltaMs,
  fmtMultiple,
  fmtPts,
  ptsBetween,
} from "src/sections/common/format/fmt";

export const VERDICT_TONES = Object.freeze(["better", "mixed", "tie", "worse"]);
const COST_TRADEOFF = 1.5;
const TIME_TRADEOFF = 1.5;
/** Below this share of the baseline (or 500 ms) a time / cost reads `same`. */
const SAME_SHARE = 0.05;
const SAME_MS = 500;
export const HIGH_CONFIDENCE_TASKS = 50;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

const runText = (run) => {
  if (!run) return "";
  if (typeof run === "string") return run;
  return [run.label, run.name].filter(Boolean).join(" ");
};
const runLabelOf = (run) =>
  typeof run === "string" ? run : run?.label ?? run?.name ?? "";

/** Significance of a success difference (API value wins). */
export function successSignificant(success = {}) {
  if (typeof success.significant === "boolean") return success.significant;
  const { cand, base } = success;
  if (!isNum(cand) || !isNum(base)) return false;
  const tries = success.tries ?? 1;
  const nc = success.candN ?? (success.n ?? 0) * tries;
  const nb = success.baseN ?? (success.n ?? 0) * tries;
  if (!nc || !nb) return false;
  const a = wilson(cand, nc);
  const b = wilson(base, nb);
  return a.low > b.high || b.low > a.high;
}

/**
 * integration C3: one end of a 95% CI in points, 1 dp, U+2212 for a minus:
 * `0.5`, `12.1`, `\u22123.1`, `0.0`. Every interval on the verdict card,
 * the Experiments answer and their tooltips prints through it.
 */
export function ciPts(v) {
  const r = Math.round(v * 10) / 10;
  if (Object.is(r, -0) || r === 0) return "0.0";
  return r > 0 ? r.toFixed(1) : `\u2212${Math.abs(r).toFixed(1)}`;
}

/** `95% CI 0.5 to 12.1 pts` for an interval `{lo, hi}` (integration C3). */
export function ciText(iv) {
  return `95% CI ${ciPts(iv.lo)} to ${ciPts(iv.hi)} pts`;
}

/**
 * integration C3 (review M1): a served paired verdict — R1 `success_rate.
 * vs_baseline` or R3 `success_vs_baseline[run]`, `{baseline_run_id,
 * delta_pts, ci95_pts, n_paired, significant}`, computed by the ONE paired
 * estimator on the server — as verdictOf's success fields `{deltaPts,
 * ci95Pts, nPaired, significant}`. Null when it was judged against another
 * baseline than `baseId`, or is missing. Reads; never computes an interval.
 */
export function servedSuccessOf(served, baseId) {
  if (!served || !isNum(served.ci95_pts) || !isNum(served.delta_pts))
    return null;
  if (baseId && served.baseline_run_id && served.baseline_run_id !== baseId)
    return null;
  return {
    deltaPts: served.delta_pts,
    ci95Pts: served.ci95_pts,
    nPaired: isNum(served.n_paired) ? served.n_paired : null,
    significant: Boolean(served.significant),
  };
}

/**
 * integration C3 (review 2, M1-residual): the served paired verdict of `candId`
 * vs `baseId` in an R3 matrix read made with `baseline=baseId`
 * (`success_vs_baseline`), or null.
 */
export function matrixPairedSuccess(matrix, baseId, candId) {
  if (!matrix || !baseId || !candId) return null;
  return servedSuccessOf(matrix.success_vs_baseline?.[candId], baseId);
}

/**
 * integration C3 (review 2, M1-residual): a runNumbers.compareRuns result that
 * carries the served paired verdict of the same pair — `significant` from
 * the paired interval, plus `ci95Pts` / `nPaired` — so every verdict built
 * from it (verdictScope, answerLines) decides with the interval the Summary
 * decides with. Only on the default scope and when the served centre is the
 * compare's own delta (the same cells); else the compare is returned as it
 * is.
 */
export function withPairedCompare(compare, paired) {
  if (!compare || !paired) return compare;
  if (compare.scope !== "all") return compare;
  if (!isNum(compare.deltaPts) || compare.deltaPts !== paired.deltaPts)
    return compare;
  return {
    ...compare,
    significant: paired.significant,
    ci95Pts: paired.ci95Pts,
    nPaired: paired.nPaired,
  };
}

/**
 * integration C3: the ONE interval of a success difference, in points.
 * - paired (`success.ci95Pts`, the per-task paired 95% CI from results-math
 *   compareRuns over the tasks both ran): `{lo, hi} = deltaPts ± ci95Pts`;
 * - else, only where no paired data exists, the two unpaired Wilson ranges:
 *   `lo = cand.low − base.high`, `hi = cand.high − base.low` (`paired:
 *   false`).
 * `significant` = the interval does not hold 0. Null without the inputs.
 * @returns {{lo: number, hi: number, half: number|null, paired: boolean,
 *   significant: boolean, n: number|null}|null}
 */
export function successInterval(success = {}, deltaPts = success.deltaPts) {
  if (isNum(success.ci95Pts) && isNum(deltaPts)) {
    const half = Math.abs(success.ci95Pts);
    const lo = deltaPts - half;
    const hi = deltaPts + half;
    return {
      lo,
      hi,
      half,
      paired: true,
      significant: lo > 0 || hi < 0,
      n: isNum(success.nPaired) ? success.nPaired : success.n ?? null,
    };
  }
  const { cand, base } = success;
  if (!isNum(cand) || !isNum(base)) return null;
  const tries = success.tries ?? 1;
  const nc = success.candN ?? (success.n ?? 0) * tries;
  const nb = success.baseN ?? (success.n ?? 0) * tries;
  if (!nc || !nb) return null;
  const a = wilson(cand, nc);
  const b = wilson(base, nb);
  const lo = (a.low - b.high) * 100;
  const hi = (a.high - b.low) * 100;
  return {
    lo,
    hi,
    half: null,
    paired: false,
    significant: lo > 0 || hi < 0,
    n: null,
  };
}

/** `{level, tasks, tries, text}`; text `medium (30 tasks × 3 tries)`. */
export function confidenceOf({ significant, tasks, tries, noun = "task" }) {
  const level = !significant
    ? "low"
    : (tasks ?? 0) >= HIGH_CONFIDENCE_TASKS
      ? "high"
      : "medium";
  const t = isNum(tasks) ? tasks : null;
  const k = isNum(tries) && tries > 1 ? tries : null;
  const detail =
    t == null
      ? ""
      : ` (${t} ${t === 1 ? noun : `${noun}s`}${k ? ` × ${k} tries` : ""})`;
  return { level, tasks: t, tries: k, text: `${level}${detail}` };
}

const ratio = (pair) =>
  pair && isNum(pair.cand) && isNum(pair.base) && pair.base > 0
    ? pair.cand / pair.base
    : null;

/**
 * The verdict of one candidate run against the baseline.
 * @param {{
 *   candidate: {label: string, name?: string}|string,
 *   baseline: {label: string, name?: string}|string,
 *   metrics: {
 *     success: {cand: number, base: number, n: number, tries?: number,
 *       significant?: boolean, delta?: number (API, 0–1 fraction),
 *       deltaPts?: number (points), candN?: number, baseN?: number},
 *     costPerTask?: {cand: number, base: number},
 *     timePerTask?: {cand: number, base: number},   // ms, p50
 *     others?: {name: string, significantlyWorse: boolean}[],
 *   },
 *   worse?: number,   // tasks that got worse
 *   better?: number,  // tasks that got better
 *   scopeText?: string, // `across 30 tasks` (runNumbers.compareRuns)
 *   baselineRole?: "baseline"|"previous version"|null, // IT-R2 G2 P5
 * }} input
 *   `metrics.success.name` (optional, default `success`): the metric word
 *   after the pts (`Task success` on a task suite).
 * @returns {{tone: "better"|"mixed"|"tie"|"worse", verb: string,
 *   sentence: string, confidence: {level, tasks, tries, text},
 *   chips: {key: string, label: string, tone: "good"|"bad"|"neutral"}[],
 *   parts: {t: "run"|"text"|"term", text: string, k?: string}[],
 *   deltaPts: number|null, significant: boolean, tradeoff: boolean}}
 */
export function verdictOf(input = {}) {
  const { candidate, baseline, metrics = {}, worse, better } = input;
  const success = metrics.success ?? {};
  // API deltas are fractions (0.254); deltaPts is already in points.
  const deltaPts = isNum(success.deltaPts)
    ? success.deltaPts
    : isNum(success.delta)
      ? Math.round(success.delta * 1000) / 10
      : ptsBetween(success.cand, success.base);
  // integration C3: with the paired CI the interval decides tie / better / worse;
  // without it the API's `significant`, else the unpaired Wilson ranges.
  const interval = successInterval(success, deltaPts);
  const significant = interval?.paired
    ? interval.significant
    : successSignificant(success);
  // The words beside an unpaired interval follow the decision.
  if (interval && !interval.paired) interval.significant = significant;
  const costX = ratio(metrics.costPerTask);
  const timeX = ratio(metrics.timePerTask);
  const timeDelta =
    metrics.timePerTask &&
    isNum(metrics.timePerTask.cand) &&
    isNum(metrics.timePerTask.base)
      ? metrics.timePerTask.cand - metrics.timePerTask.base
      : null;
  const qualityWorse = (metrics.others ?? []).some(
    (o) => o?.significantlyWorse,
  );
  const tradeoff =
    (costX != null && costX >= COST_TRADEOFF) ||
    (timeX != null && timeX >= TIME_TRADEOFF);

  let tone;
  let verb;
  if (!significant || deltaPts == null || deltaPts === 0) {
    tone = "tie";
    verb = "is level with";
  } else if (deltaPts < 0) {
    tone = "worse";
    verb = "trails";
  } else if (qualityWorse) {
    tone = "mixed";
    verb = "is mixed vs";
  } else {
    tone = tradeoff ? "mixed" : "better";
    verb = "beats";
  }

  const ptsText = fmtPts(deltaPts);
  const metricName = success.name ?? "success";
  const first = `${ptsText} ${metricName}${tone === "tie" ? " (within noise)" : ""}`;
  const chips = [
    {
      key: "success",
      label: `${ptsText} ${metricName}`,
      tone: tone === "tie" ? "neutral" : deltaPts > 0 ? "good" : "bad",
    },
  ];
  const tail = [];
  if (costX != null) {
    const same = Math.abs(costX - 1) < SAME_SHARE;
    // Far cheaper reads `50× cheaper`, never `0.0× cost`.
    const label = same
      ? "same cost"
      : costX < 0.1 && costX > 0
        ? `${fmtMultiple(1 / costX)} cheaper`
        : `${fmtMultiple(costX)} cost`;
    tail.push(label);
    chips.push({
      key: "cost",
      label,
      tone: same ? "neutral" : costX > 1 ? "bad" : "good",
    });
  }
  if (timeDelta != null) {
    const base = metrics.timePerTask.base;
    const same =
      Math.abs(timeDelta) < SAME_MS ||
      (isNum(base) && base > 0 && Math.abs(timeDelta) / base < SAME_SHARE);
    const label = same
      ? "same time"
      : fmtDeltaMs(timeDelta, { style: "words" });
    tail.push(label);
    chips.push({
      key: "time",
      label,
      tone: same ? "neutral" : timeDelta > 0 ? "bad" : "good",
    });
  }
  if (isNum(worse))
    chips.push({
      key: "worse",
      label: worse === 0 ? "None worse" : `${worse} worse`,
      tone: worse === 0 ? "good" : "bad",
    });
  const confidence = confidenceOf({
    significant,
    tasks: success.n,
    tries: success.tries,
    noun: success.noun,
  });

  const cand = runText(candidate);
  const base = runLabelOf(baseline);
  const counts = [];
  if (isNum(better))
    counts.push({ t: "text", text: ` · improved ${better} · ` });
  else if (isNum(worse)) counts.push({ t: "text", text: " · " });
  if (isNum(worse))
    counts.push({ t: "term", k: "worse", text: `worse ${worse}` });
  // IT-R2 G2 P5 (additive `baselineRole`): `baseline` names only the
  // dataset's pinned run. Another reference reads `#2G beats #2B (previous
  // version): …` or plain `#3B is level with #3A: …`. Default unchanged.
  const role =
    input.baselineRole === undefined ? "baseline" : input.baselineRole;
  const parts = [
    { t: "run", text: cand },
    {
      t: "text",
      text: role === "baseline" ? ` ${verb} baseline ` : ` ${verb} `,
    },
    { t: "run", text: base },
    {
      t: "text",
      text: role && role !== "baseline" ? ` (${role}): ` : ": ",
    },
    { t: "term", k: tone === "tie" ? "withinNoise" : "delta", text: first },
    {
      t: "text",
      role: "scope",
      text: input.scopeText ? ` ${input.scopeText}` : "",
    },
    ...counts,
    { t: "text", text: `${tail.length ? ` · ${tail.join(" · ")}` : ""}. ` },
    { t: "term", k: "confidence", text: "Confidence" },
    { t: "text", text: `: ${confidence.text}.` },
  ];
  const sentence = parts.map((p) => p.text).join("");
  return {
    tone,
    verb,
    sentence,
    confidence,
    chips,
    parts,
    deltaPts,
    significant,
    interval,
    tradeoff,
    worse: isNum(worse) ? worse : null,
    better: isNum(better) ? better : null,
  };
}

/**
 * IT-R5 G2 P5 (rule 37, seam S1): the verdict words, one place. `tie` is
 * `Too close to call` (never `Level`), shown with a neutral grey chip.
 */
export const VERDICT_WORD = Object.freeze({
  better: "Better",
  worse: "Worse",
  mixed: "Mixed",
  tie: "Too close to call",
});

/**
 * IT-R2 G2 P3: the one word a verdict chip shows, from the task counts the
 * sentence prints: `Mixed` ONLY when some tasks got better AND some got
 * worse; else `Better` / `Worse` by the success change, `Too close to
 * call` within noise. A cost or time trade-off never turns `Better` into `Mixed` (the
 * dot's `tone` still marks it).
 * @param {{tone?: string, better?: number|null, worse?: number|null,
 *   deltaPts?: number|null}} v  verdictOf() result (or the same fields)
 * @returns {"Better"|"Worse"|"Mixed"|"Too close to call"}
 */
export function verdictWord(v) {
  const better = isNum(v?.better) ? v.better : null;
  const worse = isNum(v?.worse) ? v.worse : null;
  if (v?.tone === "tie") return VERDICT_WORD.tie;
  if (better != null && worse != null && better > 0 && worse > 0)
    return "Mixed";
  if (v?.tone === "worse" || (isNum(v?.deltaPts) && v.deltaPts < 0))
    return "Worse";
  return "Better";
}

const TONE_RANK = { better: 0, mixed: 1, tie: 2, worse: 3 };

/** Verdicts best first: tone, then pts delta (desc). */
export function sortVerdicts(items) {
  const v = (x) => x?.verdict ?? x;
  return [...(items ?? [])].sort((a, b) => {
    const ra = TONE_RANK[v(a)?.tone] ?? 9;
    const rb = TONE_RANK[v(b)?.tone] ?? 9;
    if (ra !== rb) return ra - rb;
    return (v(b)?.deltaPts ?? -Infinity) - (v(a)?.deltaPts ?? -Infinity);
  });
}
