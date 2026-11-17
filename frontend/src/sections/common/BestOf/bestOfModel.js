// Best of N (the interface contract): the view's words and the cost line. Pure. The
// numbers come from the mock's w5BestOf.js (the one computation); the cost
// before you run is the one estimator's (runStats.estimateRun with the
// judge's own price per call, estimateInputs.judgeCallUsd).
import { estimateRun } from "src/sections/common/Analysis/runStats";
import { judgeCallUsd } from "src/sections/develop-detail/Experiment/estimateInputs";
import { fmtCost } from "src/sections/common/format/fmt";
import { wilson } from "src/sections/common/Analysis/runStats";

export const BEST_OF_MIN_RUNS = 2;
export const BEST_OF_MAX_RUNS = 8;

/**
 * The picker value of a stored result (`judge:<eval>` / `max_score:<eval>`),
 * so a result opened from its link shows its own eval and mode (M7).
 */
export const pickValueOf = (rec) =>
  rec?.mode && rec?.eval_key ? `${rec.mode}:${rec.eval_key}` : null;

/**
 * Picker options (L7c g6, the capability contract f: one scorer): only `Highest score on
 * <eval>`, which compares the scores the one scorer already stored with each
 * output. Judging again here would be a second scorer, so that mode is not
 * offered.
 */
export function pickOptions(evals = []) {
  return evals.map((e) => ({
    value: `max_score:${e.key}`,
    mode: "max_score",
    key: e.key,
    label: `Highest score on ${e.name}`,
  }));
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * Per-run outright wins (L7c g6): a tie is never a fractional win. Wins are
 * the rows the run won alone (exactly the rows its filter opens), ties the
 * rows it shared the top score on; the share is wins over decided rows with
 * its Wilson interval, and the tie share is reported once for all runs.
 */
export function outrightShares(rec) {
  // A record without its rows (a summary-only read) keeps its own shares.
  if (!Array.isArray(rec?.rows))
    return {
      n: rec?.n ?? 0,
      shares: rec?.shares ?? [],
      tieRows: 0,
      tieShare: 0,
    };
  const rows = rec.rows;
  const decided = rows.filter((r) => !r.skipped);
  const n = decided.length;
  const runIds = rec?.run_ids ?? (rec?.shares ?? []).map((s) => s.run_id);
  const shares = runIds.map((id) => {
    const wins = decided.filter((r) => r.winner === id).length;
    const ties = decided.filter(
      (r) => r.winner === "tie" && (r.tied ?? []).includes(id),
    ).length;
    const share = n ? wins / n : 0;
    return { run_id: id, wins, ties, share, ci: wilson(share, n) };
  });
  const tieRows = decided.filter((r) => r.winner === "tie").length;
  return { n, shares, tieRows, tieShare: n ? tieRows / n : 0 };
}

/** `3 wins · 1 tie` (grammar per count). */
export const winsText = (s) =>
  `${plural(s.wins, "win")} · ${plural(s.ties, "tie")}`;

/** The judge cost of best-of through the one estimator. */
export function bestOfCost(evalOpt, outputs) {
  if (!evalOpt?.judge) return 0;
  return estimateRun({
    rows: outputs,
    trials: 1,
    targets: [{ kind: "code" }],
    evals: [
      { type: "judge", cost_per_call: judgeCallUsd(evalOpt.judge_model) },
    ],
  }).judge_cost_usd;
}

/** `Judge 238 rows (714 outputs) · about $0.43` and the other modes. */
export function costLine({ mode, evalOpt, rows, outputs }) {
  const r = `${rows.toLocaleString("en-US")} row${rows === 1 ? "" : "s"}`;
  if (mode === "max_score")
    return `Compare the stored ${evalOpt?.name ?? "eval"} scores of ${r} · no judging, free`;
  if (!evalOpt?.judge)
    return `Score ${r} (${outputs} outputs) with the ${evalOpt?.name ?? ""} check · free`;
  return `Judge ${r} (${outputs} outputs) with ${evalOpt.judge_model} · about ${fmtCost(bestOfCost(evalOpt, outputs))}`;
}

export const pct = (v) =>
  Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";

/** `41.2% (35.1–47.6%)` */
export const shareText = (s) =>
  `${pct(s.share)} (${(s.ci.low * 100).toFixed(1)}–${(s.ci.high * 100).toFixed(1)}%)`;

/** A run's value on one row: `Pass` / `Fail` or `0.82`. */
export function scoreText(v, outputType) {
  if (v == null) return "—";
  if (outputType === "pass_fail") return v >= 0.5 ? "Pass" : "Fail";
  return Number(v).toFixed(2);
}

/** `#2B`, `Tie · #1A, #2B`, `Skipped · fewer than 2 outputs`. */
export function verdictText(row, labelOf) {
  if (row.skipped) return "Skipped · fewer than 2 outputs";
  if (row.winner === "tie") return `Tie · ${row.tied.map(labelOf).join(", ")}`;
  return labelOf(row.winner);
}

/** The recorded order the outputs were judged in: `#2A → #1A → #2B`. */
export const orderText = (order, labelOf) => order.map(labelOf).join(" → ");

/**
 * The order to show for a row (L7c g6): only for a result that judged
 * outputs (mode `judge`), and only the runs that had an output to judge.
 * A highest-stored-score result judged nothing, so it has no order.
 */
export function judgedOrder(rec, row) {
  if (rec?.mode !== "judge") return [];
  return (row?.order ?? []).filter((id) => row?.scores?.[id] != null);
}

/** Rows of one filter: all, ties, skipped, or the rows one run won. */
export function rowsOf(rows, filter) {
  if (!filter || filter === "all") return rows;
  if (filter === "tie") return rows.filter((r) => r.winner === "tie");
  if (filter === "skipped") return rows.filter((r) => r.skipped);
  return rows.filter((r) => r.winner === filter);
}

/** The summary line under the shares. */
export function summaryLine(rec, labelOf) {
  const parts = [`${plural(rec.n, "row")} decided`, plural(rec.ties, "tie")];
  if (rec.skipped) parts.push(`${rec.skipped} skipped`);
  parts.push(
    rec.mode === "judge"
      ? `${rec.judge_calls} outputs scored${rec.judge_model ? ` by ${rec.judge_model} · ${fmtCost(rec.judge_cost_usd)}` : " (check, free)"}`
      : "stored scores, no judging",
  );
  // Equal top shares are named together (M7: "most wins: #9A" read as a
  // winner when #9A and #10A had the same 50%).
  const { shares } = outrightShares(rec);
  const top = Math.max(...shares.map((s) => s.share));
  const leaders = shares.filter((s) => Math.abs(s.share - top) < 1e-9);
  if (leaders.length === 1)
    parts.push(`most wins: ${labelOf(leaders[0].run_id)}`);
  else if (leaders.length > 1)
    parts.push(
      `even: ${leaders.map((s) => labelOf(s.run_id)).join(", ")} share the most wins`,
    );
  return parts.join(" · ");
}

/**
 * Review M5: `judged now Pass · stored Fail` when the scorer's value for an
 * output differs from the score stored with that output (w5BestOf
 * `differs`), else null.
 */
export function judgedVsStored(row, runId, outputType) {
  if (!row?.differs?.includes(runId)) return null;
  return `judged now ${scoreText(row.scores?.[runId], outputType)} · stored ${scoreText(row.stored?.[runId], outputType)}`;
}

/** The note over the rows when judged values differ from stored scores. */
export function storedNote(rec) {
  if (rec?.mode !== "judge" || !rec.stored_differs) return null;
  const n = rec.stored_differs;
  return `On ${n} of ${rec.stored_compared} outputs the judge's value now differs from the score stored with the output, which the Summary and results show. Both are shown; the winner is picked on the judged value.`;
}
