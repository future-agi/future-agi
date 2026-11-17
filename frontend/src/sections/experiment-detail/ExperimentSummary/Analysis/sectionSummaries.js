// One-line answers for the Summary's collapsible Sections (one view A5-02).
// A collapsed Section still says its answer; every line is computed from the
// analysis object the cards already use, never typed. `null` = cannot be
// computed: the header then shows the title (and count) only. Pure.
import {
  fmtCost,
  fmtCount,
  fmtMs,
  fmtNumber,
  fmtPct,
} from "src/sections/common/format/fmt";
import { formatDelta } from "src/sections/common/Metrics/metricFormat";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const labelOf = (analysis, runId) =>
  (analysis?.runs ?? []).find((r) => r.run_id === runId)?.label ?? null;
const isRate = (m) =>
  m?.output_type === "pass_fail" || m?.output_type === "thumbs";

/** The eval metrics of an analysis (family `eval`). */
export const evalMetricsOf = (analysis) =>
  (analysis?.metrics ?? []).filter((m) => m.family === "eval");

/**
 * `lowest pass rate: Task reply rubric v1 64.2% on #2A` — the lowest pass
 * rate of any pass/fail eval on any run in view.
 */
export function evalsSummary(analysis) {
  let low = null;
  evalMetricsOf(analysis)
    .filter(isRate)
    .forEach((m) =>
      (m.per_run ?? []).forEach((p) => {
        if (!p?.n || !isNum(p.value)) return;
        if (!low || p.value < low.value)
          low = { metric: m, runId: p.run_id, value: p.value };
      }),
    );
  if (!low) return null;
  const run = labelOf(analysis, low.runId);
  if (!run) return null;
  return `lowest pass rate: ${low.metric.name} ${fmtPct(low.value)} on ${run}`;
}

/**
 * `#2D slowest p50 74 s · #2A cheapest $0.0040 per task` — the same
 * Time per task (p50) and Cost per task as All runs.
 */
export function opsSummary(analysis) {
  const runs = analysis?.runs ?? [];
  const slow = runs
    .filter((r) => isNum(r.latency_p50_ms))
    .sort((a, b) => b.latency_p50_ms - a.latency_p50_ms)[0];
  const cheap = runs
    .filter((r) => isNum(r.cost_per_row))
    .sort((a, b) => a.cost_per_row - b.cost_per_row)[0];
  const parts = [
    slow && `${slow.label} slowest p50 ${fmtMs(slow.latency_p50_ms)}`,
    cheap && `${cheap.label} cheapest ${fmtCost(cheap.cost_per_row)} per task`,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * `12 of 30 rows reviewed · judge agrees 83%` — the pairwise review of the
 * page's candidate (else the first one); without pairwise data, the rubric
 * labels count (`2 rubric labels`); nothing reviewed → null.
 */
export function humanSummary(analysis) {
  const prefs = analysis?.preference ?? {};
  const p =
    prefs[analysis?.candidate_run_id] ?? Object.values(prefs).find(Boolean);
  if (p && isNum(p.items) && isNum(p.judged)) {
    const agree = p.judge?.agreement?.pct;
    return [
      `${p.judged} of ${p.items} rows reviewed`,
      isNum(agree) ? `judge agrees ${fmtPct(agree, { dp: 0 })}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  const rubric = analysis?.human ?? [];
  return rubric.length ? fmtCount(rubric.length, "rubric label") : null;
}

/** `#2D takes the most steps (16.0 per task)`. */
export function agentSummary(analysis) {
  const top = (analysis?.agent ?? [])
    .filter((a) => isNum(a.avg_steps))
    .sort((a, b) => b.avg_steps - a.avg_steps)[0];
  const run = top ? labelOf(analysis, top.run_id) : null;
  if (!run) return null;
  return `${run} takes the most steps (${fmtNumber(top.avg_steps)} per task)`;
}

/**
 * `2 splits · biggest gap: No split (#2A −41.7 pts)` — the group where a run
 * moved most against the baseline on the breakdown's metric.
 */
export function breakdownSummary(analysis) {
  const bd = analysis?.breakdown;
  const groups = bd?.groups ?? [];
  if (!groups.length) return null;
  const metric = (analysis?.metrics ?? []).find((m) => m.key === bd.metric);
  const noun = (bd.group_by ?? "split") === "split" ? "split" : "group";
  let gap = null;
  groups.forEach((g) =>
    Object.entries(g.vs_baseline ?? {}).forEach(([runId, v]) => {
      if (!isNum(v?.delta)) return;
      if (!gap || Math.abs(v.delta) > Math.abs(gap.delta))
        gap = { group: g.label, runId, delta: v.delta };
    }),
  );
  const count = fmtCount(groups.length, noun);
  if (!gap || !metric) return count;
  const run = labelOf(analysis, gap.runId);
  const delta = formatDelta(metric, gap.delta);
  return `${count} · biggest gap: ${gap.group} (${run ? `${run} ` : ""}${delta})`;
}

/** The Section titles, in page order (one view A5-02 / A5-03). */
export const SUMMARY_SECTIONS = Object.freeze([
  { id: "evals", title: "Evals" },
  { id: "human", title: "Human review" },
  { id: "ops", title: "Latency and cost" },
  { id: "agent", title: "Agent steps" },
  { id: "breakdown", title: "Breakdown by split" },
]);
