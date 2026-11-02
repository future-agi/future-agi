/*
  The pass-rate change between two runs over the scenarios both played, with
  its 95% interval. Ported from the prototype's `_mock/pairs.js`
  (pairedInterval, verdictLabelOf); the maths is unchanged.
*/

/** Points either side of zero inside which a change isn't meaningful. */
export const NO_CHANGE_POINTS = 10;
/** Below this many comparable pairs there is no aggregate, only rows. */
export const PAIR_FLOOR = 10;

const DEFAULT_BETWEEN = 0.035;
const Z95 = 1.96;

const smooth = (p, k) => (p * k + 0.5) / (k + 1);
const mean = (xs) =>
  xs.length ? xs.reduce((a, x) => a + x, 0) / xs.length : 0;

/**
 * Pass-rate delta over paired scenarios with a 95% interval. Trials are
 * nested in scenarios: the paired differences carry the between-scenario
 * spread, and each scenario's binomial trial noise floors it (normal
 * approximation, deterministic).
 * @param {{ a: number, b: number, ka: number, kb: number }[]} diffs
 *   Each scenario's pass share in the baseline (`a`) and the newer run (`b`),
 *   with the trials each share rests on.
 * @returns {{ n: number, delta: ?number, lo: ?number, hi: ?number, half: ?number }}
 *   In points.
 */
export const pairedInterval = (diffs) => {
  const n = diffs.length;
  if (!n) return { n: 0, delta: null, lo: null, hi: null, half: null };
  const d = diffs.map((x) => x.b - x.a);
  const m = mean(d);
  const s2 =
    n > 1 ? d.reduce((a, x) => a + (x - m) ** 2, 0) / (n - 1) : DEFAULT_BETWEEN;
  const trial = mean(
    diffs.map((x) => {
      const pa = smooth(x.a, x.ka);
      const pb = smooth(x.b, x.kb);
      return (pa * (1 - pa)) / x.ka + (pb * (1 - pb)) / x.kb;
    }),
  );
  const half = Z95 * Math.sqrt(Math.max(s2, trial) / n);
  const r = (v) => Math.round(v * 1000) / 10;
  return {
    n,
    delta: r(m),
    lo: r(m - half),
    hi: r(m + half),
    half: r(half),
    s2,
    trial,
  };
};

/** Improved, Regressed, No meaningful change or Inconclusive; rows only below the floor. */
export const verdictLabelOf = (iv, band = NO_CHANGE_POINTS) => {
  if (!iv || iv.n < PAIR_FLOOR) {
    return {
      id: "rows",
      label: "Rows only",
      note: `Fewer than ${PAIR_FLOOR} comparable pairs. No aggregate, read the rows.`,
    };
  }
  if (iv.lo > 0) {
    return {
      id: "improved",
      label: "Improved",
      note: "The whole interval is above zero.",
    };
  }
  if (iv.hi < 0) {
    return {
      id: "regressed",
      label: "Regressed",
      note: "The whole interval is below zero.",
    };
  }
  if (iv.lo >= -band && iv.hi <= band) {
    return {
      id: "same",
      label: "No meaningful change",
      note: `The whole interval sits inside ±${band} points.`,
    };
  }
  return {
    id: "inconclusive",
    label: "Inconclusive",
    note: `The interval crosses zero and reaches past ±${band} points.`,
  };
};
