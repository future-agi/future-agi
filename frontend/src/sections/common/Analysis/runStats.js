// Run statistics for the Matrix, Workbench and By dataset (R2-SPEC §8).
//
// Pure. The mock (R3 / R5 / I1 handlers) and the browser share them, so a
// count on screen always equals the rows it links to. `passHatK` and
// `estimateRun` are re-exported from fixtures/world/results-math.js.
//
// Frozen names (R2 manifest row 45). Trial outcomes are r2Shapes.js
// TrialOutcome values: 1 pass · 0 fail · "infra" (didn't run; excluded from
// every denominator) · "error" (the agent errored; counts as a fail).
// `null` / `undefined` = not run yet (excluded like infra).

import { costPerTry } from "src/sections/common/RunSources/declaredUses";

// ------------------------------------------------------------------ trials

const isPass = (t) => t === 1 || t === true;
/** Scored = ran and was judged: pass, fail or error. */
const isScored = (t) =>
  t === 1 || t === 0 || t === true || t === false || t === "error";

/** A cell may be a trials array or an R3 matrix cell `{trials}`. */
const trialsOf = (cell) =>
  Array.isArray(cell) ? cell : Array.isArray(cell?.trials) ? cell.trials : [];

const countOf = (trials) => {
  let pass = 0;
  let n = 0;
  let infra = 0;
  trials.forEach((t) => {
    if (isScored(t)) {
      n += 1;
      if (isPass(t)) pass += 1;
    } else if (t === "infra") infra += 1;
  });
  return { pass, n, infra, total: trials.length };
};

const rateOf = (pass, n) => (n > 0 ? pass / n : null);
const round9 = (x) => Math.round(x * 1e9) / 1e9;

/** Matrix rows normalised to `{id, n, cells: {runId: trials[]}, row}`. */
function matrixRows(matrix) {
  const rows = Array.isArray(matrix) ? matrix : matrix?.rows ?? [];
  return rows.map((row, i) => {
    const cells = {};
    Object.entries(row?.cells ?? {}).forEach(([runId, cell]) => {
      cells[runId] = trialsOf(cell);
    });
    return {
      id: row?.id ?? row?.row_id ?? String(i),
      n: Number.isFinite(row?.n) ? row.n : i + 1,
      cells,
      row,
    };
  });
}

/** Run ids present in a matrix (column order when `columns` is given). */
export function matrixRunIds(matrix) {
  const fromColumns = Object.keys(matrix?.columns ?? {});
  if (fromColumns.length) return fromColumns;
  const ids = [];
  matrixRows(matrix).forEach((r) =>
    Object.keys(r.cells).forEach((id) => {
      if (!ids.includes(id)) ids.push(id);
    }),
  );
  return ids;
}

/** Per-row trials arrays of one run in a matrix (rows the run lacks skipped). */
export function runCells(matrix, runId) {
  return matrixRows(matrix)
    .filter((r) => r.cells[runId])
    .map((r) => r.cells[runId]);
}

// ---------------------------------------------------------------- pass@k

/**
 * pass@1 of a run: passed attempts / scored attempts over every row and
 * trial (`infra` excluded from both).
 * @param {Array<Array<1|0|"infra"|"error">>} cells  per row: its trials
 * @returns {{pass: number, n: number, rate: number|null}}
 */
export function passAt1(cells) {
  let pass = 0;
  let n = 0;
  (cells ?? []).forEach((cell) => {
    const c = countOf(trialsOf(cell));
    pass += c.pass;
    n += c.n;
  });
  return { pass, n, rate: rateOf(pass, n) };
}

/**
 * pass^k: the share of rows whose first k scored trials ALL pass (rows with
 * fewer than k scored trials are left out).
 * Rows with an `infra` trial leave the denominator (R2-SPEC §8.4).
 * @param {Array<Array<1|0|"infra"|"error">>} cells
 * @param {number} k
 * @returns {{pass: number, n: number, rate: number|null}}
 */
export function passHatK(cells, k) {
  const need = Math.max(1, Number(k) || 1);
  let pass = 0;
  let n = 0;
  (cells ?? []).forEach((cell) => {
    const trials = trialsOf(cell);
    if (trials.some((t) => t === "infra")) return;
    const scored = trials.filter(isScored);
    if (scored.length < need) return;
    n += 1;
    if (scored.slice(0, need).every(isPass)) pass += 1;
  });
  return { pass, n, rate: rateOf(pass, n) };
}

/**
 * One row's pass rate over its scored trials.
 * @param {Array<1|0|"infra"|"error">} trials
 * @returns {number|null}  null when no trial was scored
 */
export function rowPassRate(trials) {
  const c = countOf(trialsOf(trials));
  return rateOf(c.pass, c.n);
}

// ----------------------------------------------------------- disagreement

const allPass = (trials) => trials.length > 0 && trials.every((t) => isPass(t));
const noPass = (trials) => {
  const c = countOf(trials);
  return c.n > 0 && c.pass === 0;
};
const mixed = (trials) => {
  const c = countOf(trials);
  return c.pass > 0 && c.pass < c.n;
};

/**
 * Row sets of the Matrix `Show` menu over the runs in view.
 * @param {{rows: Array<{id: string, cells: Object<string,
 *   Array<1|0|"infra"|"error">>}>}} matrix
 * @returns {{all: string[], disagree: string[], flaky: string[],
 *   nobody: string[], everyone: string[], sd: Object<string, number>}}
 *   disagree = some run passes every trial and some run passes none;
 *   flaky = some run's scored trials are mixed; nobody = no run passes every
 *   scored trial; everyone = every run passes every trial (an infra trial
 *   is not a pass); sd = population SD of the per-run pass rates of the row,
 *   rounded to 9 decimals. The sets overlap. Additive: `order` = row ids by
 *   sd descending, ties by row number.
 */
export function disagreement(matrix) {
  const out = {
    all: [],
    disagree: [],
    flaky: [],
    nobody: [],
    everyone: [],
    sd: {},
    order: [],
  };
  const ranked = [];
  matrixRows(matrix).forEach((r) => {
    const runs = Object.values(r.cells).filter((t) => countOf(t).n > 0);
    out.all.push(r.id);
    if (!runs.length) {
      out.sd[r.id] = 0;
      ranked.push({ id: r.id, n: r.n, sd: 0 });
      return;
    }
    if (runs.some(allPass) && runs.some(noPass)) out.disagree.push(r.id);
    if (runs.some(mixed)) out.flaky.push(r.id);
    if (
      runs.every((t) => {
        const c = countOf(t);
        return c.pass < c.n;
      })
    )
      out.nobody.push(r.id);
    if (runs.every(allPass)) out.everyone.push(r.id);
    const rates = runs.map((t) => rowPassRate(t));
    const mean = rates.reduce((a, b) => a + b, 0) / rates.length;
    const sd = round9(
      Math.sqrt(rates.reduce((a, b) => a + (b - mean) ** 2, 0) / rates.length),
    );
    out.sd[r.id] = sd;
    ranked.push({ id: r.id, n: r.n, sd });
  });
  out.order = ranked.sort((a, b) => b.sd - a.sd || a.n - b.n).map((x) => x.id);
  return out;
}

// ----------------------------------------------------------------- pareto

/**
 * Cost / quality frontier: a point is dominated when another is no more
 * expensive and no worse, and strictly better on one.
 * `dominated_by` = the dominating point with the best quality (then the
 * cheapest, then input order).
 * @param {Array<{id: string, cost: number, quality: number}>} points
 * @returns {Array<{id: string, frontier: boolean, dominated_by: string|null}>}
 */
export function pareto(points) {
  const list = (points ?? []).filter(
    (p) => Number.isFinite(p?.cost) && Number.isFinite(p?.quality),
  );
  return (points ?? []).map((p) => {
    if (!Number.isFinite(p?.cost) || !Number.isFinite(p?.quality))
      return { id: p?.id ?? null, frontier: false, dominated_by: null };
    const dominators = list.filter(
      (q) =>
        q !== p &&
        q.cost <= p.cost &&
        q.quality >= p.quality &&
        (q.cost < p.cost || q.quality > p.quality),
    );
    if (!dominators.length)
      return { id: p.id, frontier: true, dominated_by: null };
    const best = dominators.reduce((a, b) =>
      b.quality > a.quality || (b.quality === a.quality && b.cost < a.cost)
        ? b
        : a,
    );
    return { id: p.id, frontier: false, dominated_by: best.id };
  });
}

// ------------------------------------------------------------- sign test

/**
 * Two-sided sign test on paired rows (ties dropped), α 0.05. `p` is exact
 * (binomial, p = 0.5); callers round for display.
 * @param {number} better  rows where the candidate wins
 * @param {number} worse  rows where it loses
 * @returns {{p: number, significant: boolean}}
 */
export function signTest(better, worse) {
  const b = Math.max(0, Math.round(Number(better) || 0));
  const w = Math.max(0, Math.round(Number(worse) || 0));
  const n = b + w;
  if (n === 0) return { p: 1, significant: false };
  const k = Math.min(b, w);
  let tail = 0;
  let c = 1; // C(n, i)
  for (let i = 0; i <= k; i += 1) {
    tail += c;
    c = (c * (n - i)) / (i + 1);
  }
  const p = Math.min(1, (2 * tail) / 2 ** n);
  return { p, significant: p < 0.05 };
}

// ------------------------------------------------------------------ pivot

const NUMERIC_MEASURES = {
  cost: "cost_usd",
  cost_usd: "cost_usd",
  latency: "wall_ms",
  wall_ms: "wall_ms",
};
const ROW_MEASURES = { steps: "steps", tool_calls: "tool_calls" };

/** Group value of a matrix row for a rows-axis key (`category`, `meta:tier`, …). */
export function groupValueOf(row, key) {
  if (!key || key === "none") return "All rows";
  if (key === "row") return row?.title ?? row?.row_id ?? row?.id ?? "—";
  const gv = row?.group_values ?? {};
  if (key.startsWith("meta:")) {
    const k = key.slice(5);
    const v = gv[k] ?? row?.values?.[k];
    return v == null || v === "" ? "—" : String(v);
  }
  if (key.startsWith("path:")) {
    const [, col] = key.split(":");
    const v = row?.values?.[col];
    return v == null || v === "" ? "—" : String(v);
  }
  if (key === "source") {
    const v = gv.source ?? row?.source?.kind ?? row?.source;
    return v == null || v === "" ? "—" : String(v);
  }
  const v = gv[key];
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  return v == null || v === "" ? "—" : String(v);
}

const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const p90 = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.ceil(0.9 * s.length) - 1)];
};
function aggregateValues(xs, agg) {
  if (!xs.length) return null;
  if (agg === "sum") return xs.reduce((a, b) => a + b, 0);
  if (agg === "count") return xs.length;
  if (agg === "median") return median(xs);
  if (agg === "p90") return p90(xs);
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function newBucket() {
  return { pass: 0, n: 0, rowsAll: new Set(), rowsPass: new Set(), xs: [] };
}

/**
 * Pivot the matrix by two axes (Workbench).
 * @param {object} matrix  R3 shape=matrix result
 * @param {{rows: string, cols: string, measure: string,
 *   agg: "mean"|"sum"|"count"|"rate"}} opts  axes as readR2Params values.
 *   Additive: `runInfo{<runId>: {<colsAxis>: value}}` maps runs to a config
 *   column (`model`, `harness`, …; default = the run id), `k` for pass^k,
 *   `runIds` limits the runs in view.
 * @returns {{rowKeys: string[], colKeys: string[],
 *   cells: Object<string, Object<string, {value: number|null, n: number}>>,
 *   totals: {rows: Object<string, number|null>, cols: Object<string,
 *   number|null>, all: number|null}}}
 *   Cells also carry `pass` (rate measures) and `rows` (row count).
 */
export function pivot(matrix, opts = {}) {
  const rowsKey = opts.rows || "none";
  const colsKey = opts.cols || "run";
  const measure = opts.measure || "pass_at_1";
  const agg = opts.agg || "mean";
  const k = opts.k ?? 3;
  const runIds = opts.runIds?.length ? opts.runIds : matrixRunIds(matrix);
  const colOf = (runId) =>
    colsKey === "run"
      ? runId
      : String(opts.runInfo?.[runId]?.[colsKey] ?? runId);
  const isRate = measure === "pass_at_1" || measure === "pass_hat_k";

  const grid = new Map(); // rowKey -> colKey -> bucket
  const rowTot = new Map();
  const colTot = new Map();
  const all = newBucket();
  const rowKeys = [];
  const colKeys = [];
  runIds.forEach((id) => {
    const c = colOf(id);
    if (!colKeys.includes(c)) colKeys.push(c);
  });
  const bucket = (map, key) => {
    if (!map.has(key)) map.set(key, newBucket());
    return map.get(key);
  };
  const add = (b, { pass, n, rowId, rowPass, xs }) => {
    b.pass += pass;
    b.n += n;
    b.rowsAll.add(rowId);
    if (rowPass) b.rowsPass.add(rowId);
    b.xs.push(...xs);
  };

  (matrix?.rows ?? []).forEach((row) => {
    runIds.forEach((runId) => {
      const cell = row?.cells?.[runId];
      if (!cell) return;
      const trials = trialsOf(cell);
      const col = colOf(runId);
      // failed_eval groups attempts; every other key groups the row.
      const parts =
        rowsKey === "failed_eval"
          ? trials.map((t, i) => ({
              key: isScored(t)
                ? isPass(t)
                  ? "Passed"
                  : cell.failed_evals?.[i]?.[0] ??
                    (t === "error" ? "Error" : "Failed")
                : null,
              trials: [t],
              idx: [i],
            }))
          : [
              {
                key: groupValueOf(row, rowsKey),
                trials,
                idx: trials.map((_, i) => i),
              },
            ];
      parts.forEach((part) => {
        if (part.key == null) return;
        const c = countOf(part.trials);
        const rowId = `${row.row_id ?? row.id}|${runId}`;
        let xs = [];
        if (NUMERIC_MEASURES[measure]) {
          const field = cell[NUMERIC_MEASURES[measure]] ?? [];
          xs = part.idx
            .filter((i) => isScored(trials[i]))
            .map((i) => field[i])
            .filter((x) => Number.isFinite(x));
          if (measure === "latency" || measure === "wall_ms")
            xs = xs.map((x) => x / 1000);
        } else if (ROW_MEASURES[measure]) {
          const v = cell[ROW_MEASURES[measure]];
          if (Number.isFinite(v)) xs = [v];
        }
        const rowPass =
          measure === "pass_hat_k"
            ? !part.trials.some((t) => t === "infra") &&
              c.n >= k &&
              part.trials.filter(isScored).slice(0, k).every(isPass)
            : false;
        const eligible =
          measure !== "pass_hat_k" ||
          (!part.trials.some((t) => t === "infra") && c.n >= k);
        if (measure === "pass_hat_k" && !eligible) return;
        const entry = { pass: c.pass, n: c.n, rowId, rowPass, xs };
        if (!rowKeys.includes(part.key)) rowKeys.push(part.key);
        if (!grid.has(part.key)) grid.set(part.key, new Map());
        add(bucket(grid.get(part.key), col), entry);
        add(bucket(rowTot, part.key), entry);
        add(bucket(colTot, col), entry);
        add(all, entry);
      });
    });
  });

  const valueOf = (b) => {
    if (!b) return null;
    if (measure === "pass_at_1") return rateOf(b.pass, b.n);
    if (measure === "pass_hat_k")
      return rateOf(b.rowsPass.size, b.rowsAll.size);
    if (agg === "count") return b.n;
    return aggregateValues(b.xs, agg === "rate" ? "mean" : agg);
  };
  const cellOf = (b) => ({
    value: valueOf(b),
    n: measure === "pass_hat_k" ? b?.rowsAll.size ?? 0 : b?.n ?? 0,
    ...(isRate
      ? {
          pass: measure === "pass_hat_k" ? b?.rowsPass.size ?? 0 : b?.pass ?? 0,
        }
      : {}),
    rows: new Set([...(b?.rowsAll ?? [])].map((x) => x.split("|")[0])).size,
  });

  const cells = {};
  rowKeys.forEach((rk) => {
    cells[rk] = {};
    colKeys.forEach((ck) => {
      cells[rk][ck] = cellOf(grid.get(rk)?.get(ck));
    });
  });
  return {
    rowKeys,
    colKeys,
    cells,
    totals: {
      rows: Object.fromEntries(
        rowKeys.map((rk) => [rk, valueOf(rowTot.get(rk))]),
      ),
      cols: Object.fromEntries(
        colKeys.map((ck) => [ck, valueOf(colTot.get(ck))]),
      ),
      all: all.n || all.xs.length || all.rowsAll.size ? valueOf(all) : null,
    },
  };
}

// ------------------------------------------------------------- gap slices

const DEFAULT_GAP_KEYS = ["category", "difficulty", "split", "source"];

/**
 * The slices where a candidate differs most from the baseline (top 3),
 * ranked by the absolute pass@1 gap, ties by slice size. A slice needs at
 * least 3 rows and at most half of the rows both runs share.
 * @param {object} matrix
 * @param {string} baselineId
 * @param {string} candId
 * @param {{keys: string[]}} opts  group keys to slice by (`category`, `meta:tier`, …);
 *   default category, difficulty, split, source + every other group_values key
 * @returns {Array<{key: string, value: string, delta_pts: number,
 *   from: {pass: number, n: number}, to: {pass: number, n: number}}>}
 *   Additive: `rows` (slice size) and `row_ids`.
 */
export function gapSlices(matrix, baselineId, candId, opts = {}) {
  const rows = (matrix?.rows ?? []).filter(
    (r) => r?.cells?.[baselineId] && r?.cells?.[candId],
  );
  if (!rows.length) return [];
  let keys = opts.keys;
  if (!keys?.length) {
    const extra = new Set();
    rows.forEach((r) =>
      Object.keys(r.group_values ?? {}).forEach((k) => {
        if (!DEFAULT_GAP_KEYS.includes(k)) extra.add(`meta:${k}`);
      }),
    );
    keys = [...DEFAULT_GAP_KEYS, ...extra];
  }
  const maxRows = rows.length / 2;
  const out = [];
  keys.forEach((key) => {
    const groups = new Map();
    rows.forEach((r) => {
      const v = groupValueOf(r, key);
      if (v === "—") return;
      if (!groups.has(v)) groups.set(v, []);
      groups.get(v).push(r);
    });
    groups.forEach((list, value) => {
      if (list.length < 3 || list.length > maxRows) return;
      const from = passAt1(list.map((r) => r.cells[baselineId]));
      const to = passAt1(list.map((r) => r.cells[candId]));
      if (from.rate == null || to.rate == null) return;
      const delta = Math.round((to.rate - from.rate) * 1000) / 10;
      out.push({
        key: key.startsWith("meta:") ? key.slice(5) : key,
        value,
        delta_pts: delta,
        from: { pass: from.pass, n: from.n },
        to: { pass: to.pass, n: to.n },
        rows: list.length,
        row_ids: list.map((r) => r.row_id ?? r.id),
      });
    });
  });
  return out
    .sort(
      (a, b) =>
        Math.abs(b.delta_pts) - Math.abs(a.delta_pts) || b.rows - a.rows,
    )
    .slice(0, opts.limit ?? 3);
}

// --------------------------------------------------------------- estimate

// options-truth L3 G3: a target with no earlier run is priced at its
// model's list price (declaredUses.MODEL_PRICES × TOKENS_PER_TRY, the
// engine's tokens per try); MEDIAN_TOKENS stays for a model with no price.
export const MEDIAN_TOKENS = 1200;
export const DEFAULT_P50_MS = 6000;
export const JUDGE_COST_PER_CALL = 0.0006;

/**
 * Prototype playback rate (attempts per second) of one run, also used for
 * the time estimate so the estimate is what the walk sees (R2-SPEC §4.3).
 */
export const playbackRate = (attempts) =>
  Math.min(6, Math.max(2, attempts / 8));

const ceilTo5 = (s) => Math.max(5, Math.ceil(s / 5) * 5);

/**
 * Cost and time before a run starts (the New experiment cost line).
 * Targets: `{key, kind?, model?, cost_per_attempt?, p50_ms?}`; a `code`
 * target bills nothing in Future AGI (your script's model calls bill to your
 * keys). History: completed runs `{target_key, cost_per_attempt, p50_ms,
 * completed_at}`, the last one per target wins. Evals: `{type:
 * "judge"|"check"|…}`; judge calls = calls × LLM-judge evals (Checks are
 * free). Seconds = the prototype playback of one target's attempts, rounded
 * up to 5 s (targets run side by side). Additive: `attempts` (= calls),
 * `per_target[]`, `total_usd` (= cost + judge cost).
 * @param {{rows: number, trials: number, targets: object[], evals: object[],
 *   history?: object[]}} opts
 * @returns {{calls: number, judge_calls: number, cost_usd: number,
 *   judge_cost_usd: number, seconds: number, rough: boolean}}
 */
export function estimateRun(opts = {}) {
  const rows = Math.max(0, Math.round(Number(opts.rows) || 0));
  const trials = Math.max(1, Math.round(Number(opts.trials) || 1));
  const targets = opts.targets?.length ? opts.targets : [{}];
  const evals = opts.evals ?? [];
  const history = opts.history ?? [];
  const perTargetAttempts = rows * trials;
  const judgeEvals = evals.filter(
    (e) => e?.type === "judge" || e?.type === "llm" || e?.kind === "judge",
  );
  const judges = judgeEvals.length;
  // options-truth L3 G3: each judge at its own judge model's price per call.
  const judgeUsdPerAttempt = judgeEvals.reduce(
    (s, e) =>
      s +
      (Number.isFinite(e?.cost_per_call)
        ? e.cost_per_call
        : JUDGE_COST_PER_CALL),
    0,
  );
  let rough = false;
  const perTarget = targets.map((t) => {
    // options-truth L3 G3 (S5): a run with its own tries plays those.
    const attempts = rows * Math.max(1, Math.round(Number(t?.tries) || trials));
    const key = t?.key ?? t?.id ?? null;
    const past = history
      .filter(
        (h) =>
          h &&
          (h.target_key ?? h.key) === key &&
          Number.isFinite(h.cost_per_attempt),
      )
      .sort((a, b) =>
        String(b.completed_at ?? "").localeCompare(
          String(a.completed_at ?? ""),
        ),
      )[0];
    let perAttempt;
    if (t?.kind === "code") perAttempt = 0;
    else if (Number.isFinite(past?.cost_per_attempt))
      perAttempt = past.cost_per_attempt;
    else if (Number.isFinite(t?.cost_per_attempt))
      perAttempt = t.cost_per_attempt;
    else {
      rough = true;
      perAttempt = costPerTry(t?.model) ?? (0.002 * MEDIAN_TOKENS) / 1000;
    }
    const p50 = past?.p50_ms ?? t?.p50_ms ?? DEFAULT_P50_MS;
    return {
      key,
      attempts,
      cost_usd: perAttempt * attempts,
      p50_ms: p50,
      from_history: Number.isFinite(past?.cost_per_attempt),
    };
  });
  const calls = perTarget.reduce((s, t) => s + t.attempts, 0);
  const judgeCalls = calls * judges;
  const cost = perTarget.reduce((s, t) => s + t.cost_usd, 0);
  const judgeCost = calls * judgeUsdPerAttempt;
  const seconds = perTargetAttempts
    ? ceilTo5(perTargetAttempts / playbackRate(perTargetAttempts))
    : 0;
  const r6 = (x) => Math.round(x * 1e6) / 1e6;
  return {
    calls,
    attempts: calls,
    judge_calls: judgeCalls,
    cost_usd: r6(cost),
    judge_cost_usd: r6(judgeCost),
    // L5 G3: the judge models the judge calls are priced at.
    judge_models: [
      ...new Set(judgeEvals.map((e) => e?.judge_model).filter(Boolean)),
    ],
    total_usd: r6(cost + judgeCost),
    seconds,
    rough,
    per_target: perTarget.map((t) => ({ ...t, cost_usd: r6(t.cost_usd) })),
  };
}

// ----------------------------------------------------------------- wilson

/**
 * Wilson score interval, 95%.
 * @param {number} p  observed rate (0..1)
 * @param {number} n  attempts
 * @returns {{low: number, high: number}}
 */
export function wilson(p, n) {
  const size = Number(n) || 0;
  if (size <= 0 || !Number.isFinite(p)) return { low: 0, high: 1 };
  const z = 1.96;
  const z2 = z * z;
  const denom = 1 + z2 / size;
  const center = (p + z2 / (2 * size)) / denom;
  const half =
    (z / denom) * Math.sqrt((p * (1 - p)) / size + z2 / (4 * size * size));
  return {
    low: Math.max(0, center - half),
    high: Math.min(1, center + half),
  };
}
