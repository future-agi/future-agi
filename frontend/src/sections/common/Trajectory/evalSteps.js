// W1-9 B3 (notify E39) · the eval steps of one try (pure). After the task
// steps, the trajectory lists one step per eval or check that judged the
// try, read from the spans' `eval_scores` (the scorer's own cells: verdict,
// reason, judge model, judge time and cost, eval trace). Failing first.
import { fmtCost, fmtMs } from "src/sections/common/format/fmt";
import { verdictKind } from "./stepBadges";

const ORDER = { fail: 0, error: 1, na: 2, skipped: 3, not_reached: 4, pass: 5 };
const WORD = {
  fail: "Fail",
  pass: "Pass",
  error: "Score error",
  na: "N/A",
  skipped: "Skipped",
  not_reached: "Not reached",
};

/**
 * Eval steps of a trace for one run (`runId` / `runLabel`; entries of other
 * runs that judged the same try are left out).
 * @returns {{key: string, name: string, kind: string, verdict: string,
 *   score: number|null, reason: string|null, judgeModel: string|null,
 *   ms: number|null, costUsd: number|null, evalTraceId: string|null,
 *   inputs: object|null, version: string|null, spanName: string}[]}
 */
export function evalStepsOf(spans, { runId = null, runLabel = null } = {}) {
  const out = [];
  const walk = (entry) => {
    const s = entry?.observation_span ?? {};
    (entry?.eval_scores ?? []).forEach((e) => {
      if (runId && e.run_id && e.run_id !== runId) return;
      if (!runId && runLabel && e.run_label && e.run_label !== runLabel) return;
      const kind = verdictKind(e);
      out.push({
        key: `${e.eval_config_id ?? e.eval_name}|${s.id ?? ""}`,
        name: e.check_key ?? e.eval_name ?? "eval",
        kind,
        verdict: WORD[kind],
        score: typeof e.score === "number" ? e.score : null,
        reason: e.explanation ?? e.skipped_reason ?? null,
        judgeModel: e.judge_model ?? null,
        ms: Number.isFinite(e.judge_latency_ms) ? e.judge_latency_ms : null,
        costUsd: Number.isFinite(e.judge_cost_usd) ? e.judge_cost_usd : null,
        evalTraceId: e.eval_trace_id ?? null,
        inputs: e.inputs_used ?? null,
        version: e.version_id ?? null,
        spanName: s.name ?? "",
      });
    });
    (entry?.children ?? []).forEach(walk);
  };
  (spans ?? []).forEach(walk);
  return out
    .map((x, i) => ({ x, i }))
    .sort((a, b) => ORDER[a.x.kind] - ORDER[b.x.kind] || a.i - b.i)
    .map(({ x }) => x);
}

/**
 * `Correctness · judge gpt-4o · Fail · "reason" · 1.2 s · $0.0004`; parts
 * the cell does not record are left out (never a made-up time or price).
 * N/A and score errors read `N/A · reason` / `Score error · reason`.
 */
export function evalStepLine(step) {
  const verdict =
    step.kind === "na" || step.kind === "error"
      ? `${step.verdict}${step.reason ? ` · ${step.reason}` : ""}`
      : step.verdict;
  const quote =
    step.reason && step.kind !== "na" && step.kind !== "error"
      ? `"${step.reason}"`
      : null;
  return [
    step.name,
    step.judgeModel ? `judge ${step.judgeModel}` : null,
    verdict,
    quote,
    step.ms != null ? fmtMs(step.ms) : null,
    step.costUsd != null ? fmtCost(step.costUsd) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
