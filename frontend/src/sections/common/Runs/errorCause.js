// W1-7 (MUST-BUILD W1-7, W1-SCOPE A4): the ONE error classifier. A try that
// errored is grouped by its cause, read from what the try recorded (the
// error text, a failed tool call), never from a fixture id: the mock (R1
// `errors_by_cause`, R3 `error_cause`, Rerun failed rows' backoff) and the
// screens (the results status line, the leaderboard's errored cell, the
// Errored filter) all import it. Pure.

const CLASS = /^\s*([A-Z]\w*(?:Error|Exception))\b/;
const COST_CAP = /\bcost cap\b/i;
const MAX_TURNS = /\bmax(?:imum)? (?:tool )?(?:turns|iterations|steps)\b/i;
const TIMEOUT = /\btimed? ?out\b|\btimeout\b/i;
// A sandbox that never started ("Setup failed: image pull timed out").
const SETUP = /^\s*setup failed\b/i;
const HTTP = /\bHTTP (\d{3})\b/;
// "Failed at step 4 · lookup_customer 404", "lookup_invoice timeout after 30s"
const TOOL_AT_STEP = /Failed at step \d+\s*·\s*([a-z][a-z0-9_]*)\b/i;
const TOOL_TIMEOUT = /\b([a-z][a-z0-9]*_[a-z0-9_]+) (?:timed out|timeout)\b/i;
// W6 A1 (M9, integration H4): a try its card's Limits per row stopped
// ("Stopped at 1,411 tokens", "Stopped at 3 messages", "Stopped at $0.00030
// for this row"); a timed-out one ("Timed out after 2 s") only when its
// record names that stop (integration's limits slot, trace_summary.stop). A try
// whose own failure came first keeps that failure as its cause.
const ROW_LIMIT =
  /^\s*Stopped at (?:[\d,]+ (?:tokens|messages)\b|\$[\d.,]+ for this row\b)/i;
const ROW_TIMEOUT = /^\s*Timed out after\b/i;
const ROW_LIMIT_KINDS = new Set([
  "tokens",
  "messages",
  "cost_usd",
  "timeout_s",
]);
export const ROW_LIMIT_CAUSE = "stopped at limit per row";

const textOf = (v) =>
  typeof v === "string"
    ? v
    : v && typeof v === "object"
      ? String(v.message ?? v.text ?? v.error ?? "")
      : "";

/** The error text a try recorded (its own, else its attempt's). */
export function errorTextOf(record = {}) {
  return (
    textOf(record.error) ||
    textOf(record.attempt_error) ||
    textOf(record.summary?.error) ||
    ""
  );
}

// The first tool call the try made that failed: {name, error}.
function failedToolOf(record = {}) {
  const calls = Array.isArray(record.tool_calls) ? record.tool_calls : [];
  const c = calls.find(
    (x) => x && (x.error || x.status === "error" || x.ok === false),
  );
  if (!c) return null;
  return { name: String(c.name ?? c.tool ?? "tool"), error: textOf(c.error) };
}

/**
 * The cause of one errored try, in the words the chips show:
 * an exception class (`RateLimitError`), `stopped at limit per row`,
 * `stopped at cost cap`, `max turns`,
 * `setup failed`, `tool timeout · <tool>`, `tool error · <tool>`, `HTTP 502`, `timeout`, or
 * `other error`. Null for a try that did not error.
 */
export function errorCauseOf(record = {}) {
  const text = errorTextOf(record);
  const errored =
    record.status === "error" ||
    record.attempt_status === "error" ||
    Boolean(text);
  if (!errored) return null;
  const cls = CLASS.exec(text);
  if (cls) return cls[1];
  if (
    ROW_LIMIT.test(text) ||
    (ROW_TIMEOUT.test(text) &&
      ROW_LIMIT_KINDS.has(record.trace_summary?.stop?.kind))
  )
    return ROW_LIMIT_CAUSE;
  if (COST_CAP.test(text)) return "stopped at cost cap";
  if (MAX_TURNS.test(text)) return "max turns";
  if (SETUP.test(text)) return "setup failed";
  const tool = failedToolOf(record);
  const toolTimeout = TOOL_TIMEOUT.exec(text);
  if (toolTimeout) return `tool timeout · ${toolTimeout[1]}`;
  if (tool)
    return TIMEOUT.test(tool.error)
      ? `tool timeout · ${tool.name}`
      : `tool error · ${tool.name}`;
  const atStep = TOOL_AT_STEP.exec(text);
  if (atStep) return `tool error · ${atStep[1]}`;
  const http = HTTP.exec(text);
  if (http) return `HTTP ${http[1]}`;
  if (TIMEOUT.test(text)) return "timeout";
  return "other error";
}

/** Whether a cause is a rate limit (it gets a backoff before it runs again). */
export const isRateLimitCause = (cause) =>
  /ratelimit|rate limit|^HTTP 429$/i.test(String(cause ?? ""));

/**
 * The wait the error text asks for before a retry, in seconds ("Please try
 * again in 1.4s", "in 850ms", "retry after 2 seconds"), or null when the
 * text names none.
 */
export function retryAfterSecondsOf(text) {
  const t = String(text ?? "");
  const m =
    /\b(?:try again|retry) (?:in|after) (\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec(?:ond)?s?)\b/i.exec(
      t,
    );
  if (!m) return null;
  const v = Number(m[1]);
  if (!Number.isFinite(v)) return null;
  return /^m/i.test(m[2]) ? v / 1000 : v;
}

/**
 * Errored rows grouped by cause, most rows first (then by name). A row is
 * errored when every try it finished errored (`status: "error"`), the rule
 * of the run's error count and the Errored filter; its cause is its first
 * errored try's. `records` are the tries a run has finished.
 * @returns {{cause: string, rows: number}[]}
 */
export function errorsByCause(records = []) {
  const byRow = new Map();
  records.forEach((r) => {
    if (!r) return;
    const key = r.example_id ?? r.row_id ?? r.id;
    const cur = byRow.get(key) ?? { all: true, first: null };
    cur.all = cur.all && r.status === "error";
    if (!cur.first && r.status === "error") cur.first = r;
    byRow.set(key, cur);
  });
  const counts = new Map();
  byRow.forEach(({ all, first }) => {
    if (!all || !first) return;
    const cause = errorCauseOf(first);
    if (!cause) return;
    const cur = counts.get(cause) ?? { rows: 0, wait: null };
    cur.rows += 1;
    const wait = retryAfterSecondsOf(errorTextOf(first));
    if (wait != null) cur.wait = Math.max(cur.wait ?? 0, wait);
    counts.set(cause, cur);
  });
  return [...counts.entries()]
    .map(([cause, { rows, wait }]) => ({
      cause,
      rows,
      // The longest wait the cause's error texts ask for before a retry.
      ...(wait != null ? { retry_after_s: wait } : {}),
    }))
    .sort((a, b) => b.rows - a.rows || a.cause.localeCompare(b.cause));
}

/**
 * The line a Rerun failed rows dialog adds when rows hit a rate limit.
 * L7c g5 (info:rerun-failed:backoff-line): it says exactly what the engine
 * does (rerun-backoff: rate-limited rows play last and wait until the delay
 * their error names is over, which may already be the case), with the
 * verb agreeing with the count:
 * `1 rate-limited row runs again after the other rows, once the 2.1 s wait
 * its error names is over.` Empty when no row hit a rate limit, and for a
 * task suite's runs (the engine holds no task try back). `runs` are R1 runs.
 */
export function backoffLine(runs = []) {
  let rows = 0;
  let wait = null;
  (runs ?? []).forEach((r) => {
    if (r?.ledger?.primary_key === "eval:task_success") return;
    (r?.errors_by_cause ?? []).forEach((c) => {
      if (!isRateLimitCause(c.cause)) return;
      rows += c.rows;
      if (c.retry_after_s != null) wait = Math.max(wait ?? 0, c.retry_after_s);
    });
  });
  if (!rows) return "";
  const one = rows === 1;
  const who = `${rows} rate-limited ${one ? "row" : "rows"} ${one ? "runs" : "run"} again after the other rows`;
  return wait != null
    ? `${who}, once the ${Number(wait.toFixed(1))} s wait ${one ? "its error names" : "their errors name"} is over.`
    : `${who} (the error names no wait).`;
}

/** `RateLimitError 14 · tool timeout · lookup_invoice 3` (one line). */
export const causesText = (list = []) =>
  list.map((c) => `${c.cause} ${c.rows}`).join(" · ");
