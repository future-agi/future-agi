import { formatExactMoney } from "src/sections/common/format/money";
// What the Run from code panel's Step 4 said (R2-SPEC §5.3.3). Pure.
// dataset integration FE-0: the panel (generated runners, the live connection) and
// howToRunOf are removed; the upload / session status helpers and the
// static agent diff stay for the results drawer and FE-1.
import { runOutcomeKey } from "src/sections/common/Runs/runStatus";

const n = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const fmt = (v) => n(v).toLocaleString("en-US");
const count = (v, one, many) => `${fmt(v)} ${n(v) === 1 ? one : many}`;
export const STALL_MS = 2 * 60 * 1000;
export const MAX_STREAM_ROWS = 50;

/** Money as the rest of the product: `$0.004` under a cent, else `$0.02`. */
export function formatUsd(v, { precise = false } = {}) {
  return formatExactMoney(v, {
    minimumFractionDigits:
      precise && Number(v) !== 0 && Math.abs(Number(v)) < 0.1 ? 3 : 2,
  });
}

/**
 * Every result the session has received, newest first (S1 events: `result`
 * one by one, `batch` with `results[]`).
 * @returns {{key, rowId, rowLabel, trial, passed, steps, costUsd, traceId,
 *   traceSummary, at}[]}
 */
export function streamedResults(session) {
  const out = [];
  (session?.events ?? []).forEach((e, i) => {
    const list =
      e?.event === "batch" || Array.isArray(e?.results)
        ? e.results ?? []
        : e?.event === "result" || e?.type === "row_done"
          ? [e]
          : [];
    list.forEach((r, j) =>
      out.push({
        key: `${i}:${j}:${r.row_id ?? r.row_label}:${r.trial ?? 1}`,
        rowId: r.row_id ?? null,
        rowLabel: r.row_label ?? "Row",
        trial: n(r.trial) || 1,
        passed: r.passed ?? null,
        steps: r.steps ?? null,
        costUsd: r.cost_usd ?? null,
        traceId: r.trace_id ?? null,
        traceSummary: r.trace_summary ?? null,
        at: r.at ?? e.at ?? null,
      }),
    );
  });
  return out.reverse();
}

/** `T17 · trial 2` (+ ` · 7 steps · $0.018` when known). */
export function streamRowText(r) {
  const parts = [r.rowLabel, `trial ${r.trial}`];
  if (r.steps !== null && r.steps !== undefined)
    parts.push(count(r.steps, "step", "steps"));
  if (r.costUsd !== null && r.costUsd !== undefined)
    parts.push(formatUsd(r.costUsd, { precise: true }));
  return parts.join(" · ");
}

/** `{head: "T17 · trial 2", tail: "7 steps · $0.018"}` for the stream list. */
export function streamRowParts(r) {
  const tail = [];
  if (r.steps !== null && r.steps !== undefined)
    tail.push(count(r.steps, "step", "steps"));
  if (r.costUsd !== null && r.costUsd !== undefined)
    tail.push(formatUsd(r.costUsd, { precise: true }));
  return { head: `${r.rowLabel} · trial ${r.trial}`, tail: tail.join(" · ") };
}

/** Rows whose every received trial passed (`21 of 30 rows pass every trial`). */
export function rowsPassingEveryTrial(results) {
  const byRow = new Map();
  results.forEach((r) => {
    const k = r.rowId ?? r.rowLabel;
    const prev = byRow.get(k) ?? true;
    byRow.set(k, prev && r.passed === true);
  });
  return {
    passing: [...byRow.values()].filter(Boolean).length,
    rows: byRow.size,
  };
}

/**
 * Works today status from the canonical run (today's upload states).
 * @returns {{state, text, done, total, pct}}
 */
export function uploadStatus(run) {
  const status = String(run?.status ?? "");
  const trials = Math.max(1, n(run?.trials) || 1);
  const done = n(run?.progress?.done) * trials;
  const total = n(run?.progress?.total ?? run?.examples) * trials;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  // the capability contract: a finished upload reads its real outcome (the one
  // derivation): every row errored is Failed, some errored is Finished
  // with errors, never Completed.
  const outcome = /^(Completed|PartialCompleted)$/.test(status)
    ? runOutcomeKey(run)
    : null;
  if (outcome === "Failed")
    return { state: "failed", text: "Failed", done, total, pct };
  if (outcome)
    return {
      state: "completed",
      text: `${outcome === "PartialCompleted" ? "Finished with errors" : "Completed"} · ${count(total, "result", "results")} received`,
      done: total,
      total,
      pct: 100,
    };
  if (/Evaluation|Scoring/i.test(status))
    return { state: "scoring", text: "Scoring", done, total, pct };
  if (/Running|Queued/i.test(status) || done > 0)
    return {
      state: "receiving",
      text: `Receiving · ${fmt(done)} of ${fmt(total)}`,
      done,
      total,
      pct,
    };
  if (/Cancel|Stopped/i.test(status))
    return { state: "stopped", text: "Stopped", done, total, pct };
  if (/Fail/i.test(status))
    return { state: "failed", text: "Failed", done, total, pct };
  return { state: "waiting", text: "Waiting for results", done, total, pct };
}

/**
 * SDK · Preview status from the S1 session (`state`), the run (carry-over)
 * and the clock (stalled = no heartbeat for 2 min while running).
 * @param {object} session  S1 body
 * @param {{carried?: {attempts: number, passed: number, from: string},
 *   stoppedBy?: string, now?: number}} [opts]
 * @returns {{state, text, sub?, done, total, pct}}
 */
export function sdkStatus(session, { carried, stoppedBy, now } = {}) {
  const results = streamedResults(session);
  const received = n(session?.received ?? results.length);
  const expected = n(session?.expected ?? session?.attempts_total);
  const failed = n(
    session?.failed ?? results.filter((r) => r.passed === false).length,
  );
  const pct = expected
    ? Math.min(100, Math.round((received / expected) * 100))
    : 0;
  const base = { done: received, total: expected, pct };
  const state = session?.state ?? "waiting";
  const last = Date.parse(session?.last_heartbeat_at ?? "");
  const stalled =
    (state === "running" || state === "connected") &&
    Number.isFinite(last) &&
    (now ?? Date.now()) - last > STALL_MS;
  if (stalled)
    return {
      ...base,
      state: "stalled",
      text: "No heartbeat for 2 min. Your script may have stopped.",
    };
  if (state === "waiting")
    return {
      ...base,
      state: "waiting",
      text: "Waiting for results…",
      sub: "Leave this open or close it; the run keeps waiting.",
    };
  if (state === "connected")
    return { ...base, state: "connected", text: "Connected" };
  if (state === "running")
    return {
      ...base,
      state: "running",
      text: results.some((r) => r.passed === true || r.passed === false)
        ? `Running · ${fmt(received)} of ${fmt(expected)} attempts · ${fmt(failed)} failed so far`
        : `Running · ${fmt(received)} of ${fmt(expected)} attempts`,
    };
  if (state === "scoring")
    return {
      ...base,
      state: "scoring",
      text: `Scoring evals · ${fmt(received)} of ${fmt(expected)}`,
    };
  if (state === "stopped")
    return {
      ...base,
      state: "stopped",
      text: `Stopped by ${stoppedBy || "you"} at ${fmt(received)} of ${fmt(expected)}. Your script was told to finish its current rows.`,
    };
  if (state === "stalled" || session?.status === "failed")
    return {
      ...base,
      state: "failed",
      text: `Marked as failed at ${fmt(received)} of ${fmt(expected)} attempts.`,
    };
  // completed
  const passed = Math.max(0, received - failed);
  if (carried?.attempts) {
    const totalAttempts = received + n(carried.attempts);
    return {
      ...base,
      pct: 100,
      state: "completed",
      text: `Done · ${fmt(passed)} of ${fmt(received)} new attempts passed · run total ${fmt(passed + n(carried.passed))} of ${fmt(totalAttempts)} (${fmt(carried.attempts)} carried from ${carried.from})`,
    };
  }
  // Uploads without pass info (works-today style results): just the count.
  if (!results.some((r) => r.passed === true || r.passed === false))
    return {
      ...base,
      pct: 100,
      state: "completed",
      text: `Done · ${count(received, "attempt", "attempts")} received`,
    };
  const { passing, rows } = rowsPassingEveryTrial(results);
  return {
    ...base,
    pct: 100,
    state: "completed",
    text: `Done · ${fmt(passed)} of ${fmt(received)} attempts passed · ${fmt(passing)} of ${fmt(rows)} rows pass every trial`,
  };
}
