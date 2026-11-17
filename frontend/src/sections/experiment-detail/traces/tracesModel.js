// W5-8 Traces view (RV-14; W5-SCOPE §7 C6): the pure rules shared by the
// mock server (w5RunTraces.js) and the view (TracesView.jsx).
//
//   traceStatusOf(result)            -> "ok" | "error" | "infra"
//   failedEvalKeys(scores, defOf)    -> eval keys the try failed, by each
//                                       eval's own definition
//   percentile(values, p)            -> nearest-rank percentile
//   summarize(traces)                -> {traces, errors, infra, p50_ms, p90_ms, cost_usd}
//   summaryLine(summary)             -> "30 traces · 4 errors · p50 2.1 s · $0.09"
//   runTracesView(page, {status})    -> a page of experiment-runs/<run>/traces/
//                                       as the view's rows
//
// No "Open in Tracing" link: Observe's trace list scopes a link only through
// an `observeLinkFilter` row, and in the prototype it lists no experiment-run
// traces, so a link would land on an empty list. The option stays absent
// until Observe (H-5) lists a run's traces and applies that row on the trace
// attribute `run_id`.

import { formatUsdCost } from "src/utils/format-usd-cost";

/** A try's trace status from its stored result status. */
export function traceStatusOf(result) {
  const s = result?.status;
  if (s === "infra") return "infra";
  if (s === "error" || result?.visibility === "error") return "error";
  return "ok";
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * True when a score fails its eval's own definition: a pass/fail eval below
 * its pass value (1), a scored eval past its pass threshold in its direction.
 * A scored eval without a threshold has no pass or fail.
 */
export function scoreFails(def, value) {
  if (!def || !isNum(value)) return false;
  if (def.output_type === "pass_fail") return value < 1;
  const t = def.uem_threshold ?? def.pass_threshold;
  if (!isNum(t)) return false;
  return def.higher_is_better === false ? value > t : value < t;
}

/** The eval keys a try failed (in the try's own score order). */
export function failedEvalKeys(scores, defOf) {
  return Object.entries(scores ?? {})
    .filter(([key, s]) => scoreFails(defOf(key), s?.value))
    .map(([key]) => key);
}

/** Nearest-rank percentile over the finite values (null when none). */
export function percentile(values, p) {
  const xs = (values ?? []).filter(isNum).sort((a, b) => a - b);
  if (!xs.length) return null;
  const rank = Math.ceil((p / 100) * xs.length);
  return xs[Math.min(xs.length, Math.max(1, rank)) - 1];
}

/** The summary over a list of traces (before paging). */
export function summarize(traces) {
  const list = traces ?? [];
  const durations = list.map((t) => t.duration_ms);
  const costs = list.map((t) => t.cost_usd).filter(isNum);
  return {
    traces: list.length,
    errors: list.filter((t) => t.status === "error").length,
    infra: list.filter((t) => t.status === "infra").length,
    p50_ms: percentile(durations, 50),
    p90_ms: percentile(durations, 90),
    cost_usd: costs.length
      ? Number(costs.reduce((a, b) => a + b, 0).toFixed(6))
      : null,
  };
}

const plural = (n, one, many = `${one}s`) =>
  `${Number(n).toLocaleString()} ${n === 1 ? one : many}`;

/** 2100 -> "2.1 s"; 850 -> "850 ms". */
export function fmtDuration(ms) {
  if (!isNum(ms)) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
}

/** Shared money display, retaining the trace model's numeric-only input. */
export function fmtCost(usd) {
  return formatUsdCost(isNum(usd) ? usd : null, "–");
}

/** "30 traces · 4 errors · 1 infra · p50 2.1 s · $0.09" (parts with no data left out). */
export function summaryLine(summary) {
  if (!summary) return "";
  const parts = [plural(summary.traces ?? 0, "trace")];
  parts.push(plural(summary.errors ?? 0, "error"));
  if (summary.infra) parts.push(`${summary.infra.toLocaleString()} infra`);
  if (isNum(summary.p50_ms)) parts.push(`p50 ${fmtDuration(summary.p50_ms)}`);
  if (isNum(summary.p90_ms)) parts.push(`p90 ${fmtDuration(summary.p90_ms)}`);
  if (isNum(summary.cost_usd)) parts.push(fmtCost(summary.cost_usd));
  return parts.join(" · ");
}

// A run item's status (pending, running, completed, failed, skipped) as the
// view's trace status; statuses with no trace meaning show as they are.
const ITEM_TRACE_STATUS = { completed: "ok", failed: "error" };

const shortId = (value) =>
  typeof value === "string" && value.length > 16
    ? `${value.slice(0, 8)}…`
    : value ?? "–";

/**
 * One page of GET /model-hub/experiment-runs/{run_id}/traces/
 * (`{traces, items_with_trace, offset, has_more, …}`) as the view's
 * `{summary, count, results, failed_evals, offset, has_more}`. The route
 * returns no duration, tokens, cost, failed evals or first error, so those
 * stay empty ("–"). `status` filters this page.
 */
export function runTracesView(page, { status } = {}) {
  if (!page) return page;
  const all = (page.traces ?? []).map((t) => ({
    trace_id: t.trace_id,
    project_id: t.project_id ?? null,
    row_label: shortId(t.example_id),
    example_id: t.example_id,
    trial: t.trial_number,
    status: ITEM_TRACE_STATUS[t.item_status] ?? t.item_status ?? "ok",
    duration_ms: null,
    tokens: null,
    cost_usd: null,
    failed_evals: [],
    first_error: null,
  }));
  const results = status ? all.filter((t) => t.status === status) : all;
  return {
    summary: summarize(all),
    count: results.length,
    results,
    failed_evals: [],
    offset: page.offset ?? 0,
    has_more: Boolean(page.has_more),
  };
}

/** Status filter options for the view. */
export const TRACE_STATUSES = [
  { value: "", label: "Any status" },
  { value: "ok", label: "OK" },
  { value: "error", label: "Error" },
  { value: "infra", label: "Infra" },
];
