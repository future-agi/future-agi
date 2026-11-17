import {
  EMPTY,
  fmtNumber,
  fmtPct,
  fmtScore,
} from "src/sections/common/format/fmt";
// Readers for the dataset Summary tab (PRODUCT-SPEC §3.14). The API stays
// snake_case; these hooks pass the Summary scope (`?version=`, split) and
// shape what the cards read, so no card reads a camelCase key the API never
// sends (R1–R6).
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import camelCase from "lodash/camelCase";
import axios, { endpoints } from "src/utils/axios";

/** Deep-convert object keys to camelCase (values untouched). */
export function camelizeKeys(value) {
  if (Array.isArray(value)) return value.map(camelizeKeys);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value).map(([k, v]) => [camelCase(k), camelizeKeys(v)]),
  );
}

/** Query params for the Summary scope; empty values are dropped. */
export function scopeParams({ splitIds, version } = {}) {
  const params = {};
  if (splitIds?.length) params.split_ids = splitIds.join(",");
  if (version != null && version !== "") params.version = version;
  return params;
}

/** GET eval-stats (one entry per eval template, `result[]` per dataset eval). */
export function useEvalStats(datasetId, { columnIds, splitIds, version } = {}) {
  return useQuery({
    queryKey: [
      "evals-summary",
      datasetId,
      columnIds ?? [],
      splitIds ?? [],
      version ?? null,
    ],
    queryFn: () =>
      axios.get(endpoints.dataset.evalsSummary(datasetId), {
        params: {
          ...(columnIds?.length ? { column_ids: columnIds.join(",") } : {}),
          ...scopeParams({ splitIds, version }),
        },
      }),
    select: (res) => res?.data?.result || [],
    enabled: Boolean(datasetId),
  });
}

/** GET annotation-summary (labels, annotators, judge vs human, review header). */
export function useAnnotationSummary(datasetId, { splitIds, version } = {}) {
  return useQuery({
    queryKey: [
      "annotation-summary",
      datasetId,
      splitIds ?? [],
      version ?? null,
    ],
    queryFn: () =>
      axios.get(endpoints.dataset.annotationSummary(datasetId), {
        params: scopeParams({ splitIds, version }),
      }),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(datasetId),
  });
}

/** GET splits (N6): `[{id, name, color, row_count}]`. */
export function useSummarySplits(datasetId) {
  const query = useQuery({
    queryKey: ["dataset-splits", datasetId],
    queryFn: () => axios.get(endpoints.develop.splits.list(datasetId)),
    select: (res) => res?.data?.result?.splits ?? [],
    enabled: Boolean(datasetId),
    staleTime: 30 * 1000,
  });
  return query.data ?? [];
}

/**
 * One row per dataset eval, flattened from eval-stats: name, kind, version,
 * direction, threshold, pass rate, mean, histogram, scored, errors and the
 * grid filters that open its failing rows.
 */
export function flattenEvalStats(stats) {
  const out = [];
  (stats ?? []).forEach((t) => {
    (t.result ?? []).forEach((r) => {
      const passFail = t.output_type === "Pass/Fail";
      out.push({
        id: r.id,
        templateId: t.id,
        templateName: t.name,
        name: r.name ?? t.name,
        outputType: t.output_type,
        passFail,
        version: r.evaluator_version ?? null,
        latestVersion: r.latest_version ?? null,
        higherIsBetter: r.higher_is_better ?? null,
        passThreshold: passFail ? null : r.pass_threshold ?? null,
        passRate: r.pass_rate ?? null,
        mean: passFail ? null : r.mean ?? null,
        histogram: r.histogram ?? [],
        scored: r.scored ?? r.total_cells ?? null,
        totalRows: r.total_rows ?? null,
        errors: r.errors ?? 0,
        failing: r.below_threshold ?? null,
        failingFilters: r.failing_filters ?? [],
        columnId: r.column_id ?? null,
      });
    });
  });
  return out;
}

export function useEvalOverview(datasetId, scope) {
  const query = useEvalStats(datasetId, scope);
  const rows = useMemo(() => flattenEvalStats(query.data), [query.data]);
  return { ...query, rows };
}

/** The eval histogram in MiniHistogram's shape for one pseudo-run `rows`. */
export function toMiniHistogram(histogram, passFail) {
  const bins = (histogram ?? []).map((b) => ({
    ...b,
    counts: { rows: b.count ?? 0 },
  }));
  return { kind: passFail ? "categorical" : "score", bins };
}

/** The judge-vs-human pair to show for an eval: most rows, then agreement. */
export function bestAgreementFor(pairs, evalId) {
  return (
    (pairs ?? [])
      .filter((p) => p.eval_id === evalId)
      .sort((a, b) => b.n - a.n || b.agreement_pct - a.agreement_pct)[0] ?? null
  );
}

// One formatter (F1): every number on the dataset Summary goes through fmt.js.
export const pct = (v, digits = 1) =>
  v == null || !Number.isFinite(Number(v))
    ? EMPTY
    : fmtPct(Number(v), { dp: digits });

export const num = (v, digits = 2) =>
  v == null || !Number.isFinite(Number(v))
    ? EMPTY
    : digits === 2
      ? fmtScore(Number(v))
      : fmtNumber(Number(Number(v).toFixed(digits)));

/**
 * Landis & Koch bands for Cohen's kappa (AS#46): `{word, tone}`; tone is
 * `good` (≥ 0.61), `fair` (0.21–0.60) or `poor`.
 */
export function kappaBand(k) {
  if (k == null || !Number.isFinite(Number(k))) return null;
  const v = Number(k);
  if (v >= 0.81) return { word: "almost perfect", tone: "good" };
  if (v >= 0.61) return { word: "substantial", tone: "good" };
  if (v >= 0.41) return { word: "moderate", tone: "fair" };
  if (v >= 0.21) return { word: "fair", tone: "fair" };
  if (v > 0) return { word: "slight", tone: "poor" };
  return { word: "none beyond chance", tone: "poor" };
}

/** `Cohen's kappa 0.62 = substantial agreement on 96 paired rows`. */
export function kappaText(k, n) {
  const band = kappaBand(k);
  if (!band) return "Kappa needs 2 raters on the same rows.";
  return `Cohen's kappa ${fmtScore(Number(k))} = ${band.word} agreement on ${n} paired ${n === 1 ? "row" : "rows"}. Kappa discounts the agreement two raters would reach by chance.`;
}

/**
 * Sort the evals table (AS#46). Keys: name, passRate, mean, scored, errors,
 * failing, agreement. Missing values sort last either way.
 */
export function sortEvalRows(rows, key, dir = "asc", agreementOf) {
  const val = (r) => {
    if (key === "name") return String(r.name ?? "").toLowerCase();
    if (key === "agreement") return agreementOf?.(r)?.agreement_pct ?? null;
    return r[key] ?? null;
  };
  const sign = dir === "desc" ? -1 : 1;
  return [...(rows ?? [])].sort((a, b) => {
    const va = val(a);
    const vb = val(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "string") return sign * va.localeCompare(vb);
    return sign * (va - vb);
  });
}

/** "38 rows below 0.70" / "47 rows fail" / "38 rows above 0.30". */
export function failingText(row) {
  const n = row.failing ?? 0;
  const rows = `${n} ${n === 1 ? "row" : "rows"}`;
  if (row.passFail) return `${rows} fail`;
  if (row.passThreshold == null) return `${rows} fail`;
  return row.higherIsBetter === false
    ? `${rows} above ${num(row.passThreshold)}`
    : `${rows} below ${num(row.passThreshold)}`;
}
