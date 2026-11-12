// R3 P5 · Verdict first (SVP-P0-1c, SVP-4.6): one verdict per candidate run
// vs the baseline, from the All-runs data (Success rate + n + significance,
// Cost per task, Time per task, tasks that got worse). Pure; the sentence and
// the tone come from the G0 `verdictOf` (never recomputed here).
import {
  ciPts as pts1,
  verdictWord,
  sortVerdicts,
  VERDICT_WORD,
} from "src/sections/common/ux/verdictModel";
import { wilson } from "src/sections/common/Analysis/runStats";
import { successVerdict } from "src/sections/common/Runs/oneVerdict";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { VOCAB } from "src/sections/common/vocab";
import {
  EMPTY,
  fmtCost,
  fmtCount,
  fmtMs,
  fmtPct,
  fmtPts,
  fmtRate,
} from "src/sections/common/format/fmt";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * A duration rounded to the precision `fmtMs` shows (`9.8 s`, `74 s`), so the
 * verdict's `64 s slower` equals the difference of the two numbers on screen.
 */
export function displayMs(ms) {
  if (!isNum(ms)) return ms;
  const a = Math.abs(ms);
  if (a < 1000) return Math.round(ms);
  if (a < 10000) return Math.round(ms / 100) * 100;
  return Math.round(ms / 1000) * 1000;
}

/** A cost rounded to the precision `fmtCost` shows (`$0.011`, `$0.21`). */
export function displayCost(usd) {
  if (!isNum(usd) || usd === 0) return usd;
  if (Math.abs(usd) < 1) return Number(usd.toPrecision(2));
  return Math.round(usd * 100) / 100;
}

/**
 * `Claude Code × claude-sonnet-4-5` → `Claude Code` (the run's own name);
 * `Support Agent v3.1 (draft) · all rows` → `Support Agent v3.1` (the name
 * every other surface prints: matrixModel.agentShortName).
 */
export function shortRunName(name) {
  const s = String(name ?? "").trim();
  if (!s) return "";
  return s
    .split(" × ")[0]
    .split(" · ")[0]
    .replace(/\s*\((draft|preview)\)\s*$/i, "")
    .trim();
}

/**
 * IT-R5 G6 P2 (rule 37, S1): the not-significant word, neutral grey. Read
 * from G2's VERDICT_WORD so the verdict words live in one place.
 */
export const TIE_WORD = VERDICT_WORD.tie;

/**
 * the capability contract: the word for a candidate whose real outcome is Failed (every
 * row it played errored). It is never scored against the baseline.
 */
export const FAILED_WORD = "Failed";

/** The word on the tone chip (`Better · +25.4 pts`). */
export const TONE_WORD = Object.freeze({
  better: "Better",
  mixed: "Mixed",
  tie: TIE_WORD,
  worse: "Worse",
});

/** G2's verdictWord with `Level` read as `Too close to call` (S1). */
export function wordOf(verdict) {
  if (verdict?.failed) return FAILED_WORD;
  const w = verdictWord(verdict);
  return w === "Level" ? TIE_WORD : w;
}

/** Copy for the tone chip's tooltip (what the tone means). */
export const TONE_HELP = Object.freeze({
  better: "Better than the baseline on success, beyond noise.",
  mixed:
    "Better on success, with a trade-off: it costs 1.5× or more, or takes 50% longer, than the baseline.",
  tie: "Too close to call: the 95% interval of the success difference holds 0, so it may be chance.",
  worse: "Worse than the baseline on success, beyond noise.",
});

/** What `Confidence: low / medium / high` means (the verdict counts tasks). */
export const CONFIDENCE_HELP =
  "Confidence: high = the difference is beyond noise on 50 or more tasks; medium = beyond noise on fewer tasks; low = the 95% interval of the difference holds 0, so it may be chance.";

/** The success rule every number on the card uses (the interface contract C3 c). */
export const SUCCESS_RULE = "Errored tries count as fails.";

/**
 * the interface contract C3: the verdict's ONE interval in words, e.g. `+7.1 pts (95% CI
 * 3.1 to 11.1): beyond noise` / `…: within noise, may be chance`. Paired on
 * the tasks both ran; `(unpaired ranges)` only where no paired data exists.
 * Null without an interval.
 */
export function intervalText(verdict) {
  const iv = verdict?.interval;
  if (!iv || !isNum(verdict?.deltaPts)) return null;
  const how = iv.paired ? "" : ", unpaired ranges";
  const word = iv.significant ? "beyond noise" : "within noise, may be chance";
  return `${fmtPts(verdict.deltaPts)} (95% CI ${pts1(iv.lo)} to ${pts1(iv.hi)}${how}): ${word}`;
}

/** `±4.0 pts`: the half-width of the verdict's interval (1 dp). */
export function bandOf(verdict) {
  const iv = verdict?.interval;
  if (!iv) return null;
  const half = iv.half ?? (iv.hi - iv.lo) / 2;
  return isNum(half) ? Math.round(half * 10) / 10 : null;
}

/**
 * A level verdict that still hides damage: success is within noise but
 * more than 3 tasks got worse, or the average dropped more than 10 pts.
 * A PM must not read `Level` as `no problem` (R3 analysis fix).
 */
export function levelButWorse(verdict) {
  if (verdict?.tone !== "tie") return false;
  const worse = verdict.worse ?? 0;
  const pts = verdict.deltaPts;
  return worse > 3 || (typeof pts === "number" && pts < -10);
}

/**
 * Chip colour: the verdict tone. IT-R5 G6 P2 (rule 37): `Too close to call`
 * is always the neutral grey chip (a level-but-worse run names its worse
 * tasks in the counts beside it and in the tooltip, never in amber).
 */
export function chipTone(verdict) {
  if (verdict?.failed) return "error";
  return verdict?.tone ?? "tie";
}

/**
 * `#2E 83.3% (95% range 74.3%–89.6%, 90 trials) vs #2B 65.6% (…)` — the two
 * success ranges the verdict compares, so `within noise` shows its numbers.
 * Null when either side has no success rate or no tries.
 */
export function successRanges(run, base) {
  if (!run || !base) return null;
  const side = (r) => {
    const n = r.success_n;
    if (!isNum(r.success_rate) || !isNum(n) || n <= 0) return null;
    const { low, high } = wilson(r.success_rate, n);
    return {
      label: r.label,
      rate: r.success_rate,
      low,
      high,
      n,
      text: `${r.label} ${fmtPct(r.success_rate)} (95% range ${fmtPct(low)}–${fmtPct(high)}, ${fmtCount(n, VOCAB.TRIAL)})`,
    };
  };
  const c = side(run);
  const b = side(base);
  if (!c || !b) return null;
  return { cand: c, base: b, overlap: c.low <= b.high && b.low <= c.high };
}

/**
 * The tone chip's tooltip: what the tone means, with the numbers behind it
 * (both 95% ranges; for a level-but-worse run, how many tasks got worse).
 * Review 2 (S1): `noun` is the analysis noun (`row` on a rows dataset), so
 * the tooltip counts what the band and the card count.
 * @param {{verdict, run, base}} item  a `summaryVerdicts` item
 * @param {{noun?: string}} [o]
 */
export function toneHelp(item, { noun = "task" } = {}) {
  const verdict = item?.verdict;
  if (verdict?.failed) return verdict.sentence;
  const tone = verdict?.tone ?? "tie";
  const lines = [];
  if (levelButWorse(verdict)) {
    const worse = verdict.worse ?? 0;
    lines.push(
      `Too close to call on average (${fmtPts(verdict.deltaPts)} success, within noise), but ${fmtCount(worse, noun)} got worse. Review them before you ship.`,
    );
  } else if (verdictWord(verdict) === "Mixed")
    // the interface contract C3: the chip's word leads (one word on chip and tooltip).
    lines.push(
      `${chipWord(verdict)}: ${fmtCount(verdict.better ?? 0, noun)} got better and ${fmtCount(verdict.worse ?? 0, noun)} got worse.`,
    );
  else lines.push(TONE_HELP[tone]);
  // the interface contract C3: the one interval decides.
  const iv = intervalText(verdict);
  const paired = Boolean(verdict?.interval?.paired);
  if (iv) {
    const n = paired ? verdict.interval.n : null;
    lines.push(
      `${iv}${isNum(n) ? `, paired on the ${fmtCount(n, noun)} both ran` : ""}.`,
    );
  }
  // Each run's own (unpaired Wilson) range: the decision only where no
  // paired data exists. Beside the paired CI it is never a
  // second verdict: it prints only as context that agrees with the paired
  // decision (IT-R5 G6 rule 37 keeps the ranges on the card), and is
  // dropped where its overlap would read as the opposite verdict.
  const r = successRanges(item?.run, item?.base);
  const agrees = r && (!paired || r.overlap === !verdict.interval.significant);
  if (r && agrees)
    lines.push(
      paired
        ? `Each run alone: ${r.cand.text} vs ${r.base.text}; ${
            r.overlap ? "the ranges overlap" : "the ranges do not overlap"
          }.`
        : `${r.cand.text} vs ${r.base.text}: ${
            r.overlap
              ? "the ranges overlap, so the difference may be chance."
              : "the ranges do not overlap, so the difference is real."
          }`,
    );
  lines.push(SUCCESS_RULE);
  return lines.join(" ");
}

/**
 * the interface contract C3: the one word the chip and its tooltip both show, from
 * verdictWord: `Mixed` (tasks moved both ways) resolves by the success
 * direction, as the chip has always read it (`Better · 7 worse` /
 * `Worse, with some gains`); a level run hiding damage reads `Too close to
 * call`.
 */
export function chipWord(verdict) {
  if (!verdict) return TIE_WORD;
  if (verdict.failed) return FAILED_WORD;
  if (levelButWorse(verdict)) return TIE_WORD;
  const w = wordOf(verdict);
  if (w === "Mixed") return (verdict.deltaPts ?? 0) >= 0 ? "Better" : "Worse";
  return w;
}

/**
 * The winning dimension shown in the chip: the success delta, unless success
 * is level and the run is clearly cheaper or faster.
 */
export function chipLabel(verdict) {
  if (!verdict) return "";
  if (verdict.failed) return FAILED_WORD;
  // IT-R4 G6 P5: `Better +25.4 pts` / `Level` / `Worse −3.1 pts`; a level
  // run that hides damage keeps the warning tone (its tooltip and sentence
  // name the tasks that got worse).
  if (levelButWorse(verdict)) return TIE_WORD;
  const chips = verdict.chips ?? [];
  const success = chips.find((c) => c.key === "success");
  // IT-R2 G2 P3: `Mixed` only when some tasks got better AND some worse
  // (the counts the sentence prints); a costly win reads `Better`.
  const word = wordOf(verdict);
  let dim = success?.label?.replace(/ (task )?success$/i, "") ?? "";
  if (verdict.tone === "tie") {
    const good = chips.find(
      (c) => (c.key === "cost" || c.key === "time") && c.tone === "good",
    );
    if (good) dim = good.label;
  }
  // IT-R3 G6 P4: never `Mixed · +18.8 pts` beside a check: a run that
  // gains on success but loses some tasks reads `Better, with trade-offs`.
  // IT-R5 G6 P2: the chip carries the trade-off (`Better · costs 19×`,
  // `Better · 64 s slower`).
  const trade = tradeoffText(verdict);
  if (word === "Mixed")
    return (verdict.deltaPts ?? 0) >= 0
      ? // IT-R5 G6 P3: the trade-off fits the chip column (`Better · 7
        // worse`), never the long `with trade-offs` over the sentence.
        `Better · ${
          trade ??
          (Number(verdict.worse) > 0 ? `${verdict.worse} worse` : "trade-offs")
        }`
      : "Worse, with some gains";
  if (word === TIE_WORD) return TIE_WORD;
  if (word === "Better" && trade) return `Better · ${trade}`;
  return dim ? `${word} ${dim}` : word;
}

/** `costs 19×` / `64 s slower`: the first bad cost or time chip. */
export function tradeoffText(verdict) {
  const bad = (verdict?.chips ?? []).find(
    (c) => (c.key === "cost" || c.key === "time") && c.tone === "bad",
  );
  if (!bad) return null;
  const m = /^(.+?) cost$/.exec(bad.label ?? "");
  return m ? `costs ${m[1]}` : bad.label;
}

/**
 * IT-R5 G6 P2: the noise band the chips use, stated once: the smallest
 * success difference (pts) whose two 95% ranges stop overlapping, at the
 * baseline's rate on `n` tries (the `Too close to call` rule). Null
 * without a rate or tries.
 */
export function noiseBandPts(baseRate, n) {
  if (!isNum(baseRate) || !(n > 0)) return null;
  const b = wilson(baseRate, n);
  let up = null;
  let down = null;
  for (let d = 0.001; d <= 1; d += 0.001) {
    if (up == null && baseRate + d <= 1 && wilson(baseRate + d, n).low > b.high)
      up = d;
    if (
      down == null &&
      baseRate - d >= 0 &&
      wilson(baseRate - d, n).high < b.low
    )
      down = d;
    if (up != null && down != null) break;
  }
  const pts = [up, down].filter((v) => v != null);
  return pts.length ? Math.round(Math.max(...pts) * 100) : null;
}

/**
 * IT-R4 G6 P5: what `Confidence: medium` rests on, in one sentence:
 * `Medium: the difference is real at 95%, but it rests on 30 tasks. 50+
 * tasks would make it high.` (verdictModel: high = significant on ≥ 50).
 */
const HIGH_TASKS = 50;

export function confidenceHelp(verdict, { noun = "task" } = {}) {
  if (verdict?.failed)
    return "No confidence band: the run has no output to compare.";
  const level = verdict?.confidence?.level;
  const n = verdict?.confidence?.tasks ?? null;
  const tasks = Number.isFinite(n) ? fmtCount(n, noun) : `these ${noun}s`;
  // the interface contract C3: the same interval as the whisker, band and tooltip.
  const iv = verdict?.interval;
  const ci = iv ? ` (95% CI ${pts1(iv.lo)} to ${pts1(iv.hi)} pts)` : "";
  if (level === "high")
    return `High: the difference is real at 95%${ci} and rests on ${tasks}.`;
  if (level === "medium")
    return `Medium: the difference is real at 95%${ci}, but it rests on ${tasks}. ${HIGH_TASKS}+ ${noun}s would make it high.`;
  if (level === "low")
    return `Low: the 95% interval of the difference${ci} holds 0 on ${tasks}, so it may be chance.`;
  return CONFIDENCE_HELP;
}

/**
 * the capability contract: a run whose real outcome is Failed (every row it played
 * errored) produced no output, so it is named Failed, never read as a
 * quality regression of the baseline.
 */
export const isFailedRun = (run) =>
  Boolean(run) && getRunStatus(run).key === "Failed";

/** `#9A HTTP endpoint failed: all 20 rows errored, so …` (the capability contract). */
export function failedRunSentence(run, base) {
  const n = Number(run?.errored_rows ?? run?.progress?.errors);
  const who = `${run?.label ?? ""} ${shortRunName(run?.name) ?? ""}`.trim();
  const rows =
    Number.isFinite(n) && n > 0 ? `all ${fmtCount(n, "row")}` : "every row";
  return `${who} failed: ${rows} errored, so it has no output to compare with ${base?.label ?? "the baseline"}. Fix the run's setup and run it again.`;
}

/**
 * the capability contract (v2.1 round 2): the ONE verdict of a Failed candidate, made in
 * `summaryVerdicts` so every consumer (the Summary card, Print or save as
 * PDF, Copy summary, Ask Falcon, the Dataset verdict card, the candidate
 * picker) reads `Failed` with its sentence. No tone word, no delta, no
 * confidence band, no counts of tasks that got worse.
 */
export function failedVerdict(run, base) {
  const sentence = failedRunSentence(run, base);
  return {
    failed: true,
    tone: "failed",
    sentence,
    parts: [{ t: "text", text: sentence }],
    chips: [],
    deltaPts: null,
    interval: null,
    confidence: null,
    better: null,
    worse: null,
  };
}

/**
 * Inputs for `verdictOf` of one candidate run vs the baseline run. IT-R1 G2
 * P1: the same sentence shape as the Matrix and the Experiments headline
 * (`+18.8 pts Task success across 30 tasks · improved 8 · worse 2`); the
 * counts are the mock's tasks_vs_baseline (runNumbers' per-row rule).
 * @param {object} run
 * @param {object} base
 * @param {{noun?: "task"|"row"}} [o]
 */
export function verdictInput(run, base, { noun } = {}) {
  const tries = run.trials ?? 1;
  const tvb = run.tasks_vs_baseline ?? {};
  // Over the rows both runs ran (compareRuns), when the API sends it.
  const scoped = isNum(tvb.cand_rate) && isNum(tvb.base_rate);
  const tasks =
    scoped && isNum(tvb.both) ? tvb.both : run.success_tasks ?? null;
  return {
    candidate: { label: run.label, name: shortRunName(run.name) },
    baseline: { label: base.label, name: shortRunName(base.name) },
    scopeText:
      noun && isNum(tasks)
        ? `across ${fmtCount(tasks, noun)}${scoped && tvb.not_run ? " both ran" : ""}`
        : undefined,
    metrics: {
      success: {
        name: noun === "task" ? "Task success" : undefined,
        noun: noun ?? undefined,
        cand: scoped ? tvb.cand_rate : run.success_rate,
        base: scoped ? tvb.base_rate : base.success_rate,
        // The compare's own significance and delta when the API sends it.
        ...(typeof tvb.compare?.significant === "boolean"
          ? { significant: tvb.compare.significant }
          : {}),
        ...(isNum(tvb.compare?.deltaPts)
          ? { deltaPts: tvb.compare.deltaPts }
          : {}),
        // the interface contract C3: the paired 95% CI of this difference (one estimator).
        ...(isNum(tvb.compare?.ci95Pts)
          ? { ci95Pts: tvb.compare.ci95Pts, nPaired: tvb.compare.nPaired }
          : {}),
        n:
          scoped && isNum(tvb.both)
            ? tvb.both
            : run.success_tasks ?? run.success_n,
        tries,
        candN: run.success_n,
        baseN: base.success_n,
      },
      costPerTask:
        isNum(run.cost_per_row) && isNum(base.cost_per_row)
          ? {
              cand: displayCost(run.cost_per_row),
              base: displayCost(base.cost_per_row),
            }
          : undefined,
      // L2 G2 R4 (consistency #3): the unrounded p50s, so the delta is
      // rounded once (`69 s slower`, as runNumbers.timeDeltaWords prints).
      timePerTask:
        isNum(run.latency_p50_ms) && isNum(base.latency_p50_ms)
          ? { cand: run.latency_p50_ms, base: base.latency_p50_ms }
          : undefined,
    },
    // IT-R5 G6 P2 (rule 36): the Matrix's counts (compareRuns over the
    // rows both ran), so `7 better · 2 worse` reads the same everywhere.
    worse: tvb.compare?.worse ?? tvb.worse,
    better: tvb.compare?.better ?? tvb.better,
  };
}

const changeIds = (tvb, change) =>
  Array.isArray(tvb?.compare?.rows)
    ? tvb.compare.rows.filter((r) => r.change === change).map((r) => r.id)
    : null;

/**
 * Verdicts for every candidate run of `analysis` (best first). Each item:
 * `{key, run, base, verdict, worseIds, betterIds}`. Empty when there is no
 * baseline (absolute view) or the baseline has no success rate yet.
 */
export function summaryVerdicts(analysis) {
  const runs = analysis?.runs ?? [];
  const baseId = analysis?.baseline_run_id;
  const base = runs.find((r) => r.run_id === baseId);
  if (!base || !isNum(base.success_rate)) return [];
  const items = runs
    .filter((r) => r.run_id !== baseId && isNum(r.success_rate))
    .map((run) => {
      // the capability contract: a Failed run (every row errored) gets the failed
      // verdict here, once, never a Worse/trails score of rows it never ran.
      if (isFailedRun(run))
        return {
          key: run.run_id,
          run,
          base,
          noun: analysis?.noun ?? "task",
          failed: true,
          verdict: failedVerdict(run, base),
          worseIds: [],
          betterIds: [],
        };
      return {
        key: run.run_id,
        run,
        base,
        // Review 2 (S1): what the card counts (`task` / `row`).
        noun: analysis?.noun ?? "task",
        // L2 G2 R4: the one verdict (diffRange95 significance, one delta).
        verdict: successVerdict(
          verdictInput(run, base, { noun: analysis?.noun }),
        ),
        // Worst drop first (the mock's order), kept to the Matrix's set.
        worseIds: (() => {
          const ids = changeIds(run.tasks_vs_baseline, "worse");
          const all = run.tasks_vs_baseline?.worse_row_ids ?? [];
          return ids
            ? [
                ...all.filter((id) => ids.includes(id)),
                ...ids.filter((id) => !all.includes(id)),
              ]
            : all;
        })(),
        betterIds:
          changeIds(run.tasks_vs_baseline, "better") ??
          run.tasks_vs_baseline?.better_row_ids ??
          [],
      };
    });
  // IT-R5 G6 P2: sorted like the Matrix — success difference vs the
  // baseline, highest first (`sorted by success vs #2B`).
  return sortBySuccess(sortVerdicts(items));
}

/** Success delta vs the baseline, descending (stable for ties). */
export function sortBySuccess(items) {
  const d = (x) =>
    isNum(x?.verdict?.deltaPts) ? x.verdict.deltaPts : -Infinity;
  return [...(items ?? [])].sort((a, b) => d(b) - d(a));
}

/** The verdict item of the page's candidate run (else the best one). */
export function candidateItem(items, candidateId) {
  const list = items ?? [];
  return list.find((i) => i.run.run_id === candidateId) ?? list[0] ?? null;
}

/** Counts per tone: `{better, mixed, tie, worse}`. */
/** `{Better, Mixed, Too close to call, Worse}` counts by the chip word. */
export function wordCounts(items) {
  const out = { Better: 0, Mixed: 0, [TIE_WORD]: 0, Worse: 0 };
  (items ?? []).forEach((i) => {
    if (!i?.verdict || i.verdict.failed) return;
    out[wordOf(i.verdict)] += 1;
  });
  return out;
}

export function toneCounts(items) {
  const out = { better: 0, mixed: 0, tie: 0, worse: 0 };
  (items ?? []).forEach((i) => {
    const t = i?.verdict?.tone;
    if (t in out) out[t] += 1;
  });
  return out;
}

/**
 * Markdown for `Copy summary` (AS#13): the verdict sentences and the numbers
 * behind them, ready to paste in a doc or a PR.
 */
export function verdictMarkdown(analysis, items, { title } = {}) {
  const runs = analysis?.runs ?? [];
  const base = runs.find((r) => r.run_id === analysis?.baseline_run_id);
  const lines = [];
  lines.push(
    `### ${title ?? "Summary"}${base ? ` vs baseline ${base.label}` : ""}`,
  );
  lines.push("");
  (items ?? []).forEach((i) =>
    lines.push(
      `- ${i.verdict?.failed || isFailedRun(i.run) ? failedRunSentence(i.run, i.base) : i.verdict.sentence}`,
    ),
  );
  lines.push("");
  lines.push("| Run | Success rate | Cost per task | Time per task |");
  lines.push("|---|---:|---:|---:|");
  runs.forEach((r) => {
    lines.push(
      `| ${r.label} ${shortRunName(r.name)}${r.run_id === base?.run_id ? " (baseline)" : ""} | ${
        isNum(r.success_rate) ? fmtRate(r.success_pass, r.success_n) : EMPTY
      } | ${fmtCost(r.cost_per_row)} | ${
        isNum(r.latency_p50_ms) ? fmtMs(r.latency_p50_ms) : EMPTY
      } |`,
    );
  });
  return lines.join("\n");
}

/** Plain one-liner for the Ask Falcon prompt about one verdict. */
export function falconQuestion(item) {
  if (!item) return "";
  const { run, base, verdict } = item;
  if (verdict?.failed)
    return `Why did every row of ${run.label} error, so it has no output to compare with ${base.label}?`;
  const pct = isNum(run.success_rate) ? fmtPct(run.success_rate) : EMPTY;
  const bpct = isNum(base.success_rate) ? fmtPct(base.success_rate) : EMPTY;
  if (verdict.tone === "worse")
    return `Why does ${run.label} trail ${base.label} (${pct} vs ${bpct} success)?`;
  if (verdict.worse > 0)
    return `Which ${verdict.worse === 1 ? "task" : `${verdict.worse} tasks`} got worse on ${run.label} vs ${base.label}, and why?`;
  return `Is ${run.label} worth its cost vs ${base.label} (${pct} vs ${bpct} success)?`;
}
