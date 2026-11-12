// R3 P5 · All runs table text (SVP-P0-2d, SVP-4.6, AS#7/#8/#10/#11/#12).
// Pure: every number goes through fmt.js; deltas are the difference of the
// numbers on screen, with a glyph (never colour alone).
import {
  EMPTY,
  fmtCost,
  fmtCostDelta,
  fmtDeltaMs,
  fmtMs,
  fmtPct,
  fmtPts,
  fmtRate,
  fmtTokens,
} from "src/sections/common/format/fmt";
import { wilson } from "src/sections/common/Analysis/runStats";
import { displayCost } from "./verdictItems";
import { ptsDelta, timeDeltaWords } from "src/sections/common/Runs/runNumbers";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export const GLYPH = Object.freeze({ up: "▲", down: "▼", flat: "" });

/**
 * A delta vs the baseline: `{text, dir, tone, glyph}`. `kind` is `pct`
 * (0–1 values → pts), `cost` (USD) or `ms`. `higherIsBetter` decides the
 * tone; equal displayed values read `±0.0 pts` / `same` with no glyph.
 */
export function deltaInfo(kind, cand, base, { higherIsBetter = true } = {}) {
  if (!isNum(cand) || !isNum(base)) return null;
  let diff;
  let text;
  if (kind === "pct") {
    // L2 G2 R4: one delta, rounded once from the unrounded rates.
    diff = ptsDelta(cand, base);
    if (Math.abs(diff) < 0.05) diff = 0;
    text = fmtPts(diff);
  } else if (kind === "cost") {
    diff = displayCost(cand) - displayCost(base);
    if (Math.abs(diff) < 1e-9) diff = 0;
    text = diff === 0 ? "same" : fmtCostDelta(diff);
  } else {
    // L2 G2 R4 (consistency #3, #15): the unrounded p50s, rounded once
    // (`+69 s`, as the sentence's `69 s slower`); a tiny delta reads `same`
    // here as it does in the sentence (runNumbers.timeDeltaWords).
    const same = timeDeltaWords(cand, base) === "same time";
    diff = same ? 0 : cand - base;
    text = same ? "same" : fmtDeltaMs(diff);
  }
  const dir = diff > 0 ? "up" : diff < 0 ? "down" : "flat";
  const good = dir === "flat" ? null : (dir === "up") === higherIsBetter;
  return {
    text,
    dir,
    glyph: GLYPH[dir],
    tone: good == null ? "neutral" : good ? "good" : "bad",
  };
}

/** `65.6%` + `65.6% (59/90): 59 of 90 trials passed across 30 tasks. …` */
export function successText(run) {
  if (!isNum(run?.success_rate)) return { text: EMPTY, tip: whyMissing(run) };
  return {
    text: fmtPct(run.success_rate),
    tip: `${fmtRate(run.success_pass, run.success_n)}: ${run.success_pass} of ${run.success_n} trials passed across ${run.success_tasks} tasks. Errored trials count as fails; trials that didn't run are not counted.`,
  };
}

/** Reliable on all k tries: tasks that passed every try. */
export function reliableText(run) {
  const k = run?.trials ?? 1;
  if (k < 2)
    return {
      text: EMPTY,
      tip: "Needs 2 or more trials per task. This run tried each task once.",
    };
  if (!isNum(run.reliable_rate)) return { text: EMPTY, tip: whyMissing(run) };
  return {
    text: fmtPct(run.reliable_rate),
    tip: `${run.reliable_pass} of ${run.reliable_n} tasks passed on all ${k} tries = ${fmtPct(run.reliable_rate)}.`,
  };
}

/** Passes every eval (the old `Pass rate`, Advanced only) with its formula. */
export function passesEveryEvalText(run) {
  if (!isNum(run?.pass_rate)) return { text: EMPTY, tip: whyMissing(run) };
  const evals = run.pass_rate_evals ?? 0;
  return {
    text: fmtPct(run.pass_rate),
    tip: `${run.pass_rate_pass} of ${run.pass_rate_n} scored rows pass all ${evals} ${evals === 1 ? "eval" : "evals"} = ${fmtPct(run.pass_rate)}. Stricter than Success rate: a row counts only when every eval passes on its first try.`,
  };
}

export function costText(run) {
  return isNum(run?.cost_per_row)
    ? {
        text: fmtCost(run.cost_per_row),
        tip: `The tries cost ${fmtCost(run.cost_total)} ÷ ${run.in_view ?? run.success_tasks ?? 0} tasks = ${fmtCost(run.cost_per_row)} per task (judge calls are in Total cost).`,
      }
    : { text: EMPTY, tip: "No cost reported for this run." };
}

export function timeText(run) {
  return isNum(run?.latency_p50_ms)
    ? {
        text: fmtMs(run.latency_p50_ms),
        tip: `Median time to the final answer (p50) ${fmtMs(run.latency_p50_ms)}${isNum(run.latency_p99_ms) ? ` · p99 ${fmtMs(run.latency_p99_ms)}` : ""}.`,
      }
    : { text: EMPTY, tip: "No timings reported for this run." };
}

export function tokensText(run) {
  return isNum(run?.avg_tokens)
    ? { text: fmtTokens(Math.round(run.avg_tokens)), tip: null }
    : { text: EMPTY, tip: "No token counts reported for this run." };
}

/** `95% CI 55.4–74.8%` for the success rate (Wilson, over trials). */
export function successCi(run) {
  if (!isNum(run?.success_rate) || !run.success_n) return null;
  const { low, high } = wilson(run.success_rate, run.success_n);
  return `${fmtPct(low).replace("%", "")}–${fmtPct(high)}`;
}

function whyMissing(run) {
  const s = run?.status;
  if (s && ["Running", "Queued", "NotStarted"].includes(s))
    return "Not scored yet: the run is still going.";
  if (s === "WaitingForResults")
    return "Waiting for results: values appear when the upload arrives.";
  return "Not scored: no results in this view.";
}

/** Header help (AS#7, AS#8): what, aggregation, direction. */
export const HEADER_HELP = Object.freeze({
  success: {
    agg: "pass@1",
    tip: "Share of trials that succeeded, over every task and trial. Higher is better. The headline number on every screen.",
  },
  reliable: {
    agg: "pass^k",
    tip: "Share of tasks solved on every try. Higher is better. Stricter than Success rate.",
  },
  cost: {
    agg: "mean",
    tip: "Total cost ÷ tasks. Lower is better.",
  },
  time: {
    agg: "p50",
    tip: "Median time from the task to the final answer. Lower is better.",
  },
  passesEveryEval: {
    agg: "share of rows",
    tip: "Rows where every eval of the run passes, on the first try. Higher is better. Stricter than Success rate.",
  },
  trials: {
    agg: "trials · flaky",
    tip: "Trials per task, and tasks that passed some trials and failed others.",
  },
  errors: {
    agg: "rows",
    tip: "Rows where the run errored before an answer. Lower is better.",
  },
  p99: {
    agg: "p99",
    tip: "Slowest 1% of tasks. Lower is better.",
  },
  tokens: {
    agg: "mean",
    tip: "Prompt + completion tokens per task. Lower is cheaper.",
  },
});

/**
 * The All runs column catalog (one view, A5-09): ids equal the grid's
 * colIds. Default: Run (locked, frozen) · Success rate · Cost per task ·
 * Time per task · Passes every eval · Errors · the first 2 evals. Hidden
 * until the user shows them (`N hidden` chip, Columns, header menu): the
 * other evals, Reliable on all k tries, Trials, Time p99, Tokens per task,
 * Total cost. Ranking shows once Choose winner ranked the runs. Pure.
 *
 * @param {{evals: object[], maxTrials?: number, hasRank?: boolean}} args
 * @returns {{id: string, label: string, defaultVisible?: boolean,
 *   locked?: boolean, defaultPinned?: "left", description?: string}[]}
 */
export function allRunsCatalog({ evals = [], maxTrials = 1, hasRank = false }) {
  const tip = (k) => HEADER_HELP[k]?.tip;
  return [
    {
      id: "run",
      label: "Run",
      locked: true,
      defaultPinned: "left",
    },
    hasRank && { id: "rank", label: "Ranking" },
    { id: "success_rate", label: "Success rate", description: tip("success") },
    maxTrials > 1 && {
      id: "reliable_rate",
      label: `Reliable on all ${maxTrials} tries`,
      defaultVisible: false,
      description: tip("reliable"),
    },
    { id: "cost_per_row", label: "Cost per task", description: tip("cost") },
    {
      id: "average_response_time",
      label: "Time per task",
      description: tip("time"),
    },
    {
      id: "pass_rate",
      label: "Passes every eval",
      description: tip("passesEveryEval"),
    },
    maxTrials > 1 && {
      id: "flaky_rows",
      label: "Trials",
      defaultVisible: false,
      description: tip("trials"),
    },
    { id: "errored_rows", label: "Errors", description: tip("errors") },
    ...evals.map((m, i) => ({
      id: m.key,
      label: m.name,
      defaultVisible: i < 2,
      description: m.description ?? undefined,
    })),
    {
      id: "latency_p99",
      label: "Time p99",
      defaultVisible: false,
      description: tip("p99"),
    },
    {
      id: "total_token",
      label: "Tokens per task",
      defaultVisible: false,
      description: tip("tokens"),
    },
    {
      id: "cost_total",
      label: "Total cost",
      defaultVisible: false,
      description:
        "Everything this run spent: its tries and its evals' judge calls.",
    },
  ].filter(Boolean);
}

/**
 * L7c g6 (sum:all-runs): the All runs chart's name for N evals — a radar
 * from 4 evals, grouped bars below (SpiderChartExperiment's rule).
 */
export const allRunsChartWord = (evalCount) =>
  Number(evalCount) >= 4 ? "Radar" : "Bars";
