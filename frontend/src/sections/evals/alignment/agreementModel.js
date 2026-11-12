// W7-6 B1/B2: pure helpers of the Agreement tab. Numbers come from
// the agreement response; nothing here invents a target, a share or a seed.
import { rowsPill } from "src/sections/common/Results/filterPills";
import {
  datasetGridHref,
  resultsGridHref,
} from "src/sections/common/Analysis/analysisLinks";
import { rowIdsFilter } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import { costClause, judgeCallsCost } from "./judgeCost";

export const DISAGREE_LABEL = "judge ≠ reviewers";
export const SPLIT_REQUIRED =
  "Choose how many labelled rows to draft from and a seed";
// Make an editable copy (W7-SCOPE §2.6).
export { COPY_INSTRUCTIONS, copyNameOf } from "./editableCopy";
export const NOT_BETTER =
  "The draft does not agree better on rows it never saw. Publishing it may only fit these labels.";

/** 0.8438 -> "84%"; null -> "–". */
export const pctText = (v) =>
  v == null || !Number.isFinite(Number(v)) ? "–" : `${Math.round(v * 100)}%`;
/** κ 0.6178 -> "0.62". */
export const kappaText = (v) =>
  v == null || !Number.isFinite(Number(v)) ? "–" : Number(v).toFixed(2);

/** "3d" / "5h" / "12m" / "now" since an ISO time. */
export function agoText(iso, now = Date.now()) {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return null;
  const mins = Math.max(0, Math.floor((now - t) / 60000));
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/**
 * Adapt the stored GET report once at the host boundary. Its identifiers are
 * flat and its disagreements contain scalar verdicts, not reviewer records.
 * Keep enriched reports intact; missing outputs, notes, rescoring and all-pairs
 * data cannot be inferred from the stored endpoint's capped disagreements.
 */
export function agreementReportForView(report) {
  if (!report || report.source_key != null || report.key == null) return report;
  const {
    key,
    binding_id,
    dataset_id,
    label_id,
    label_type,
    binding_name,
    dataset_name,
    label_name,
  } = report;
  // React does not render boolean children. Preserve false and zero as text.
  const verdict = (value) => ({
    verdict: value == null ? null : String(value),
  });
  return {
    ...report,
    source_key: key,
    source: {
      key,
      binding_id,
      dataset_id,
      label_id,
      label_type,
      binding_name,
      dataset_name,
      label_name,
    },
    disagreements: (report.disagreements ?? []).map((item) => ({
      row_id: item.row_id,
      row_label: item.row_id,
      human: verdict(item.human),
      judge: verdict(item.judge),
    })),
  };
}

/** Source list line: `GS · Correct? · 96 rows · 84% · κ 0.62 · last labelled 3d`. */
export function sourceLine(s, now = Date.now()) {
  const where = s.dataset_name ?? s.run_label ?? "";
  const parts = [where, s.binding_name, s.label_name];
  if (s.n != null) parts.push(`${s.n} ${s.n === 1 ? "row" : "rows"}`);
  if (s.accuracy != null)
    parts.push(pctText(s.accuracy), `κ ${kappaText(s.kappa)}`);
  else if (s.spearman != null) parts.push(`ρ ${kappaText(s.spearman)}`);
  const ago = agoText(s.last_at, now);
  if (ago) parts.push(`last labelled ${ago}`);
  return parts.filter(Boolean).join(" · ");
}

/** `Accuracy 84% (76–90%) · κ 0.62 · n 96`. */
export function headlineOf(r) {
  if (!r) return "";
  const ci =
    r.ci95?.[0] != null
      ? ` (${Math.round(r.ci95[0] * 100)}–${Math.round(r.ci95[1] * 100)}%)`
      : "";
  return `Accuracy ${pctText(r.accuracy)}${ci} · κ ${kappaText(r.kappa)} · n ${r.n}`;
}

/** Target line: null until a target is typed; else Reached / Below by N pts. */
export function targetLine(accuracy, target) {
  const t = Number(target);
  if (
    target === "" ||
    target == null ||
    !Number.isFinite(t) ||
    accuracy == null
  )
    return null;
  const pts = Math.round(t - accuracy * 100);
  return pts <= 0 ? "Reached" : `Below target by ${pts} pts`;
}

/** Row ids of a matrix cell (human label row h, judge label column j). */
export function cellRows(pairs, labels, h, j) {
  return (pairs ?? [])
    .filter(
      (p) => p.human?.verdict === labels[h] && p.judge?.verdict === labels[j],
    )
    .map((p) => p.row_id);
}

/**
 * Open rows: a dataset source opens the Data grid on exactly these rows
 * (`system:row_ids`); a run source opens its results with a rows pill.
 */
export function openRowsHref(source, ids, label = DISAGREE_LABEL) {
  if (!source || !ids?.length) return null;
  if (source.run_id)
    return resultsGridHref({
      mode: source.experiment_id ? "experiment" : "compare",
      datasetId: source.dataset_id,
      experimentId: source.experiment_id,
      runIds: [source.run_id],
      pills: [rowsPill(ids, { label })],
    });
  return datasetGridHref(source.dataset_id, [rowIdsFilter(ids, { label })]);
}

/** `confidence 0.5–0.6` for a bin range. */
export const confidenceLabel = (x0, x1) =>
  `confidence ${Number(x0).toFixed(1)}–${Number(x1).toFixed(1)}`;

/** Rows of bins [from..to] (inclusive), for click or drag. */
export function binRows(bins, from, to) {
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  return (bins ?? []).slice(lo, hi + 1).flatMap((b) => b.row_ids ?? []);
}

/**
 * The compare confirm: both judges re-scored on the labelled rows, each
 * call priced by the one estimator (custom models at their own prices).
 */
export function compareConfirm(n, currentModel, otherModel, customPrices) {
  const cost = judgeCallsCost(n, [currentModel, otherModel], customPrices);
  return {
    calls: cost.calls,
    usd: cost.usd,
    text: `Score ${n} labelled rows with ${otherModel} and again with ${currentModel ?? "the current judge"} · ${cost.calls} judge calls · ${costClause(cost)}`,
  };
}

/** Improve step 1: both inputs typed and valid, else the disabled reason. */
export function splitReason(share, seed) {
  const s = Number(share);
  if (
    share === "" ||
    share == null ||
    !Number.isFinite(s) ||
    s <= 0 ||
    s >= 100
  )
    return SPLIT_REQUIRED;
  if (seed === "" || seed == null || String(seed).trim() === "")
    return SPLIT_REQUIRED;
  return null;
}

/** Improve step 3 confirm: the current definition and the draft, both at its judge. */
export function improveConfirm(n, model, customPrices) {
  const cost = judgeCallsCost(n, [model, model], customPrices);
  return `Score ${n} labelled rows twice (current, draft) · ${cost.calls} judge calls · ${costClause(cost)}`;
}

/** Accuracy on a set of per-row verdicts (the walk's own recomputation). */
export function accuracyOn(verdicts, part, key) {
  const rows = (verdicts ?? []).filter((v) => !part || v.part === part);
  const scored = rows.filter((v) => v[key]);
  if (!scored.length) return null;
  return scored.filter((v) => v[key] === v.human).length / scored.length;
}

/** The candidate's line under it: who and which rows it came from. */
export function candidateFrom(c) {
  if (c.source === "note")
    return c.from.notes
      .map((n) => `from ${n.by ?? "a reviewer"}'s note on ${n.row_label}`)
      .join("; ");
  if (c.source === "false_fails") return `from ${c.from.row_labels.join(", ")}`;
  return `in every false pass (${c.from.row_labels.join(", ")}), in no true pass`;
}

/** `Correctness (aligned) v2 saved. Runs scored with v1 …`. */
export const savedToast = (name, n) =>
  `${name} v${n} saved. Runs scored with v${n - 1} can be re-scored on v${n} from their results.`;

/** The Agreement tab of an eval on one dataset label (Improve this judge). */
export const improveJudgeHref = (pair, datasetId) =>
  pair?.eval_template_id && datasetId && pair?.label_id
    ? `/dashboard/evaluations/${pair.eval_template_id}?tab=agreement&dataset=${datasetId}&label=${pair.label_id}`
    : null;
