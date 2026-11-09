// Explore aggregates and the paired mean difference (W5-2 B1). Pure. One
// definition each, the same formulas as the results aggregate the results
// header and R5 use (results-math `aggregate` / `compareRuns`); the parity
// test `w5-b-agg-parity.test.js` pins them against it.

const Z = 1.96;
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export const meanOf = (xs) =>
  xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

export function medianOf(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Nearest-rank quantile (`ceil(q·n) − 1`), as results-math's p50 / p99. */
export function quantileOf(xs, q) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.ceil(q * s.length) - 1))];
}

export const minOf = (xs) => (xs.length ? Math.min(...xs) : null);
export const maxOf = (xs) => (xs.length ? Math.max(...xs) : null);

/** Sample standard deviation (n − 1); 0 under 2 values, as results-math. */
export function sdOf(xs) {
  if (xs.length < 2) return 0;
  const m = meanOf(xs);
  return Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1));
}

/** One aggregate of plain numbers: avg · median · p90 · min · max · std. */
export function aggOf(values, agg) {
  const xs = (values ?? []).filter(isNum);
  if (!xs.length) return null;
  switch (agg) {
    case "median":
      return medianOf(xs);
    case "p90":
      return quantileOf(xs, 0.9);
    case "min":
      return minOf(xs);
    case "max":
      return maxOf(xs);
    case "std":
      return sdOf(xs);
    default:
      return meanOf(xs);
  }
}

/**
 * Paired difference of two runs on the rows both have a value for (the
 * results-math `compareRuns` rule): delta = mean(cand − base), its 95%
 * half-width = 1.96 · sd / √n, significant when |delta| > half-width.
 * @param {Map<string, number>} cand  row id → value
 * @param {Map<string, number>} base  row id → value
 * @returns {{delta: number|null, ci95: number|null, n: number,
 *   significant: boolean, low: number|null, high: number|null}}
 */
export function pairedMeanDelta(cand, base) {
  const diffs = [];
  cand.forEach((c, id) => {
    const b = base.get(id);
    if (isNum(c) && isNum(b)) diffs.push(c - b);
  });
  const n = diffs.length;
  const delta = n ? meanOf(diffs) : null;
  const ci95 = n > 1 ? (Z * sdOf(diffs)) / Math.sqrt(n) : null;
  return {
    delta,
    ci95,
    n,
    significant: delta != null && ci95 != null && Math.abs(delta) > ci95,
    low: delta != null && ci95 != null ? delta - ci95 : null,
    high: delta != null && ci95 != null ? delta + ci95 : null,
  };
}
