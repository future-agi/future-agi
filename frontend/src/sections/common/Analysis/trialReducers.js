// W1-8 (MUST-BUILD W1-8, W1-SCOPE A9): how a row's tries become one value,
// and a run's number with its 95% interval. One implementation: the mock's
// R3 aggregates (results-wire), the results Aggregate menu, Review's
// "Score each row from its tries" and the leaderboard read these. Pure.
//
// A try is a value in 0..1 (a pass is 1, a fail 0, a score its value), or
// "error" (the try errored), or "infra" (its infrastructure failed), or
// null / "running" (not scored or not finished: left out of every reducer).
//   pass_at_k, pass_hat_k and at_least count an errored or infra try as a
//   failed try whatever the pass mark: a try that did not pass did not
//   pass;
//   mode and max read the scored tries only;
//   mean and median count an errored try as 0.
// The Results read's per-try aggregates (results_grid.per_try_aggregates:
// mode, max, pass_at_k, pass_hat_k, at_least:<n>) follow the first two
// rules.
//
// Reducers of one row's tries (k = the run's tries per row):
//   mean        the mean of the scored tries
//   median      their median
//   mode        the most common verdict (pass / fail); a tie is a fail
//               (owner decision 6). A run's number (reduceRun) reads a
//               score eval's tries as verdicts at its pass mark too, so
//               "Most common" is always a share of rows with a Wilson
//               interval; reduceRow on raw score values keeps
//               the most common value, the lower on a tie. Only the
//               scored tries count
//   max         the best scored try
//   pass_at_k   1 when at least one of the first k tries passes
//   pass_hat_k  1 when all of the first k tries pass. Each row is read
//               against its own tries (its first k when it has more): a
//               row with one that did not count is left out, and a row
//               planned with fewer than k tries is read on those
//   at_least    1 when at least n tries pass (`at_least:2`)
// A run's number is the mean of its rows' values. Rates (every reducer of a
// pass / fail eval, and pass_at_k / pass_hat_k / at_least of a score) carry
// the Wilson 95% interval; means of scores carry mean ± 1.96·sd/√n.
import { wilson } from "./runStats";

export const REDUCERS = Object.freeze([
  "mean",
  "median",
  "mode",
  "max",
  "pass_at_k",
  "pass_hat_k",
  "at_least",
]);

const Z = 1.96;
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const valueOf = (t) => (t === true ? 1 : t === false ? 0 : t);
// Tries that ran and failed to give an answer.
const FAILED_TRIES = new Set(["error", "infra"]);
// The reducers that read whether each try passed.
const PASS_FAMILY = ["pass_at_k", "pass_hat_k", "at_least"];
// Mean, median and flakiness: an errored try is 0.
const scoredOf = (tries) =>
  (tries ?? []).map((t) => (t === "error" ? 0 : valueOf(t))).filter(isNum);
// Mode and max: the scored tries only.
const judgedOf = (tries) => (tries ?? []).map(valueOf).filter(isNum);
// pass@k, pass^k and at least n: each try's verdict (true / false), or null
// for a try that does not count. An errored or infra try is a failed try
// whatever the pass mark, so it never reaches the comparison.
const verdictOf = (t, pass) => {
  if (FAILED_TRIES.has(t)) return false;
  const x = valueOf(t);
  return isNum(x) ? pass(x) : null;
};
const triedOf = (tries, pass) =>
  (tries ?? []).map((t) => verdictOf(t, pass)).filter((v) => v !== null);

/** `{kind, n}` from a stored reducer or an R3 `agg` value (`at_least:2`). */
export function parseReducer(value) {
  if (value && typeof value === "object")
    return {
      kind: REDUCERS.includes(value.kind) ? value.kind : "mean",
      ...(value.kind === "at_least"
        ? { n: Math.max(1, Number(value.n) || 1) }
        : {}),
    };
  const text = String(value ?? "mean");
  const m = /^at_least:(\d+)$/.exec(text);
  if (m) return { kind: "at_least", n: Math.max(1, Number(m[1])) };
  if (text === "avg") return { kind: "mean" };
  return { kind: REDUCERS.includes(text) ? text : "mean" };
}

/** The R3 `agg` value of a reducer: `pass_hat_k`, `at_least:2`. */
export const aggOfReducer = (r) => {
  const p = parseReducer(r);
  return p.kind === "at_least" ? `at_least:${p.n}` : p.kind;
};

/**
 * One row's value under a reducer.
 * @param {Array<number|"error"|"infra"|null>} tries  the row's planned
 *   tries, in try order (null for one not scored or not finished)
 * @param {{kind?: string, n?: number, k?: number, threshold?: number,
 *   passFail?: boolean}} o
 * @returns {number|null}  null when no try counts (for pass^k, when one of
 *   the row's own first k tries does not count)
 */
export function reduceRow(tries, o = {}) {
  const { kind, n } = parseReducer(
    o.kind === "at_least" ? { kind: "at_least", n: o.n } : o.kind,
  );
  const bar = Number.isFinite(o.threshold) ? o.threshold : 0.5;
  const pass = (x) => x >= bar - 1e-9;
  if (PASS_FAMILY.includes(kind)) {
    const ys = triedOf(tries, pass);
    if (!ys.length) return null;
    const k = Math.max(1, Number(o.k) || (tries ?? []).length);
    if (kind === "pass_at_k") return ys.slice(0, k).some(Boolean) ? 1 : 0;
    if (kind === "pass_hat_k") {
      // Each row against its own planned tries (its first k when it has
      // more): a row with one of them that did not count is left out.
      const own = (tries ?? []).slice(0, k);
      const counted = triedOf(own, pass);
      if (counted.length < own.length) return null;
      return counted.every(Boolean) ? 1 : 0;
    }
    return ys.filter(Boolean).length >= (n ?? 1) ? 1 : 0;
  }
  const xs =
    kind === "mode" || kind === "max" ? judgedOf(tries) : scoredOf(tries);
  if (!xs.length) return null;
  switch (kind) {
    case "median": {
      const s = [...xs].sort((a, b) => a - b);
      const mid = Math.floor(s.length / 2);
      return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
    }
    case "mode": {
      if (o.passFail !== false && xs.every((x) => x === 0 || x === 1)) {
        const passes = xs.filter(pass).length;
        return passes > xs.length - passes ? 1 : 0;
      }
      const counts = new Map();
      xs.forEach((x) => counts.set(x, (counts.get(x) ?? 0) + 1));
      const top = Math.max(...counts.values());
      return Math.min(
        ...[...counts].filter(([, c]) => c === top).map(([x]) => x),
      );
    }
    case "max":
      return Math.max(...xs);
    default:
      return xs.reduce((a, b) => a + b, 0) / xs.length;
  }
}

/** Mean ± 1.96·sd/√n of values; [lo, hi] or null for fewer than 2. */
export function meanInterval(values) {
  const xs = (values ?? []).filter(isNum);
  if (xs.length < 2) return null;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  const sd = Math.sqrt(
    xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1),
  );
  const h = (Z * sd) / Math.sqrt(xs.length);
  return [m - h, m + h];
}

/** The Wilson 95% interval of a share; [lo, hi] or null with no rows. */
export function rateInterval(rate, n) {
  if (!isNum(rate) || !(n > 0)) return null;
  const w = wilson(rate, n);
  return [w.low, w.high];
}

/** Whether a reducer's run number is a share of rows (Wilson interval). */
export function isRateReducer(kind, { passFail = true } = {}) {
  const p = parseReducer(kind).kind;
  if (["pass_at_k", "pass_hat_k", "at_least", "mode"].includes(p)) return true;
  return passFail && p === "max";
}

/**
 * A row's tries as verdicts at the pass mark: an errored or infra try fails
 * whatever the pass mark.
 */
export const verdictsAt = (tries, threshold = 0.5) =>
  (tries ?? []).map((t) => {
    if (FAILED_TRIES.has(t)) return 0;
    const x = valueOf(t);
    return isNum(x) ? (x >= threshold - 1e-9 ? 1 : 0) : x;
  });

/**
 * A run's number under a reducer over its rows' tries, with its 95%
 * interval: `{value, n, ci95, kind, k}`.
 * @param {Array<Array<number|"error"|null>>} rowsTries
 */
export function reduceRun(rowsTries, o = {}) {
  const r = parseReducer(
    o.kind === "at_least" ? { kind: "at_least", n: o.n } : o.kind,
  );
  // Review M6: "Most common" of a score eval is the most common verdict at
  // its pass mark (a tie is a fail), never the most common raw value. Like
  // every Most common, it reads the scored tries only.
  const verdictMode = r.kind === "mode" && o.passFail === false;
  const bar = Number.isFinite(o.threshold) ? o.threshold : 0.5;
  const values = (rowsTries ?? [])
    .map((t) =>
      verdictMode
        ? reduceRow(verdictsAt(judgedOf(t), bar), {
            ...o,
            ...r,
            passFail: true,
          })
        : reduceRow(t, { ...o, ...r }),
    )
    .filter(isNum);
  const n = values.length;
  const value = n ? values.reduce((a, b) => a + b, 0) / n : null;
  // A pass / fail eval's number is always a share (Wilson); a score's is a
  // share under Most common, pass@k, pass^k and at least n.
  const rate =
    o.passFail !== false || isRateReducer(r.kind, { passFail: false });
  return {
    kind: r.kind,
    ...(r.n ? { n_pass: r.n } : {}),
    k: Number(o.k) || null,
    value,
    n,
    ci95: rate ? rateInterval(value, n) : meanInterval(values),
  };
}

/**
 * How unsure a row is across its tries: 1 − |2p − 1| (p = its pass share;
 * 0 = every try agrees, 1 = an even split), and for a score eval the try
 * standard deviation. Null with fewer than 2 scored tries.
 * @returns {{score: number, p: number, sd: number}|null}
 */
export function flakinessOf(tries, { threshold = 0.5 } = {}) {
  const xs = scoredOf(tries);
  if (xs.length < 2) return null;
  const p = xs.filter((x) => x >= threshold - 1e-9).length / xs.length;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  const sd = Math.sqrt(
    xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1),
  );
  const r3 = (x) => Math.round(x * 1000) / 1000;
  return { score: r3(1 - Math.abs(2 * p - 1)), p: r3(p), sd: r3(sd) };
}

/** The reducer's name as menus and headers print it (`pass^3`, `At least 2 of 3`). */
export function reducerLabel(r, k = null) {
  const p = parseReducer(r);
  const kk = Number(k) || "k";
  switch (p.kind) {
    case "median":
      return "Median";
    case "mode":
      return "Most common";
    case "max":
      return "Best";
    case "pass_at_k":
      return `pass@${kk}`;
    case "pass_hat_k":
      return `pass^${kk}`;
    case "at_least":
      return `At least ${p.n} of ${kk}`;
    default:
      return "Mean";
  }
}

const pct = (x) => `${Math.round(x * 100)}%`;
/**
 * A run's number with its interval, as headers and hover cards print it:
 * `pass^3 · 41% (33–49%)`; a score mean `Mean · 0.72 (0.68–0.76)`.
 */
export function reducedText(res, { k = null, rate = true } = {}) {
  if (!res || !isNum(res.value)) return null;
  const label = reducerLabel(
    res.kind === "at_least" ? { kind: "at_least", n: res.n_pass } : res.kind,
    k ?? res.k,
  );
  const f = rate ? pct : (x) => x.toFixed(2);
  const ci = res.ci95
    ? ` (${f(Math.max(rate ? 0 : -Infinity, res.ci95[0]))}–${f(Math.min(rate ? 1 : Infinity, res.ci95[1]))})`
    : "";
  return `${label} · ${f(res.value)}${ci}`;
}

/**
 * The 95% interval from a summary (`avg`, `std`, `n`): Wilson for a share
 * (pass / fail), mean ± 1.96·sd/√n for a score. Null with no rows.
 */
export function intervalOfSummary({ avg, std, n }, { rate = false } = {}) {
  if (!isNum(avg) || !(n > 0)) return null;
  if (rate) return rateInterval(avg, n);
  if (!(n > 1) || !isNum(std)) return null;
  const h = (Z * std) / Math.sqrt(n);
  return [avg - h, avg + h];
}
