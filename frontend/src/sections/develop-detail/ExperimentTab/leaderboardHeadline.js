// R3 P3 (SVP-P0-2b, SVP-4.3, EL#1): the leaderboard's locked headline
// numbers and its one answer line. Pure; the grid, the answer line and the
// tests share it, so a value on screen is computed once.
//
// - Success rate = R1 `success_rate {passed, total}` (pass@1 with the Matrix
//   rule, computed by the API; the browser never recomputes it).
// - Cost per task = total cost / tasks done; Time per task = p50 latency.
// - The answer line reads only FINISHED runs (Completed / Completed with
//   errors, every task done): a partial or stopped run never headlines.
import {
  VERDICT_WORD,
  ciText,
  servedSuccessOf,
  successInterval,
  verdictWord,
} from "src/sections/common/ux/verdictModel";
import { successVerdict } from "src/sections/common/Runs/oneVerdict";
import {
  MINUS,
  fmtCost,
  fmtMs,
  fmtMultiple,
  fmtNumber,
  fmtPct,
  fmtPts,
} from "src/sections/common/format/fmt";
import {
  diffRange95,
  extraCostPerSolved,
  ptsDelta,
} from "src/sections/common/Runs/runNumbers";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { waitNounOf } from "src/sections/common/Runs/runLedger";
import { isUnrankedRun } from "./leaderboardModel";

// L7c g5: several waiting runs share one noun when they wait the same way.
const waitNounOfAllRuns = (runs) => {
  const nouns = [...new Set((runs ?? []).map((r) => waitNounOf(r)))];
  return nouns.length === 1 ? nouns[0] : "their results";
};

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** `{passed, total, rate}` or null when the run has nothing scored yet. */
export function successOf(run) {
  const s = run?.success_rate;
  if (!s || !isNum(s.total) || s.total <= 0) return null;
  return {
    passed: s.passed ?? 0,
    total: s.total,
    rate: (s.passed ?? 0) / s.total,
  };
}

/**
 * integration C3: the served paired verdict of `cand` vs `baseId` —
 * the run list's `success_rate.vs_baseline` (experimentsList.js, the ONE
 * paired estimator the Summary reads) — as verdictOf's success fields
 * `{deltaPts, ci95Pts, nPaired, significant}`. Null when the run list
 * judged it against another baseline, or could not pair it. The one reader
 * is verdictModel.servedSuccessOf (R1 and R3 serve the same shape).
 */
export const servedPairedSuccess = servedSuccessOf;

/**
 * integration C3: the paired interval a compare already carries —
 * the Summary's `tasks_vs_baseline.compare` (analysis-model, `ci95Pts`,
 * `nPaired`, `significant`), which the dataset Summary's verdict card
 * hands to answerLines. A runNumbers.compareRuns result has no `ci95Pts`
 * and gives null.
 */
function pairedOfCompare(compare) {
  if (!isNum(compare?.ci95Pts) || !isNum(compare?.deltaPts)) return null;
  if (typeof compare.significant !== "boolean") return null;
  return {
    deltaPts: compare.deltaPts,
    ci95Pts: compare.ci95Pts,
    nPaired: isNum(compare.nPaired) ? compare.nPaired : null,
    significant: compare.significant,
  };
}

/** Total cost / tasks done (USD); null without a cost or a finished task. */
export function costPerTask(run) {
  // L7c g5 (lb:compare): the unrounded total (the compare page's), not the
  // cent-rounded `cost_usd`, divided by the rows done.
  const raw = run?.metrics?.aggregates?.["ops:cost"]?.sum;
  const c = isNum(raw) ? raw : run?.metrics?.cost_usd;
  const n = run?.progress?.done;
  return isNum(c) && isNum(n) && n > 0 ? c / n : null;
}

/** p50 wall time of one task, in ms. */
export function timePerTaskMs(run) {
  const s = run?.metrics?.latency_p50_s;
  return isNum(s) ? s * 1000 : null;
}

/**
 * Relative change `+1,809%` / `−64%` (grouped, whole percent, U+2212);
 * null when either side is missing or the base is 0.
 */
export function pctChangeText(value, base) {
  if (!isNum(value) || !isNum(base) || base === 0) return null;
  // IT-R4 G2 P7: 2× or more reads as a multiple (`19×`), never `+1,809%`;
  // L2 G2 (consistency #11): the `×` glyph, as every other multiple.
  if (base > 0 && value / base >= 2) {
    const r = value / base;
    return `${r >= 10 ? Math.round(r) : Math.round(r * 10) / 10}×`;
  }
  const pct = Math.round(((value - base) / Math.abs(base)) * 100);
  if (pct === 0) return "±0%";
  return `${pct > 0 ? "+" : MINUS}${fmtNumber(Math.abs(pct))}%`;
}

/**
 * A duration rounded the way fmtMs prints it (`74 s`, `9.8 s`, `842 ms`),
 * so a delta is the difference of the two DISPLAYED values: `74 s` vs
 * `9.8 s` reads `+64 s`, never `+65 s` from the raw 74.35 s.
 */
export function shownMs(ms) {
  if (!isNum(ms)) return null;
  const a = Math.abs(ms);
  const r =
    Math.round(a) < 1000
      ? Math.round(a)
      : Number((a / 1000).toFixed(1)) < 10
        ? Number((a / 1000).toFixed(1)) * 1000
        : Math.round(a / 1000) * 1000;
  return ms < 0 ? -r : r;
}

/**
 * IT-R2 G2 P8: the leaderboard's time column always in seconds with one
 * decimal (`0.2 s`, `9.8 s`, `50.0 s`), so a voice table never mixes
 * `190 ms` with `50 s`. `signed` adds `+` / `−` for a delta.
 */
export function fmtSecs1(ms, { signed = false } = {}) {
  if (!isNum(ms)) return null;
  const v = Math.abs(ms) / 1000;
  const body = `${v.toFixed(1)} s`;
  if (!signed) return ms < 0 ? `${MINUS}${body}` : body;
  if (Number(v.toFixed(1)) === 0) return "±0.0 s";
  return `${ms > 0 ? "+" : MINUS}${body}`;
}

/** Success-rate points vs the baseline (one delta: runNumbers.ptsDelta). */
export function successPts(run, baseline) {
  const a = successOf(run);
  const b = successOf(baseline);
  if (!a || !b) return null;
  return ptsDelta(a.rate, b.rate);
}

/** Finished = terminal, not stopped / failed, every task done. */
export function isFinished(run) {
  const st = getRunStatus(run);
  if (st.key !== "Completed" && st.key !== "PartialCompleted") return false;
  const p = run?.progress;
  if (p && isNum(p.total) && p.total > 0 && (p.done ?? 0) < p.total)
    return false;
  return !!successOf(run);
}

const isActive = (run) => {
  const st = getRunStatus(run);
  return st.active || st.waiting;
};

/**
 * IT-R3 G2 P1: like for like = the run ran the baseline's dataset version
 * AND every task the baseline ran. A run on an older version (#1D: v1, 24
 * of 30 tasks) never headlines while a like-for-like run exists.
 * @returns {{sameVersion: boolean, full: boolean, likeForLike: boolean,
 *   version: number|null, done: number|null, total: number|null}}
 */
export function coverageOf(run, baseline) {
  const v = run?.dataset_version?.number ?? null;
  const bv = baseline?.dataset_version?.number ?? null;
  const sameVersion = v == null || bv == null || v === bv;
  const done = run?.progress?.total;
  const total = baseline?.progress?.total;
  const full = !isNum(done) || !isNum(total) || done >= total;
  return {
    sameVersion,
    full,
    likeForLike: sameVersion && full,
    version: v,
    done: isNum(done) ? done : null,
    total: isNum(total) ? total : null,
  };
}

/**
 * IT-R3 G2 P3: the one `best` rule on the tab, the Charts line and the
 * dataset list — the highest success among FINISHED runs on every task of
 * the baseline's dataset version (a v1 run on 24 of 30 tasks never wins).
 * Falls back to every finished run when none is like for like.
 * @param {object[]} runs  every run on the dataset
 * @param {(run) => {rate: number}|null} rateOf  the leaderboard's numbers
 * @param {object|null} baseline
 * @returns {{label, rate, run, rule: string}|null}
 */
export function bestLikeForLike(runs, rateOf, baseline) {
  // IT-R5 G2 P6: a text-only voice run is never the best.
  const done = (runs ?? []).filter(
    (r) =>
      r &&
      r.listed !== false &&
      isFinished(r) &&
      !isUnrankedRun(r) &&
      rateOf(r)?.rate != null,
  );
  const like = done.filter((r) => coverageOf(r, baseline).likeForLike);
  const pool = like.length ? like : done;
  if (!pool.length) return null;
  const run = [...pool].sort(
    (a, b) =>
      rateOf(b).rate - rateOf(a).rate ||
      (costPerTask(a) ?? Infinity) - (costPerTask(b) ?? Infinity),
  )[0];
  const total = baseline?.progress?.total;
  const v = baseline?.dataset_version?.number;
  return {
    label: run.label,
    rate: rateOf(run).rate,
    run,
    rule: like.length
      ? `Best = the highest success (pass@1) among runs on all ${total ?? "the"} tasks${v != null ? ` of v${v}` : ""}.`
      : "Best = the highest success (pass@1) among finished runs.",
  };
}

/**
 * IT-R3 G2 P1: the order a run earns the headline in — finished and like
 * for like (0), finished but partial (1), still going with scored rows
 * (2, 3); a run with nothing scored never headlines (null).
 * @param {object} run
 * @param {object|null} baseline
 * @param {boolean} scored  the run has at least one scored row
 */
export function headlineRank(run, baseline, scored) {
  if (!scored) return null;
  const like = coverageOf(run, baseline).likeForLike;
  return (isFinished(run) ? 0 : 2) + (like ? 0 : 1);
}

/**
 * IT-R3 G2 P1 / S6: the ship line, same copy as the compare page —
 *   `Don't ship #2D Claude Code yet: it fails T25, which #2B passes.`
 *   `No task got worse: review it, then ship.`
 * A partial candidate (another dataset version, fewer tasks) never reads
 * `ship`: `No task got worse on the 24 it ran: run it on all 30 before you
 * ship.` Null while either run is still going.
 * @param {object} compare  runNumbers.compareRuns()
 * @param {{cand: string, base: string, rowLabel: (n: number) => string,
 *   noun?: string, partial?: boolean, total?: number|null}} o
 * @returns {{text: string, tone: "worse"|"better"|"tie", rowId: string|null,
 *   rowText: string|null}|null}
 */
export function shipLine(
  compare,
  {
    cand,
    base,
    rowLabel,
    noun = "task",
    partial = false,
    total = null,
    candLabel = null,
    rowGroup = null,
  },
) {
  if (!compare || compare.partial) return null;
  const worse = (compare.rows ?? []).filter((r) => r.change === "worse");
  // IT-R4 G2 P8 (additive options): 3+ worse rows read as a count, with
  // what they share — `Don't ship #1B yet: 4 calls got worse (all with
  // Mike), which #1A handled.` [Open Mike · corrects order id]
  if (worse.length > 2 && (candLabel || rowGroup)) {
    const groups = rowGroup
      ? [...new Set(worse.map((r) => rowGroup(r.n)))]
      : [];
    const shared =
      groups.length === 1 && groups[0] ? ` (all with ${groups[0]})` : "";
    return {
      text: `Don't ship ${candLabel ?? cand} yet: ${worse.length} ${noun}s got worse${shared}, which ${base} handled.`,
      tone: "worse",
      rowId: worse[0].id,
      rowText: `Open ${rowLabel(worse[0].n)}`,
    };
  }
  if (worse.length) {
    const named = worse.slice(0, 2).map((r) => rowLabel(r.n));
    const more = worse.length - named.length;
    const which = more
      ? `${named.join(", ")} and ${more} more`
      : named.join(" and ");
    return {
      text: `Don't ship ${cand} yet: it fails ${which}, which ${base} passes.`,
      tone: "worse",
      rowId: worse[0].id,
      rowText: `Open ${named[0]}`,
    };
  }
  const both = (compare.rows ?? []).filter((r) => r.change !== "not_run");
  if (partial)
    return {
      text: `No ${noun} got worse on the ${both.length} it ran: run it on all ${total ?? compare.rows.length} before you ship.`,
      tone: "tie",
      rowId: null,
      rowText: null,
    };
  if ((compare.deltaPts ?? 0) < 0) return null;
  return {
    text: `No ${noun} got worse: review it, then ship.`,
    tone: "better",
    rowId: null,
    rowText: null,
  };
}

/**
 * IT-R4 G2 P5: `a` was made after `b` (later created_at; same moment →
 * the later letter of the same experiment). The headline calls it `New`.
 */
export function madeAfter(a, b) {
  if (!a || !b || a.run_id === b.run_id) return false;
  const ca = String(a.created_at ?? "");
  const cb = String(b.created_at ?? "");
  if (ca !== cb) return ca > cb;
  const letter = (r) => String(r.label ?? "").match(/^#\d+([A-Z]+)/)?.[1] ?? "";
  return (
    a.experiment_id === b.experiment_id &&
    letter(a).localeCompare(letter(b)) > 0
  );
}

const newestFirst = (a, b) =>
  String(b.created_at).localeCompare(String(a.created_at)) ||
  (b.experiment_ordinal ?? 0) - (a.experiment_ordinal ?? 0);

/**
 * IT-R4 G2 P1: `Latest vs baseline` — the pinned baseline and the newest
 * finished run that is not it. When the newest run is still going,
 * `skipped` names it (the tooltip says the newest finished one is used).
 * Spread into compareUrl (S2: runs, baseline, cand).
 * @returns {{runIds: string[], baselineId: string, candId: string,
 *   cand: object, base: object, skipped: object|null}|null}
 */
export function latestVsBaseline(runs = [], pinnedRun = null) {
  if (!pinnedRun?.run_id) return null;
  // Same moment (one experiment) → the later letter is the newer run.
  const others = runs
    .filter((r) => r && r.listed !== false && r.run_id !== pinnedRun.run_id)
    .sort(
      (a, b) =>
        newestFirst(a, b) ||
        String(b.label ?? "").localeCompare(String(a.label ?? "")),
    );
  const cand = others.find(isFinished);
  if (!cand) return null;
  const newest = others[0];
  return {
    runIds: [pinnedRun.run_id, cand.run_id],
    baselineId: pinnedRun.run_id,
    candId: cand.run_id,
    cand,
    base: pinnedRun,
    skipped:
      newest && newest.run_id !== cand.run_id && isActive(newest)
        ? newest
        : null,
  };
}

/**
 * The answer line's runs: the newest experiment with a finished run that is
 * not the baseline, its best finished run by Success rate (ties: cheaper per
 * task), plus every run still going (named, never headlined).
 * @returns {{candidate, baseline, running: object[], experimentId}|null}
 */
export function pickHeadline(runs = [], baselineRun = null) {
  const baseId = baselineRun?.run_id ?? null;
  const running = runs.filter(isActive);
  const finished = runs
    .filter((r) => r.run_id !== baseId && isFinished(r))
    .sort(newestFirst);
  if (!finished.length)
    return running.length
      ? { candidate: null, baseline: baselineRun, running, experimentId: null }
      : null;
  const experimentId = finished[0].experiment_id;
  const pool = finished.filter((r) => r.experiment_id === experimentId);
  const candidate = [...pool].sort(
    (a, b) =>
      successOf(b).rate - successOf(a).rate ||
      (costPerTask(a) ?? Infinity) - (costPerTask(b) ?? Infinity),
  )[0];
  return { candidate, baseline: baselineRun, running, experimentId };
}

// The sentence names the run the way people say it: `Claude Code`, not
// `Claude Code × claude-sonnet-4-5` (the model stays in the table).
const nameOf = (run) =>
  run?.target?.harness ??
  String(run?.name ?? run?.experiment_name ?? "").split(" × ")[0];
const runText = (run) => [run.label, nameOf(run)].filter(Boolean).join(" ");

function costClause(cand, base) {
  const c = costPerTask(cand);
  const b = costPerTask(base);
  if (!isNum(c) || !isNum(b) || b <= 0) return null;
  const ratio = c / b;
  if (ratio > 0.95 && ratio < 1.05) return "same";
  return fmtMultiple(c, b);
}

const runningText = (running) => {
  if (!running.length) return null;
  const list = (rs) =>
    `${rs
      .slice(0, 3)
      .map((r) => r.label)
      .join(", ")}${rs.length > 3 ? ` +${rs.length - 3}` : ""}`;
  const waiting = running.filter((r) => getRunStatus(r).waiting);
  const going = running.filter((r) => !getRunStatus(r).waiting);
  const parts = [];
  if (going.length)
    parts.push(
      `${going.length} ${going.length === 1 ? "run" : "runs"} still running (${list(going)})`,
    );
  if (waiting.length)
    parts.push(
      // L7c g5: the one waiting noun from the run ledger, never "your code".
      `${list(waiting)} ${waiting.length === 1 ? "waits" : "wait"} for ${waitNounOfAllRuns(waiting)}`,
    );
  return `${parts.join("; ")}.`;
};

/**
 * VerdictLine input for the leaderboard: verdictOf() for the tone (and its
 * noise rule), with the leaderboard's own sentence in `parts`:
 * `Best: #2D Claude Code 91.0% (+25.4 pts vs baseline #2B) at 19× the cost
 * per task.` Within noise adds `, within noise`; a candidate below the
 * baseline reads `No run beats baseline #2B (65.6%). Closest: …`.
 * @returns {{verdict, candidate, baseline, running}|null}
 */
export function headlineVerdict(runs = [], baselineRun = null) {
  const pick = pickHeadline(runs, baselineRun);
  if (!pick) return null;
  const { candidate, baseline, running } = pick;
  const tail = runningText(running);
  if (!candidate) {
    return {
      ...pick,
      verdict: {
        tone: "tie",
        parts: [
          { t: "text", text: `No finished run yet. ${tail ?? ""}`.trim() },
        ],
        sentence: `No finished run yet. ${tail ?? ""}`.trim(),
      },
    };
  }
  const cs = successOf(candidate);
  const bs =
    baseline && baseline.run_id !== candidate.run_id
      ? successOf(baseline)
      : null;
  const parts = [];
  const text = (t) => parts.push({ t: "text", text: t });
  let verdict;
  if (!bs) {
    verdict = { tone: "better" };
    text("Best: ");
    parts.push({ t: "run", text: runText(candidate) });
    text(` ${fmtPct(cs.rate)} `);
    parts.push({ t: "term", k: "successRate", text: "success rate" });
    text(` (${cs.passed}/${cs.total} tries).`);
  } else {
    const tasks = candidate.progress?.done ?? candidate.examples ?? null;
    // L2 G2 R4: the one verdict (one delta). integration C3: with the
    // run list's paired interval against this baseline, that interval
    // decides (the Summary's); else diffRange95 on the tasks.
    const served = servedPairedSuccess(
      candidate.success_rate?.vs_baseline,
      baseline.run_id,
    );
    verdict = successVerdict({
      candidate: { label: candidate.label, name: nameOf(candidate) },
      baseline: { label: baseline.label },
      metrics: {
        success: {
          cand: cs.rate,
          base: bs.rate,
          n: served?.nPaired ?? tasks ?? cs.total,
          tries: candidate.trials ?? 1,
          candN: cs.total,
          baseN: bs.total,
          ...(served ?? {}),
        },
        costPerTask: {
          cand: costPerTask(candidate),
          base: costPerTask(baseline),
        },
        timePerTask: {
          cand: timePerTaskMs(candidate),
          base: timePerTaskMs(baseline),
        },
      },
    });
    const pts = ptsDelta(cs.rate, bs.rate);
    const leads = pts >= 0;
    if (leads) {
      text("Best: ");
      parts.push({ t: "run", text: runText(candidate) });
      text(` ${fmtPct(cs.rate)} (`);
    } else {
      text("No run beats ");
      parts.push({ t: "term", k: "baseline", text: "baseline" });
      text(" ");
      parts.push({ t: "run", text: baseline.label });
      text(` (${fmtPct(bs.rate)}). Closest: `);
      parts.push({ t: "run", text: runText(candidate) });
      text(` ${fmtPct(cs.rate)} (`);
    }
    parts.push({ t: "term", k: "delta", text: fmtPts(pts) });
    if (leads) {
      text(" vs ");
      parts.push({ t: "term", k: "baseline", text: "baseline" });
      text(" ");
      parts.push({ t: "run", text: baseline.label });
    }
    if (!verdict.significant) {
      text(", ");
      parts.push({ t: "term", k: "withinNoise", text: "within noise" });
    }
    const cost = costClause(candidate, baseline);
    if (cost === "same") {
      text(") at the same ");
      parts.push({ t: "term", k: "costPerTask", text: "cost per task" });
      text(".");
    } else if (cost) {
      text(`) at ${cost} the `);
      parts.push({ t: "term", k: "costPerTask", text: "cost per task" });
      text(".");
    } else text(").");
  }
  if (tail) text(` ${tail}`);
  const sentence = parts.map((p) => p.text).join("");
  return { ...pick, verdict: { ...verdict, parts, sentence } };
}

// ------------------------------------------------ IT-R2 G2 P2: three lines

const plainCount = (n, noun) => `${n} ${n === 1 ? noun : `${noun}s`}`;

/** `Acme voice v1` after `Acme voice v2` reads `v1` (same leading words). */
function shortAfter(first, second) {
  if (!first || !second) return second;
  const a = String(first).split(" ");
  const b = String(second).split(" ");
  if (a.length !== b.length || a.length < 2) return second;
  const same = a.slice(0, -1).every((w, i) => w === b[i]);
  return same ? b[b.length - 1] : second;
}
const WORD_TONE = Object.freeze({
  [VERDICT_WORD.better]: "better",
  [VERDICT_WORD.mixed]: "mixed",
  [VERDICT_WORD.tie]: "tie",
  [VERDICT_WORD.worse]: "worse",
});

/**
 * The Experiments tab's answer in three plain lines (IT-R2 G2 P2; P3 one
 * definition of better / worse; P5 one baseline word):
 *
 *   1  Claude Code (#1D) passes more tasks than Support Agent v3 (#2B,
 *      baseline): 91.5% vs 65.6% on the 24 tasks both ran (Claude Code did
 *      not run T25–T30) · 8 better · 0 worse
 *   2  Costs 19× more ($0.21 vs $0.011 per task) and is 64 s slower — about
 *      $0.77 per extra task solved.
 *   3  [Compare these two] · Change runs ▾ · Confidence: medium ⓘ
 *
 * Every count and rate is runNumbers.compareRuns over the leaderboard's
 * matrix (tasks BOTH ran); tone and confidence are verdictOf's. Pure.
 * @param {{compare: object, verdict: object, cand: {label, name},
 *   base: {label, name, role?: "baseline"|"previous version"|null},
 *   noun?: string, notRun?: string|null,
 *   costPerTask?: {cand: number, base: number},
 *   timePerTask?: {cand: number, base: number},
 *   paired?: object}} a  times in ms; `paired` = the candidate run's
 *   `success_rate.vs_baseline` from the run list (integration C3): used only
 *   when it was judged against `compare.baseline.id`.
 * @returns {{tone, parts: object[], tradeoff: string|null,
 *   confidence: {level: string, extra: string}}}
 */
export function answerLines({
  compare,
  verdict: given,
  cand,
  base,
  noun = "task",
  notRun = null,
  costPerTask: cost,
  timePerTask: time,
  partialRun = null,
  paired = null,
}) {
  const bothN = compare.rows.filter((r) => r.change !== "not_run").length;
  // L7c g5 (action:stop-resume): `Still running` only while the candidate
  // can still play; a run Stop ended says its numbers cover what it ran.
  const partialWords = compare.partial
    ? cand && getRunStatus(cand).key === "Cancelled"
      ? "Stopped part-way, so these numbers cover only the tries it ran."
      : "Still running, so these numbers will move."
    : null;
  // integration C3: the paired interval of this pair decides — the
  // run list's `success_rate.vs_baseline` when the caller passes it and it
  // was judged against this baseline, else the one the compare carries
  // (the Summary's): one verdict for one difference on every screen. The
  // verdict, its confidence and the range text then all read it.
  const served =
    servedPairedSuccess(paired, compare.baseline?.id) ??
    pairedOfCompare(compare);
  const iv = served ? successInterval(served, served.deltaPts) : null;
  // A verdict already decided by the paired interval (the Summary's) is
  // kept as it is; any other is re-decided with it.
  const verdict =
    served && iv && !given?.interval?.paired
      ? successVerdict({
          candidate: { label: cand.label, name: cand.name },
          baseline: { label: base.label },
          worse: compare.worse,
          better: compare.better,
          metrics: {
            success: {
              cand: compare.candidate.rate,
              base: compare.baseline.rate,
              n: bothN,
              tries: given?.confidence?.tries ?? 1,
              candN: compare.candidate.n,
              baseN: compare.baseline.n,
              noun,
              ...served,
            },
            costPerTask: cost,
            timePerTask: time,
          },
        })
      : given;
  // IT-R4 G2 P8 (S1): a difference whose 95% range (on the tasks both
  // ran) holds 0 is `Too close to call`, whatever the try counts say.
  const range =
    served && iv
      ? { lo: iv.lo, hi: iv.hi, real: iv.significant, text: ciText(iv) }
      : diffRange95(compare.candidate.rate, compare.baseline.rate, bothN);
  const level = verdict?.tone === "tie" || (range && !range.real);
  // P3: the dot agrees with the Summary chip — some tasks better AND some
  // worse is `Mixed` (amber) even when success rose.
  const tone = level
    ? "tie"
    : WORD_TONE[
        verdictWord({
          ...(verdict ?? {}),
          better: compare?.better,
          worse: compare?.worse,
        })
      ];
  const nouns = `${noun}s`;
  const verb =
    tone === "tie"
      ? `passes about as many ${nouns} as`
      : (verdict?.deltaPts ?? 0) < 0
        ? `passes fewer ${nouns} than`
        : `passes more ${nouns} than`;
  // IT-R5 G2 P5 (rule 37): short labels — `#1D Claude Code`,
  // `#2B Support Agent v3 (baseline)`.
  const who = (r, role0) => {
    const role = role0 === "new" ? null : role0;
    const head = [r.label, r.name].filter(Boolean).join(" ");
    return role ? `${head} (${role})` : head;
  };
  const both = compare.rows.filter((r) => r.change !== "not_run").length;
  const scope = compare.notRun
    ? `on the ${plainCount(both, noun)} both ran`
    : `on all ${plainCount(both, noun)}`;
  // IT-R3 G2 P1: a partial candidate says so in the first sentence —
  // `#1D Claude Code passes more than #2B (91.5% vs 66.7%), but it ran
  // dataset v1: T25–T30 were not run, so regressions there are unknown.`
  const partialParts = partialRun
    ? [
        { t: "run", text: [cand.label, cand.name].filter(Boolean).join(" ") },
        {
          t: "text",
          text: ` ${
            tone === "tie"
              ? "passes about as many as"
              : (verdict?.deltaPts ?? 0) < 0
                ? "passes fewer than"
                : "passes more than"
          } `,
        },
        { t: "run", text: base.label },
        {
          t: "text",
          text: ` (${fmtPct(compare.candidate.rate)} vs ${fmtPct(compare.baseline.rate)}), but it ran ${partialRun.ran}: ${partialRun.missing} ${partialRun.missingCount === 1 ? "was" : "were"} not run, so regressions there are unknown.`,
        },
      ]
    : null;
  // IT-R5 G2 P5 (rule 37, S1): `Too close to call: #1B Acme voice v2 and
  // #1A v1 pass 79.2% vs 66.7% of 24 calls (95% range −12 to +36 pts).
  // 7 calls better, 4 worse.` — short labels, the range a Term.
  const short = (r) => [r.label, r.name].filter(Boolean).join(" ");
  const rangeNums = range
    ? String(range.text).replace(/^95% (range|CI)\s*/, "")
    : "";
  const levelParts =
    !partialParts && level && range
      ? [
          { t: "text", text: `${VERDICT_WORD.tie}: ` },
          { t: "run", text: short(cand) },
          { t: "text", text: " and " },
          {
            t: "run",
            text: short({ ...base, name: shortAfter(cand.name, base.name) }),
          },
          {
            t: "text",
            text: ` pass ${fmtPct(compare.candidate.rate)} vs ${fmtPct(compare.baseline.rate)} of ${compare.notRun ? `the ${plainCount(both, noun)} both ran` : plainCount(both, noun)} (`,
          },
          {
            t: "term",
            k: "withinNoise",
            text: served && iv ? "95% CI" : "95% range",
          },
          {
            t: "text",
            text: ` ${rangeNums}). ${plainCount(compare.better, noun)} better, ${compare.worse} worse.`,
          },
          ...(partialWords ? [{ t: "text", text: ` ${partialWords}` }] : []),
        ]
      : null;
  // IT-R4 G2 P5 (rule 30): a run made after the pinned baseline is new,
  // the baseline is `the current baseline`. IT-R5 G2 P3/P5: the roles line
  // under it names New and Old, so the sentence drops its `New:` — `#2D
  // Claude Code passes more tasks than the current baseline, #2B Support
  // Agent v3: 91.0% vs 65.6% on 30 tasks.`
  const isNew = cand.role === "new" && base.role === "baseline";
  const newParts =
    !partialParts && !levelParts && isNew
      ? [
          { t: "run", text: who(cand, null) },
          { t: "text", text: ` ${verb} the current ` },
          { t: "term", k: "baseline", text: "baseline" },
          { t: "text", text: ", " },
          { t: "run", text: who(base, null) },
          {
            t: "text",
            text: `: ${fmtPct(compare.candidate.rate)} vs ${fmtPct(compare.baseline.rate)} `,
          },
          compare.notRun
            ? { t: "term", k: "bothRan", text: scope }
            : { t: "text", text: `on ${plainCount(both, noun)}` },
          ...(notRun ? [{ t: "text", text: ` (${notRun})` }] : []),
          // The ship line under it names the rows that got worse.
          { t: "text", text: "." },
          ...(partialWords ? [{ t: "text", text: ` ${partialWords}` }] : []),
        ]
      : null;
  const parts = partialParts ??
    levelParts ??
    newParts ?? [
      { t: "run", text: who(cand, cand.role) },
      { t: "text", text: ` ${verb} ` },
      { t: "run", text: who(base, base.role) },
      {
        t: "text",
        text: `: ${fmtPct(compare.candidate.rate)} vs ${fmtPct(compare.baseline.rate)} `,
      },
      compare.notRun
        ? { t: "term", k: "bothRan", text: scope }
        : { t: "text", text: scope },
      ...(notRun ? [{ t: "text", text: ` (${notRun})` }] : []),
      { t: "text", text: ` · ${compare.better} better · ` },
      { t: "term", k: "worse", text: `${compare.worse} worse` },
      ...(tone === "tie"
        ? [
            { t: "text", text: " · " },
            { t: "term", k: "withinNoise", text: "within noise" },
          ]
        : []),
      // Honest status: a run still going is compared on what is scored.
      ...(partialWords
        ? [
            {
              t: "text",
              text: ` · ${partialWords[0].toLowerCase()}${partialWords.slice(1, -1)}`,
            },
          ]
        : []),
    ];

  // Line 2: what the difference costs, computed.
  const clauses = [];
  let costMore = false;
  if (cost && isNum(cost.cand) && isNum(cost.base) && cost.base > 0) {
    const r = cost.cand / cost.base;
    const pair = `(${fmtCost(cost.cand)} vs ${fmtCost(cost.base)} per ${noun})`;
    if (Math.abs(r - 1) < 0.05) clauses.push(`Costs the same ${pair}`);
    else if (r > 1) {
      costMore = true;
      clauses.push(`Costs ${fmtMultiple(r)} more ${pair}`);
    } else clauses.push(`Costs ${fmtMultiple(1 / r)} less ${pair}`);
  }
  if (time && isNum(time.cand) && isNum(time.base)) {
    const d = time.cand - time.base;
    const same =
      Math.abs(d) < 500 || (time.base > 0 && Math.abs(d) / time.base < 0.05);
    const t = same
      ? "takes about the same time"
      : `is ${fmtMs(Math.abs(d))} ${d > 0 ? "slower" : "faster"}`;
    clauses.push(
      clauses.length ? `and ${t}` : `${t[0].toUpperCase()}${t.slice(1)}`,
    );
  }
  let tradeoff = clauses.length ? clauses.join(" ") : null;
  const extra =
    costMore && cost
      ? extraCostPerSolved(cost, {
          cand: compare.candidate.rate,
          base: compare.baseline.rate,
        })
      : null;
  if (tradeoff && extra != null)
    tradeoff += ` — about ${fmtCost(extra)} per extra ${noun} solved.`;
  else if (tradeoff) tradeoff += ".";

  const conf = verdict?.confidence ?? null;
  const tries = conf?.tries ? ` × ${conf.tries} tries` : "";
  const shrink =
    conf?.level === "high"
      ? "the gap is clear at this size"
      : conf?.level === "medium"
        ? "a gap this size could shrink with more tasks"
        : "a gap this size could be chance";
  return {
    tone,
    parts,
    tradeoff,
    confidence: conf
      ? {
          level: conf.level,
          extra: `${plainCount(both, noun)}${tries}; ${shrink}.`,
        }
      : null,
  };
}
