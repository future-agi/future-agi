// The printable Summary (W5-5 B8, AG-03). Pure. Every number comes from the
// same R5 analysis and R1 runs the Summary page reads; config differences
// come from the one config diff (CompareRuns/configRows.js).
import { configRows } from "src/sections/common/CompareRuns/configRows";
import { wilson } from "src/sections/common/Analysis/runStats";
import { fmtCost, fmtPct } from "src/sections/common/format/fmt";
import {
  LOW_SAMPLE_N,
  formatCi,
} from "src/sections/common/Metrics/metricFormat";
import {
  chipLabel,
  summaryVerdicts,
} from "src/sections/experiment-detail/ExperimentSummary/Analysis/verdictItems";

const ok = (v) => typeof v === "number" && Number.isFinite(v);
const RATE_TYPES = new Set(["pass_fail", "thumbs"]);
const isRate = (metric) =>
  metric.family === "eval" && RATE_TYPES.has(metric.output_type);

/**
 * The Summary verdict card's words: `Better +25.4 pts — <sentence>`. A
 * sentence that already opens with the chip's words (`Too close to call:
 * #2A … vs baseline #1A, …`) is printed once, never `Too close to call —
 * Too close to call: …`.
 */
export function printCardLine(verdict) {
  const label = chipLabel(verdict);
  const sentence = verdict?.sentence ?? "";
  if (
    label &&
    sentence &&
    sentence.toLowerCase().startsWith(label.toLowerCase())
  )
    return sentence;
  return [label, sentence].filter(Boolean).join(" — ");
}

/** One metric value as the Summary prints it. */
export function fmtMetric(metric, v) {
  if (!ok(v)) return "—";
  if (isRate(metric)) return `${(v * 100).toFixed(1)}%`;
  // L7c g6: the one money format (2 significant figures under a cent).
  if (/cost/.test(metric.key)) return fmtCost(v);
  if (/latency/.test(metric.key))
    return `${Math.round(v).toLocaleString("en-US")} ms`;
  if (/tokens|steps/.test(metric.key)) return v.toFixed(1);
  return v.toFixed(2);
}

/**
 * A value's 95% interval as the Summary prints it.
 * - A rate (pass/fail, thumbs): the Wilson interval over its n, bounded to
 *   0–100% (`runStats.wilson`, the interval the Summary's All runs table
 *   prints: `allRunsModel.successCi`, `49.0–94.3%`).
 * - A mean (scores, time, cost, tokens): `±` R5 `per_run.ci95` in the
 *   metric's own format (`metricFormat.formatCi`, the Summary's metric
 *   cards: `±0.05`, `±0.3 s`).
 */
export function ciText(metric, value, n, half) {
  if (!ok(value)) return null;
  if (isRate(metric)) {
    if (!ok(n) || n <= 0) return null;
    const { low, high } = wilson(value, n);
    return `${fmtPct(low).replace("%", "")}–${fmtPct(high)}`;
  }
  if (!ok(half)) return null;
  return formatCi(metric, half) || null;
}

/**
 * The verdict line of one candidate. R5 holds a sentence only once it can
 * judge the run; a suppressed verdict prints the Summary's own reason (its
 * trade-offs line: the run has not finished and fewer than LOW_SAMPLE_N
 * rows are comparable), with the comparable count from R5's low-sample
 * caveat. Never an empty line.
 */
export function verdictLine(v) {
  if (v?.sentence) return { text: v.sentence, reason: false };
  const shared = (v?.caveats ?? []).find((c) => c.kind === "low_sample");
  const so = ok(shared?.count) ? ` (${shared.count} comparable so far)` : "";
  if (v?.suppressed || v?.low_sample)
    return {
      text: `No verdict yet: the verdict is available when the run finishes or ${LOW_SAMPLE_N} rows are comparable${so}.`,
      reason: true,
    };
  return { text: "No verdict for this run yet.", reason: true };
}

/**
 * @param {{analysis: object, runs: object[], baselineId: string|null}} args
 *   `runs` = R1 runs in view (label, name, config), baseline first
 */
export function printModel({ analysis, runs, baselineId }) {
  const list = runs ?? [];
  const labelOf = (id) => list.find((r) => r.run_id === id)?.label ?? id;
  // `verdict: null` = no verdict yet; `sentence` then holds the reason.
  // L7c g6 (print:view): a run the Summary's verdict card judges prints
  // that card's words (chip label + sentence, verdictItems.summaryVerdicts),
  // so the print never contradicts the Summary behind it.
  const cards = new Map(
    summaryVerdicts(analysis).map((i) => [i.run.run_id, i.verdict]),
  );
  const verdicts = (analysis?.verdicts ?? []).map((v) => {
    const card = cards.get(v.run_id);
    return {
      runId: v.run_id,
      label: labelOf(v.run_id),
      verdict: card ? card.tone ?? "card" : v.verdict ?? null,
      sentence: card ? printCardLine(card) : verdictLine(v).text,
    };
  });
  const metrics = (analysis?.metrics ?? [])
    .filter((m) => ["eval", "ops", "agent"].includes(m.family))
    .map((m) => {
      const cells = list.map((r) => {
        const pr = (m.per_run ?? []).find((x) => x.run_id === r.run_id);
        return {
          runId: r.run_id,
          value: ok(pr?.value) ? pr.value : null,
          n: pr?.n ?? 0,
          text: fmtMetric(m, pr?.value),
          ci: ciText(m, pr?.value, pr?.n, pr?.ci95),
        };
      });
      const valued = cells.filter((c) => c.value != null);
      let best = null;
      let worst = null;
      if (valued.length >= 2 && m.higher_is_better != null) {
        const sorted = [...valued].sort((a, b) =>
          m.higher_is_better ? b.value - a.value : a.value - b.value,
        );
        if (sorted[0].value !== sorted[sorted.length - 1].value) {
          best = sorted[0].runId;
          worst = sorted[sorted.length - 1].runId;
        }
      }
      return { key: m.key, name: m.name, cells, best, worst };
    });
  const base = list.find((r) => r.run_id === baselineId) ?? null;
  const configDiffs = base
    ? list
        .filter((r) => r.run_id !== baselineId && r.config && base.config)
        .map((r) => ({
          label: r.label,
          chips: configRows(base, r).chips.map((c) => c.text ?? c.label),
        }))
    : [];
  // The worst rows: the verdict's `Got worse` tasks (biggest drop first),
  // else the regressed rows, as the Summary's worklist orders them.
  const tabs = analysis?.worklist?.tabs ?? {};
  const worstItems = tabs.worse?.items?.length
    ? tabs.worse.items
    : tabs.regressed?.items ?? analysis?.worklist?.items ?? [];
  const worst = worstItems.slice(0, 10).map((it) => ({
    id: it.example_id,
    row: it.row_order,
    input: it.input_preview ?? "",
    regressed: (it.regressed ?? [])
      .map((x) => {
        const m = (analysis?.metrics ?? []).find((mm) => mm.key === x.metric);
        return m
          ? `${m.name} ${fmtMetric(m, x.baseline)} → ${fmtMetric(m, x.candidate)}`
          : null;
      })
      .filter(Boolean)
      .join(" · "),
  }));
  return {
    baselineLabel: base?.label ?? null,
    worklistRun: analysis?.worklist?.run_id
      ? labelOf(analysis.worklist.run_id)
      : null,
    runs: list.map((r) => ({ id: r.run_id, label: r.label, name: r.name })),
    verdicts,
    metrics,
    configDiffs,
    worst,
  };
}
