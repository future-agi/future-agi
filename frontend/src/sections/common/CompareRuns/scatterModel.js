// Two-run scatter (W5-6 B5). Pure. One point per row both runs have a value
// for: x = the baseline's per-row value of a measure, y = the candidate's.
// A rate measure's per-row value is the share of the row's scored attempts
// that pass; a score / numeric measure's is the mean of its attempts.
// Jitter is for display only: selection always uses the true values.
import { attemptsOf } from "src/sections/common/Analysis/workbench/workbenchModel";

const ok = (v) => typeof v === "number" && Number.isFinite(v);

/** The per-row value of a measure on one matrix cell, or null. */
export function rowValueOf(cell, measure) {
  if (!cell) return null;
  const attempts = attemptsOf(cell, measure).filter(Boolean);
  if (measure?.kind === "rate") {
    const scored = attempts.filter((a) => a.pass !== undefined);
    if (!scored.length) return null;
    return scored.reduce((s, a) => s + a.pass, 0) / scored.length;
  }
  const xs = attempts.map((a) => a.x).filter(ok);
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

/**
 * @param {object} matrix  R3 shape=matrix
 * @param {{baselineId: string, candidateId: string, measure: object}} opts
 * @returns {Array<{id: string, label: string, x: number, y: number}>}
 */
export function scatterPoints(matrix, { baselineId, candidateId, measure }) {
  if (!baselineId || !candidateId || !measure) return [];
  const out = [];
  (matrix?.rows ?? []).forEach((row) => {
    const x = rowValueOf(row.cells?.[baselineId], measure);
    const y = rowValueOf(row.cells?.[candidateId], measure);
    if (!ok(x) || !ok(y)) return;
    out.push({
      id: row.row_id,
      label: row.label ?? `Row ${row.n}`,
      x,
      y,
    });
  });
  return out;
}

/** FNV-1a of a string → [0, 1). */
function unitHash(text) {
  let h = 0x811c9dc5;
  const s = String(text);
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h / 0x100000000;
}

/**
 * Display jitter for discrete values: `{dx, dy}` in data units, a fraction
 * of the span (`share`), the same for a row id on every render.
 */
export function jitterOf(id, span, share) {
  const s = (Number(span) || 0) * (Number(share) || 0);
  return {
    dx: (unitHash(`${id}:x`) - 0.5) * s,
    dy: (unitHash(`${id}:y`) - 0.5) * s,
  };
}

/** True when the values are few enough to stack (≤ 12 distinct). */
export function isDiscrete(points) {
  const xs = new Set();
  points.forEach((p) => {
    xs.add(p.x);
    xs.add(p.y);
  });
  return xs.size <= 12;
}

/** `{min, max}` over x and y together, padded so the diagonal is square. */
export function extentOf(points, measure) {
  if (measure?.kind === "rate") return { min: 0, max: 1 };
  const vals = points.flatMap((p) => [p.x, p.y]);
  if (!vals.length) return { min: 0, max: 1 };
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (min === max) {
    min -= 0.5;
    max += 0.5;
  }
  const pad = (max - min) * 0.05;
  return { min: min - pad, max: max + pad };
}

/**
 * Points inside a brush rectangle (data units, true values), split by
 * direction: `worse` = the candidate's value is worse than the baseline's
 * for the measure's direction, `better` the opposite, `same` equal.
 * @param {Array<{id, x, y}>} points
 * @param {{x0: number, x1: number, y0: number, y1: number}} rect
 * @param {{higherIsBetter?: boolean}} measure
 */
export function brushSelect(points, rect, measure) {
  const [xa, xb] = [Math.min(rect.x0, rect.x1), Math.max(rect.x0, rect.x1)];
  const [ya, yb] = [Math.min(rect.y0, rect.y1), Math.max(rect.y0, rect.y1)];
  const hib = measure?.higherIsBetter !== false;
  const out = { ids: [], worse: [], better: [], same: [] };
  points.forEach((p) => {
    if (p.x < xa || p.x > xb || p.y < ya || p.y > yb) return;
    out.ids.push(p.id);
    if (p.y === p.x) out.same.push(p.id);
    else if (p.y > p.x === hib) out.better.push(p.id);
    else out.worse.push(p.id);
  });
  return out;
}

/** `14 rows selected · 9 got worse · 5 got better`. */
export function selectionText(sel) {
  const n = sel?.ids?.length ?? 0;
  const parts = [`${n} ${n === 1 ? "row" : "rows"} selected`];
  parts.push(`${sel?.worse?.length ?? 0} got worse`);
  parts.push(`${sel?.better?.length ?? 0} got better`);
  if (sel?.same?.length) parts.push(`${sel.same.length} unchanged`);
  return parts.join(" · ");
}
