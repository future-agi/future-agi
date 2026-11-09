// The single source of every run number and row label on screen (R4-SPEC
// D22; lane F; frozen API). Built on G0's runStats.js (pass@1, the Matrix
// per-task rule) and fmt.js; never a second pass-rate formula. Every pass
// rate, delta, better / worse count, cost per solved task and "Best" on R4
// screens (leaderboard, run view headline, Matrix, Summary, Compare chain,
// the `Test a fix` banner) calls these. Pure.
//
// Inputs are R3 matrix shapes: `{rows: [{id|row_id, n, cells: {runId:
// {trials, cost_usd?, trace_summary?}}}], columns?: {runId: {label}}}`;
// a cell may also be a bare trials array.
import {
  pareto,
  passAt1,
  rowPassRate,
  wilson,
} from "src/sections/common/Analysis/runStats";
import {
  EMPTY,
  fmtCost,
  fmtDeltaMs,
  fmtPct,
  fmtRate,
  ptsBetween,
} from "src/sections/common/format/fmt";
import { plural } from "src/sections/common/vocab";
import { rowLabel } from "src/sections/common/Datasets/datasetKind";

const EPS = 1e-9;
const isPass = (t) => t === 1 || t === true;
const isScored = (t) =>
  t === 1 || t === 0 || t === true || t === false || t === "error";
const isPending = (t) => t === null || t === undefined || t === "running";

const trialsOf = (cell) =>
  Array.isArray(cell) ? cell : Array.isArray(cell?.trials) ? cell.trials : [];

const rowsOf = (matrix) =>
  (Array.isArray(matrix) ? matrix : matrix?.rows ?? []).map((row, i) => ({
    id: row?.id ?? row?.row_id ?? String(i),
    n: Number.isFinite(row?.n) ? row.n : i + 1,
    cells: row?.cells ?? {},
    row,
  }));

/** True when a cell's results were copied from the parent run. */
export function isCarriedCell(cell) {
  if (!cell || Array.isArray(cell)) return false;
  if (cell.carried === true || cell.carried_from) return true;
  const s = cell.trace_summary;
  return (
    Array.isArray(s) && s.length > 0 && s.every((x) => x?.status === "carried")
  );
}

const carriedFromOf = (cell) =>
  cell?.carried_from ??
  (Array.isArray(cell?.trace_summary)
    ? cell.trace_summary.find((x) => x?.carried_from)?.carried_from
    : null) ??
  null;

/** Raw cells of one run: an array of cells, `{cells}`, or `{matrix, runId}`. */
function cellsOf(input) {
  if (Array.isArray(input)) return input;
  if (Array.isArray(input?.cells)) return input.cells;
  if (input?.matrix && input?.runId)
    return rowsOf(input.matrix)
      .map((r) => r.cells[input.runId])
      .filter(Boolean);
  return [];
}

/**
 * A run's success rate with R3's denominators: passed tries / scored tries;
 * `infra` and unfinished tries are excluded and named.
 * `91.0% (81/89)` + `1 try didn't finish · not counted`.
 * @param {object[]|{cells: object[]}|{matrix: object, runId: string}} input
 * @returns {{rate: number|null, k: number, n: number, excluded: number,
 *   text: string, note: string|null}}
 */
export function runRate(input) {
  const cells = cellsOf(input);
  const { pass, n, rate } = passAt1(cells.map(trialsOf));
  let excluded = 0;
  cells.forEach((c) =>
    trialsOf(c).forEach((t) => {
      if (!isScored(t)) excluded += 1;
    }),
  );
  return {
    rate,
    k: pass,
    n,
    excluded,
    text: fmtRate(pass, n),
    note: excluded
      ? `${plural(excluded, "try")} didn't finish · not counted`
      : null,
  };
}

const labelOf = (matrix, id, labels) =>
  labels?.[id] ??
  matrix?.columns?.[id]?.label ??
  (Array.isArray(matrix?.runs)
    ? matrix.runs.find((r) => r.id === id || r.run_id === id)?.label
    : null) ??
  null;

/**
 * Compare a candidate with a baseline over a scope (D22).
 * - `all` (default): every row both ran. Rows the candidate carried over
 *   from its parent keep the parent's tries, so they come out `same`
 *   against that parent; they are counted in `carried` and named.
 * - `rerun`: only rows the candidate ran itself (not carried).
 * - `rowIds[]`: those rows.
 * Per-row better / worse use the Matrix rule (pass rate over scored tries).
 * `deltaPts` = the success difference over the scope rows both scored, in
 * pts, rounded once from the unrounded rates (ptsDelta). `range` /
 * `significant` = diffRange95 over those rows (the one noise rule). `partial` = `{scored, total}`
 * over the candidate's tries in scope while any of them is unscored (else
 * null). `rows` = `[{id, n,
 * change: "better"|"worse"|"same"|"not_run", carried}]`.
 * @param {{matrix: object, baselineId: string, candidateId: string,
 *   scope?: "all"|"rerun"|string[], labels?: Object<string, string>,
 *   noun?: string, trialsPerRow?: number, candidateEnded?: boolean}} args
 */
export function compareRuns({
  matrix,
  baselineId,
  candidateId,
  scope = "all",
  labels,
  noun = "row",
  trialsPerRow,
  candidateEnded = false,
}) {
  const rowIds = Array.isArray(scope) ? new Set(scope) : null;
  const inScope = [];
  let carried = 0;
  let carriedFrom = null;
  rowsOf(matrix).forEach((r) => {
    const b = r.cells[baselineId];
    const c = r.cells[candidateId];
    if (!b && !c) return;
    if (rowIds && !rowIds.has(r.id)) return;
    const isCarried = isCarriedCell(c);
    if (scope === "rerun" && isCarried) return;
    if (isCarried) {
      carried += 1;
      carriedFrom = carriedFrom ?? carriedFromOf(c);
    }
    inScope.push({ ...r, b, c, isCarried });
  });

  let better = 0;
  let worse = 0;
  let same = 0;
  let notRun = 0;
  let scored = 0;
  let total = 0;
  const both = [];
  const betterIds = [];
  const worseIds = [];
  const rowList = [];
  inScope.forEach((r) => {
    // options-truth L3 G10: `partial` counts the candidate's tries only
    // (`36 of 153 tries scored`, the run line's numbers); the baseline's
    // finished tries never pad it to `153 of 306`.
    if (r.c) {
      const trials = trialsOf(r.c);
      const expected = Math.max(trialsPerRow ?? 0, trials.length);
      trials.forEach((t) => {
        if (t === "infra") return;
        total += 1;
        if (!isPending(t)) scored += 1;
      });
      total += expected - trials.length;
    }
    const rb = r.b ? rowPassRate(trialsOf(r.b)) : null;
    const rc = r.c ? rowPassRate(trialsOf(r.c)) : null;
    let change = "not_run";
    // IT-R2 G2 P3: which side has no result on a row neither compares.
    const missing =
      rb == null && rc == null
        ? "both"
        : rc == null
          ? "candidate"
          : rb == null
            ? "baseline"
            : null;
    if (rb == null || rc == null) notRun += 1;
    else {
      both.push(r);
      if (rc > rb + EPS) {
        better += 1;
        betterIds.push(r.id);
        change = "better";
      } else if (rc < rb - EPS) {
        worse += 1;
        worseIds.push(r.id);
        change = "worse";
      } else {
        same += 1;
        change = "same";
      }
    }
    rowList.push({
      id: r.id,
      n: r.n,
      change,
      carried: r.isCarried,
      ...(missing ? { missing } : {}),
    });
  });

  const cand = passAt1(both.map((r) => trialsOf(r.c)));
  const base = passAt1(both.map((r) => trialsOf(r.b)));
  const range = diffRange95(cand.rate, base.rate, both.length);
  const candLabel = labelOf(matrix, candidateId, labels) ?? "the candidate";
  const baseLabel = labelOf(matrix, baselineId, labels) ?? "the baseline";
  const n = inScope.length;
  // IT-R1 G2 P1: rows only one of the two ran are not compared; the scope
  // says so (`across 24 tasks both ran`), never `across 30` next to a
  // confidence of 24 tasks.
  const both_ = n - notRun;
  let scopeText;
  if (scope === "rerun")
    scopeText = `on the ${plural(n, noun)} ${candLabel} reran`;
  else if (rowIds) scopeText = `on ${plural(n, noun)} you picked`;
  // IT-R1 G2 P1: `across 30 tasks`, never `on all 30 tasks` (it read as
  // "better on every task" next to `worse 2`).
  else
    scopeText = `across ${plural(notRun ? both_ : n, noun)}${notRun ? " both ran" : ""}${
      carried
        ? ` · ${carried} carried over from ${carriedFrom ?? baseLabel}`
        : ""
    }`;

  return {
    scope: rowIds ? "rows" : scope,
    scopeText,
    rows: rowList,
    betterIds,
    worseIds,
    // L2 G2 R4: one delta, rounded once from the unrounded rates (84.44 −
    // 65.56 = +18.9 pts, never the +18.8 of two rounded percents); and one
    // significance rule: the 95% range of the difference on the rows both
    // ran (diffRange95) must not hold 0, else every screen says `Too close
    // to call`.
    deltaPts: ptsDelta(cand.rate, base.rate),
    range,
    significant: Boolean(range?.real),
    candidate: {
      id: candidateId,
      label: candLabel,
      rate: cand.rate,
      k: cand.pass,
      n: cand.n,
    },
    baseline: {
      id: baselineId,
      label: baseLabel,
      rate: base.rate,
      k: base.pass,
      n: base.n,
    },
    better,
    worse,
    same,
    carried: scope === "rerun" ? 0 : carried,
    notRun,
    // L7c merge (banner:verdict, g4 owed runNumbers note): a candidate run
    // that has ended (Completed, Stopped, Failed) has no try still to
    // score: its missing tries were skipped, N/A or never played, so the
    // headline gives the verdict over what ran instead of `Partial ·
    // verdict when done`.
    partial: !candidateEnded && scored < total ? { scored, total } : null,
  };
}

/**
 * Row numbers as short ranges: `T25–T30`, `T3, T7–T9` (rowLabelOf names).
 * @param {number[]} ns
 * @param {string} [datasetKind]
 */
export function rowRangesText(ns, datasetKind) {
  const sorted = [...new Set((ns ?? []).filter(Number.isFinite))].sort(
    (a, b) => a - b,
  );
  const parts = [];
  let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j += 1;
    const a = rowLabelOf(sorted[i], datasetKind);
    // Word joiners keep `T25–T30` on one line.
    parts.push(
      j > i + 1
        ? `${a}\u2060–\u2060${rowLabelOf(sorted[j], datasetKind)}`
        : j === i + 1
          ? `${a}, ${rowLabelOf(sorted[j], datasetKind)}`
          : a,
    );
    i = j + 1;
  }
  return parts.join(", ");
}

/**
 * IT-R2 G2 P3: why a compare covers fewer rows than the dataset holds —
 * `the coding harness Code did not run T25–T30` (one clause per side that skipped rows).
 * Null when both ran every row in scope.
 * @param {object} compare  compareRuns() result
 * @param {{candidate?: string, baseline?: string, datasetKind?: string}} names
 */
export function notRunText(
  compare,
  { candidate, baseline, datasetKind, noun = "row" } = {},
) {
  const rows = (compare?.rows ?? []).filter((r) => r.change === "not_run");
  if (!rows.length) return null;
  const total = compare?.rows?.length ?? rows.length;
  const side = (who) =>
    rows
      .filter((r) => r.missing === who || r.missing === "both")
      .map((r) => r.n);
  // A short list names the rows (`T25–T30`); a long one counts them.
  const which = (ns) => {
    const text = rowRangesText(ns, datasetKind);
    return text.split(", ").length <= MAX_RANGES
      ? text
      : `${ns.length} of the ${plural(total, noun)}`;
  };
  const clauses = [];
  const c = side("candidate");
  const b = side("baseline");
  if (c.length)
    clauses.push(
      `${candidate ?? compare.candidate?.label ?? "the candidate"} did not run ${which(c)}`,
    );
  if (b.length)
    clauses.push(
      `${baseline ?? compare.baseline?.label ?? "the baseline"} did not run ${which(b)}`,
    );
  return clauses.join("; ") || null;
}

/** Above this many row ranges, notRunText counts instead of listing. */
const MAX_RANGES = 3;

/**
 * IT-R2 G2 P2: what each extra solved task costs — the extra cost per task
 * divided by the extra share solved (`$0.77 per extra task solved` for
 * `$0.21` vs `$0.011` at `91.5%` vs `65.6%`). Null unless the candidate both
 * costs more and solves more.
 * @param {{cand: number, base: number}} costPerTask  USD per task
 * @param {{cand: number, base: number}} rate  0–1 success
 */
export function extraCostPerSolved(costPerTask, rate) {
  const dc = Number(costPerTask?.cand) - Number(costPerTask?.base);
  const dr = Number(rate?.cand) - Number(rate?.base);
  if (!Number.isFinite(dc) || !Number.isFinite(dr) || dc <= 0 || dr <= EPS)
    return null;
  return dc / dr;
}

/**
 * Cost per solved try: total cost of the run's tries / passed tries.
 * `$0.05 per solved task`; `— · nothing solved yet` when none passed.
 * @param {object[]|{cells}|{matrix, runId}} input  cells carry `cost_usd[]`
 * @param {{noun?: string}} [opts]
 */
export function costPerSolved(input, { noun = "task" } = {}) {
  const cells = cellsOf(input);
  let usd = 0;
  let solved = 0;
  let hasCost = false;
  cells.forEach((c) => {
    const trials = trialsOf(c);
    const costs = Array.isArray(c?.cost_usd) ? c.cost_usd : [];
    trials.forEach((t, i) => {
      if (isPass(t)) solved += 1;
      const v = Number(costs[i]);
      if (Number.isFinite(v)) {
        usd += v;
        hasCost = true;
      }
    });
  });
  if (!hasCost)
    return { usd: null, solved, text: `${EMPTY} · no cost recorded` };
  if (!solved)
    return { usd: null, solved, text: `${EMPTY} · nothing solved yet` };
  const per = usd / solved;
  return { usd: per, solved, text: `${fmtCost(per)} per solved ${noun}` };
}

/**
 * The best run over EVERY run passed in (callers pass all runs, never a
 * filtered list): `success` = highest success rate (ties: more scored
 * tries, then earlier); `value` = lowest cost per solved try among runs
 * that solved something.
 * @param {{id: string, label?: string, cells: object[]}[]} runs
 * @param {{by?: "success"|"value", noun?: string}} [opts]
 * @returns {{id, label, rate, usd, text}|null}
 */
export function bestOf(runs, { by = "success", noun = "task" } = {}) {
  const scored = (runs ?? []).map((r, i) => ({
    r,
    i,
    rate: runRate(r),
    cost: costPerSolved(r, { noun }),
  }));
  let pick = null;
  if (by === "value") {
    scored
      .filter((x) => x.cost.usd != null)
      .forEach((x) => {
        if (!pick || x.cost.usd < pick.cost.usd - EPS) pick = x;
      });
  } else {
    scored
      .filter((x) => x.rate.rate != null)
      .forEach((x) => {
        if (
          !pick ||
          x.rate.rate > pick.rate.rate + EPS ||
          (Math.abs(x.rate.rate - pick.rate.rate) <= EPS &&
            x.rate.n > pick.rate.n)
        )
          pick = x;
      });
  }
  if (!pick) return null;
  return {
    id: pick.r.id,
    label: pick.r.label ?? null,
    rate: pick.rate.rate,
    usd: pick.cost.usd,
    text: by === "value" ? pick.cost.text : pick.rate.text,
  };
}

/** Best value keeps to runs within this many pts of the most solved. */
export const BEST_VALUE_WITHIN_PTS = 10;

/** USD per try over every try with a recorded cost (null without one). */
function costPerTry(cells) {
  let usd = 0;
  let tries = 0;
  (cells ?? []).forEach((c) => {
    const costs = Array.isArray(c?.cost_usd) ? c.cost_usd : [];
    costs.forEach((v) => {
      const x = Number(v);
      if (Number.isFinite(x)) {
        usd += x;
        tries += 1;
      }
    });
  });
  return tries ? usd / tries : null;
}

/**
 * IT-R2 G2 P4: `Best value` with a quality floor. Among the runs on the
 * cost / success frontier whose success is within BEST_VALUE_WITHIN_PTS of
 * the most solved run, the lowest cost per solved task. The cheapest run
 * (lowest cost per task) is named apart when it is a different run, so a
 * 53% run is `Cheapest`, never `Best value`.
 * @param {{id: string, label?: string, cells: object[]}[]} runs  EVERY run
 *   (callers drop runs whose target reports no cost)
 * @param {{noun?: string, withinPts?: number}} [opts]
 * @returns {{id, label, rate, usd, text, cheapest: {id, label, rate,
 *   usdPerTask}|null}|null}  `usd` = per solved task
 */
export function bestValueOf(
  runs,
  { noun = "task", withinPts = BEST_VALUE_WITHIN_PTS } = {},
) {
  const pts = (runs ?? [])
    .map((r) => {
      const rate = runRate(r).rate;
      const perTry = costPerTry(r.cells);
      const solved = costPerSolved(r, { noun });
      return { r, rate, perTry, solved };
    })
    .filter(
      (p) =>
        p.rate != null &&
        p.perTry != null &&
        p.perTry > 0 &&
        p.solved.usd != null,
    );
  if (!pts.length) return null;
  const front = new Set(
    pareto(pts.map((p) => ({ id: p.r.id, cost: p.perTry, quality: p.rate })))
      .filter((f) => f.frontier)
      .map((f) => f.id),
  );
  const top = Math.max(...pts.map((p) => p.rate));
  let pick = null;
  pts
    .filter(
      (p) => front.has(p.r.id) && ptsBetween(top, p.rate) <= withinPts + EPS,
    )
    .forEach((p) => {
      if (!pick || p.solved.usd < pick.solved.usd - EPS) pick = p;
    });
  if (!pick) return null;
  const cheap = pts.reduce((a, b) => (b.perTry < a.perTry - EPS ? b : a));
  return {
    id: pick.r.id,
    label: pick.r.label ?? null,
    rate: pick.rate,
    usd: pick.solved.usd,
    text: pick.solved.text,
    cheapest:
      cheap.r.id === pick.r.id
        ? null
        : {
            id: cheap.r.id,
            label: cheap.r.label ?? null,
            rate: cheap.rate,
            usdPerTask: cheap.perTry,
          },
  };
}

/**
 * `#3A LangGraph · 88.9% at $0.0060 per solved task (cheapest: #2A, passes
 * only 53.3%)`. `nameOf(id)` adds the run's short name.
 * @param {ReturnType<typeof bestValueOf>} bv
 * @param {{nameOf?: (id: string) => string|null, noun?: string}} [o]
 */
export function bestValueText(bv, { nameOf, noun = "task" } = {}) {
  if (!bv) return null;
  const who = (id, label) => [label, nameOf?.(id)].filter(Boolean).join(" ");
  const head = `${who(bv.id, bv.label)} · ${fmtPct(bv.rate)} at ${fmtCost(bv.usd)} per solved ${noun}`;
  if (!bv.cheapest) return head;
  return `${head} (cheapest: ${who(bv.cheapest.id, bv.cheapest.label)}, passes only ${fmtPct(bv.cheapest.rate)})`;
}

/** Every run of a matrix as bestOf input: `[{id, label, cells}]`. */
export function runsOfMatrix(matrix, { labels, runIds } = {}) {
  const rows = rowsOf(matrix);
  const ids =
    runIds ??
    Object.keys(matrix?.columns ?? {}).concat(
      rows.flatMap((r) => Object.keys(r.cells)),
    );
  return [...new Set(ids)].map((id) => ({
    id,
    label: labelOf(matrix, id, labels),
    cells: rows.map((r) => r.cells[id]).filter(Boolean),
  }));
}

/**
 * A row's one name: `T4` in a task suite, else `Row 4` (Datasets/
 * datasetKind.js rowLabel). The id only ever appears inside a CopyId.
 * @param {{n?: number, row_no?: number, number?: number}|number} row
 * @param {string} datasetKind
 * @param {Object<number, string>} [names]  row_labels (voice calls)
 */
export function rowLabelOf(row, datasetKind, names = null) {
  const n =
    typeof row === "number" ? row : row?.n ?? row?.row_no ?? row?.number;
  // IT-R4 G2 P8: a named row (a voice call: `Mike · corrects order id`).
  return names?.[n] ?? rowLabel(datasetKind, n);
}

/**
 * IT-R4 G2 P5 / P8 (S1): the 95% range of a success difference, in pts,
 * on `n` tasks per side (Newcombe's hybrid score interval from the two
 * Wilson ranges). `real` = the range leaves 0 out; when it does not, the
 * sentence reads `Level: … which is not a real difference yet (95% range
 * −12 to +36 pts)`. Null without two rates and n.
 * @returns {{lo: number, hi: number, real: boolean, text: string}|null}
 */
/**
 * L2 G2 R4 (consistency #2): a success difference in pts, rounded ONCE from
 * the unrounded rates (0.8444 − 0.6556 → 18.9). Every delta of two rates
 * goes through here, so the list row, the verdict and the Summary card
 * print the same number. Null when either rate is missing.
 */
export function ptsDelta(candRate, baseRate) {
  const ok = (v) => typeof v === "number" && Number.isFinite(v);
  if (!ok(candRate) || !ok(baseRate)) return null;
  return Math.round((candRate - baseRate) * 1000) / 10;
}

/**
 * L2 G2 R4 (consistency #3): a time difference (candidate − baseline, ms)
 * from the unrounded p50s, in words: `69 s slower`, `1.2 s faster`,
 * `same time` (|d| < 500 ms or < 5% of the baseline). One helper for the
 * results header, the Summary sentence, its table and the Experiments tab.
 */
export function timeDeltaWords(candMs, baseMs) {
  const ok = (v) => typeof v === "number" && Number.isFinite(v);
  if (!ok(candMs) || !ok(baseMs)) return null;
  const d = candMs - baseMs;
  if (Math.abs(d) < 500 || (baseMs > 0 && Math.abs(d) / baseMs < 0.05))
    return "same time";
  return fmtDeltaMs(d, { style: "words" });
}

export function diffRange95(candRate, baseRate, n) {
  const ok = (v) => typeof v === "number" && Number.isFinite(v);
  if (!ok(candRate) || !ok(baseRate) || !(n > 0)) return null;
  const a = wilson(candRate, n);
  const b = wilson(baseRate, n);
  const d = candRate - baseRate;
  const lo =
    (d - Math.sqrt((candRate - a.low) ** 2 + (b.high - baseRate) ** 2)) * 100;
  const hi =
    (d + Math.sqrt((a.high - candRate) ** 2 + (baseRate - b.low) ** 2)) * 100;
  const pts = (v) => {
    const r = Math.round(v);
    return r > 0 ? `+${r}` : r < 0 ? `\u2212${Math.abs(r)}` : "0";
  };
  return {
    lo,
    hi,
    real: lo > 0 || hi < 0,
    text: `95% range ${pts(lo)} to ${pts(hi)} pts`,
  };
}
