// What an iteration moved (R2-SPEC §4.3.2): per-row better / worse /
// same, fixed / still failing / new failures on the rerun rows, pass@1,
// pass^k and cost per attempt, and the two-sided sign test. Pure.
import {
  passAt1,
  passHatK,
  signTest,
} from "src/sections/common/Analysis/runStats";
import { cellRate, fmtPct, fmtUsd } from "./iterateModel";
// W1-9 B6 (review M2): the one Failed rule (the dock's chip reads it too).
import { failsSome } from "./failedRule";

export { failsSome };

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const trialsOf = (cell) => (Array.isArray(cell?.trials) ? cell.trials : []);
const passesAll = (cell) => {
  const r = cellRate(cell);
  return r !== null && r === 1;
};
const passCount = (cell) => trialsOf(cell).filter((t) => t === 1).length;
// Points between two rates as shown (one decimal each), so the delta reads
// as the difference of the two numbers on screen.
const ptsBetween = (from, to) =>
  Math.round(to * 1000) / 10 - Math.round(from * 1000) / 10;

const meanCost = (rows, runId) => {
  let s = 0;
  let n = 0;
  rows.forEach((r) =>
    (r.cells?.[runId]?.cost_usd ?? []).forEach((c) => {
      if (isNum(c)) {
        s += c;
        n += 1;
      }
    }),
  );
  return n ? s / n : null;
};

/**
 * Compare a candidate run with its base on the rows both ran.
 * @param {{rows: object[]}} matrix  R3 shape=matrix with both runs
 * @param {string} baseId
 * @param {string} candId
 * @param {{k?: number, exclude?: string[]|Set<string>}} [opts]  k for
 *   pass^k (default: the base's trials). W1-9 B6 (review M4): `exclude` =
 *   rows whose verdict no score backs (unscoredModel.verdictLeftOut): they
 *   stay rerun / carried rows but count in no better / worse / same, fixed /
 *   still failing / new failure and no pass@1 / pass^k (`unscored` lists
 *   them).
 */
export function summarizeIteration(matrix, baseId, candId, opts = {}) {
  const left = new Set(opts.exclude ?? []);
  const all = (matrix?.rows ?? []).filter(
    (r) => r.cells?.[baseId] && r.cells?.[candId],
  );
  const rows = all.filter((r) => !left.has(r.row_id));
  const out = {
    rows: all.length,
    better: [],
    worse: [],
    same: [],
    rerun: [],
    carried: [],
    fixed: [],
    stillFailing: [],
    newFailures: [],
    worseTrials: [],
    unscored: [],
  };
  all.forEach((r) => {
    if (!left.has(r.row_id)) return;
    out.unscored.push(r.row_id);
    if (r.cells[candId].carried_from) out.carried.push(r.row_id);
    else out.rerun.push(r.row_id);
  });
  rows.forEach((r) => {
    const a = r.cells[baseId];
    const b = r.cells[candId];
    const ra = cellRate(a);
    const rb = cellRate(b);
    if (ra !== null && rb !== null && rb > ra) out.better.push(r.row_id);
    else if (ra !== null && rb !== null && rb < ra) {
      out.worse.push(r.row_id);
      out.worseTrials.push(passCount(a) - passCount(b));
    } else out.same.push(r.row_id);
    if (b.carried_from) {
      out.carried.push(r.row_id);
      return;
    }
    out.rerun.push(r.row_id);
    if (failsSome(a)) {
      if (passesAll(b)) out.fixed.push(r.row_id);
      else out.stillFailing.push(r.row_id);
    } else if (failsSome(b)) out.newFailures.push(r.row_id);
  });
  const k =
    opts.k ?? Math.max(1, ...all.map((r) => trialsOf(r.cells[baseId]).length));
  const cells = (id) => rows.map((r) => trialsOf(r.cells[id]));
  out.k = k;
  out.pass1 = { from: passAt1(cells(baseId)), to: passAt1(cells(candId)) };
  out.passK = {
    from: passHatK(cells(baseId), k),
    to: passHatK(cells(candId), k),
  };
  out.cost = { from: meanCost(all, baseId), to: meanCost(all, candId) };
  out.sign = signTest(out.better.length, out.worse.length);
  return out;
}

/**
 * IT-R5 G3 (P4): the rows the new run still fails (some try fails), in row
 * order; rows that also failed on the base first (still failing), then new
 * failures. Carried rows count: they are the new run's results too. Pure.
 */
export function stillFailingRows(matrix, baseId, candId) {
  const rows = (matrix?.rows ?? []).filter(
    (r) => r.cells?.[baseId] && r.cells?.[candId] && failsSome(r.cells[candId]),
  );
  const both = rows.filter((r) => failsSome(r.cells[baseId]));
  const fresh = rows.filter((r) => !failsSome(r.cells[baseId]));
  return [...both, ...fresh].map((r) => r.row_id);
}

/** `significant` / `within noise` from the sign test. */
export const verdictWord = (sign) =>
  sign?.significant ? "significant" : "within noise";

/** `3%` / `18%` / `<1%` from a p-value. */
export function pPercent(p) {
  if (!isNum(p)) return "—";
  const pct = Math.round(p * 100);
  return pct < 1 ? "<1%" : `${pct}%`;
}

/** `p = 0.031` (2 significant figures). */
export const pText = (p) => (isNum(p) ? `p = ${Number(p.toPrecision(2))}` : "");

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/**
 * The banner's one sentence.
 * - partial (rows carried over from the base): `#2F reran 6 rows: 6 fixed,
 *   0 still failing, 0 new failures. 24 rows carried over from #2B.`
 * - otherwise: `#2G ran all 30 rows: 7 better, 2 worse (1 trial each,
 *   within noise), 21 same.`
 */
export function bannerSentence(s, { candLabel, baseLabel, partial }) {
  if (partial) {
    const carried = s.carried.length
      ? ` ${count(s.carried.length, "row", "rows")} carried over from ${baseLabel}.`
      : "";
    return `${candLabel} reran ${count(s.rerun.length, "row", "rows")}: ${s.fixed.length} fixed, ${s.stillFailing.length} still failing, ${count(s.newFailures.length, "new failure", "new failures")}.${carried}`;
  }
  const each =
    s.worseTrials.length && s.worseTrials.every((d) => d === s.worseTrials[0])
      ? `${count(s.worseTrials[0], "trial", "trials")} each, `
      : "";
  return `${candLabel} ran all ${s.rows} rows: ${s.better.length} better, ${s.worse.length} worse (${each}${verdictWord(s.sign)}), ${s.same.length} same.`;
}

/** `pass@1 65.6% → 85.6% (+20.0 pts, significant)`. */
export function pass1Chip(s) {
  const from = s.pass1.from.rate;
  const to = s.pass1.to.rate;
  if (!isNum(from) || !isNum(to)) return null;
  const pts = ptsBetween(from, to);
  const sign = pts >= 0 ? "+" : "−";
  return `pass@1 ${fmtPct(from)} → ${fmtPct(to)} (${sign}${Math.abs(pts).toFixed(1)} pts, ${verdictWord(s.sign)})`;
}

/** `pass^3 56.7% → 76.7%`. */
export function passKChip(s) {
  const from = s.passK.from.rate;
  const to = s.passK.to.rate;
  if (!isNum(from) || !isNum(to) || s.k < 2) return null;
  return `pass^${s.k} ${fmtPct(from)} → ${fmtPct(to)}`;
}

/** `cost / row unchanged $0.011` or `cost / row $0.011 → $0.014`. */
export function costChip(s) {
  const { from, to } = s.cost;
  if (!isNum(from) || !isNum(to)) return null;
  const a = fmtUsd(from);
  const b = fmtUsd(to);
  return a === b ? `cost / row unchanged ${b}` : `cost / row ${a} → ${b}`;
}

/**
 * The popover's plain sentence: `6 rows got better and none got worse. If
 * the change did nothing, a split this lopsided happens 3% of the time
 * (sign test).`
 */
export function signSentence(s) {
  const b = s.better.length;
  const w = s.worse.length;
  if (!b && !w)
    return "No row changed its pass rate, so there is nothing to test.";
  const better = `${count(b, "row", "rows")} got better`;
  const worse = w ? `${w} got worse` : "none got worse";
  return `${better} and ${worse}. If the change did nothing, a split this lopsided happens ${pPercent(s.sign.p)} of the time (sign test).`;
}

/** `+20.0 pts on 6 rows` (the ribbon hover). */
export function hopDelta(s) {
  const from = s.pass1.from.rate;
  const to = s.pass1.to.rate;
  if (!isNum(from) || !isNum(to)) return null;
  const pts = ptsBetween(from, to);
  const n = s.rerun.length || s.rows;
  return `${pts >= 0 ? "+" : "−"}${Math.abs(pts).toFixed(1)} pts on ${count(n, "row", "rows")}`;
}
