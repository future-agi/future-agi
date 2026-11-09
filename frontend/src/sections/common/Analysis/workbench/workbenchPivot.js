// Workbench pivot (R2-SPEC §6.3.2): measure × rows axis × columns axis over
// R3 `shape=matrix` cells. Pure. Cells keep the row ids behind every
// number (`rowIds`, `failRowIds`) so a click opens exactly those rows.
import { runKindOf, kindLabel } from "src/sections/common/Runs/runKind";
import { wilson } from "src/sections/common/Analysis/runStats";
import { attemptsOf, groupsOfRow, LOW_SAMPLE_ATTEMPTS } from "./workbenchModel";
import { expressionGrouper } from "./matrixScope";
import { aggOf } from "./workbenchAggs";

// ----------------------------------------------------------- columns axis

const versionNum = (v) => {
  const m = String(v ?? "").match(/(\d+(?:\.\d+)?)/);
  return m ? Number(m[1]) : Number.POSITIVE_INFINITY;
};

/** One run's value on each config axis (R1 run with `config`). */
export function runAxisValues(run) {
  const c = run?.config ?? {};
  const kind = runKindOf(run);
  const model =
    c.model?.id ??
    c.harness?.model ??
    c.agent?.nodes?.[0]?.model ??
    run?.target?.model ??
    null;
  return {
    run: run?.run_id,
    harness: c.harness?.name ?? "No harness",
    model: model ?? "Not set",
    prompt_version: c.prompt
      ? `${c.prompt.name} ${c.prompt.version}`
      : "No prompt",
    agent_version: c.agent ? `${c.agent.name} ${c.agent.version}` : "No agent",
    run_kind: kindLabel(kind, { tools: c.tools }),
    dataset_version:
      c.dataset?.version ??
      (run?.dataset_version?.number != null
        ? `v${run.dataset_version.number}`
        : "Not set"),
    code_repo: c.code?.repo ?? "No repo",
  };
}

/**
 * The iteration chain of the runs in view (root → latest), or [] when no
 * run in view has iterations. Runs carry `iteration{root_run_id, n}`.
 */
export function iterationChain(runs) {
  const list = runs ?? [];
  const roots = new Set(
    list.map((r) => r.iteration?.root_run_id).filter(Boolean),
  );
  const root = list.find((r) => roots.has(r.run_id));
  if (!root) return [];
  return [
    root,
    ...list
      .filter((r) => r.iteration?.root_run_id === root.run_id)
      .sort((a, b) => (a.iteration?.n ?? 0) - (b.iteration?.n ?? 0)),
  ];
}

/**
 * Columns for an axis: `[{key, label, runIds}]` in display order.
 * `runs` = R1 runs in view order (baseline first), each with `label`.
 */
export function columnsFor(runs, axis) {
  const list = runs ?? [];
  if (axis === "run" || !axis)
    return list.map((r) => ({
      key: r.run_id,
      label: r.label,
      runIds: [r.run_id],
    }));
  if (axis === "iteration")
    return iterationChain(list).map((r, i) => ({
      key: r.run_id,
      label:
        i === 0
          ? `${r.label} (root)`
          : `${r.label} (iteration ${r.iteration?.n ?? i})`,
      runIds: [r.run_id],
    }));
  const cols = [];
  list.forEach((r) => {
    const v = runAxisValues(r)[axis] ?? "Not set";
    const col = cols.find((c) => c.key === v);
    if (col) col.runIds.push(r.run_id);
    else cols.push({ key: v, label: v, runIds: [r.run_id] });
  });
  if (axis === "dataset_version" || axis === "prompt_version")
    cols.sort((a, b) => versionNum(a.key) - versionNum(b.key));
  return cols;
}

// ------------------------------------------------------------------ pivot

const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

// W5 B1: avg · median · p90 · min · max · std, one definition each
// (workbenchAggs.js, pinned against results-math `aggregate`).
function aggregate(xs, agg, kind) {
  if (!xs.length) return null;
  if (kind === "count") return xs.reduce((a, b) => a + b, 0);
  return aggOf(xs, agg);
}

const bucket = () => ({
  pass: 0,
  n: 0,
  xs: [],
  rows: new Set(),
  failRows: new Set(),
  passRows: new Set(),
});

function addAttempts(b, rowId, attempts, measure, k) {
  const done = attempts.filter(Boolean);
  if (measure.key === "pass_hat_k") {
    if (attempts.length && attempts.some((a) => a === null)) return false;
    if (done.length < k) return false;
    b.rows.add(rowId);
    b.n += 1;
    if (done.slice(0, k).every((a) => a.pass === 1)) {
      b.pass += 1;
      b.passRows.add(rowId);
    } else b.failRows.add(rowId);
    return true;
  }
  if (!done.length) return false;
  b.rows.add(rowId);
  done.forEach((a) => {
    if (a.pass !== undefined) {
      b.n += 1;
      b.pass += a.pass;
      if (a.pass === 0) b.failRows.add(rowId);
    }
    if (a.x !== undefined) b.xs.push(a.x);
  });
  if (measure.kind === "numeric" || measure.kind === "count") b.n = b.xs.length;
  return true;
}

function finish(b, measure, agg) {
  const rate = measure.kind === "rate";
  const value = rate
    ? b.n
      ? b.pass / b.n
      : null
    : aggregate(b.xs, agg, measure.kind);
  let ci = null;
  if (rate && b.n) ci = wilson(value, b.n);
  else if (
    !rate &&
    b.xs.length > 1 &&
    agg === "avg" &&
    measure.kind !== "count"
  ) {
    const m = mean(b.xs);
    const sd = Math.sqrt(
      b.xs.reduce((s, x) => s + (x - m) ** 2, 0) / (b.xs.length - 1),
    );
    const half = (1.96 * sd) / Math.sqrt(b.xs.length);
    ci = { low: m - half, high: m + half };
  }
  return {
    value,
    n: b.n,
    pass: rate ? b.pass : undefined,
    rows: b.rows.size,
    rowIds: [...b.rows],
    failRowIds: [...b.failRows],
    lowSample: b.n > 0 && b.n < LOW_SAMPLE_ATTEMPTS,
    ci,
  };
}

/**
 * @param {object} matrix  R3 shape=matrix
 * @param {{rows: string, columns: Array<{key, label, runIds}>,
 *   measure: object, agg?: string, k?: number}} opts
 * @returns {{rowKeys: string[], columns: object[], cells, rowTotals,
 *   colTotals, all, failedEval: boolean}}
 */
export function buildPivot(matrix, optsIn) {
  // W5 B1: `expr:<text>` groups each row by the expression's value.
  const opts = optsIn.rows?.startsWith("expr:")
    ? {
        ...optsIn,
        ctx: {
          ...(optsIn.ctx ?? {}),
          exprGroups: expressionGrouper(matrix, optsIn.rows.slice(5)),
        },
      }
    : optsIn;
  const { rows: rowsKey = "none", columns = [], measure, agg = "avg" } = opts;
  const k = opts.k ?? 3;
  const failedEval = rowsKey === "failed_eval";
  const m = failedEval ? { key: "pass_at_1", kind: "rate" } : measure;
  const grid = new Map();
  const rowTot = new Map();
  const colTot = new Map();
  const all = bucket();
  const order = [];
  const counts = new Map(); // failed_eval: group -> col -> {n, rows}
  const get = (map, key) => {
    if (!map.has(key)) map.set(key, bucket());
    return map.get(key);
  };
  const isCheck = opts.isCheck ?? (() => true);

  (matrix?.rows ?? []).forEach((row) => {
    const rowId = row.row_id;
    columns.forEach((col) => {
      col.runIds.forEach((runId) => {
        const cell = row.cells?.[runId];
        if (!cell) return;
        if (failedEval) {
          (cell.trials ?? []).forEach((t, i) => {
            if (!(t === 0 || t === "error")) return;
            // An errored attempt is its own failure mode (the agent crashed
            // before any eval could judge it), never a Check.
            const evals = t === "error" ? [] : cell.failed_evals?.[i] ?? [];
            const check = evals.find((e) => isCheck(e));
            // L7c g6 (ex:group-by): the group reads the eval's name, never
            // its raw key (`eval:correctness`).
            const key = check
              ? evalNameOf(check, matrix)
              : evals[0]
                ? `${evalNameOf(evals[0], matrix)} (Judge)`
                : t === "error"
                  ? "Error"
                  : "Failed";
            if (!order.includes(key)) order.push(key);
            if (!counts.has(key)) counts.set(key, new Map());
            const byCol = counts.get(key);
            if (!byCol.has(col.key))
              byCol.set(col.key, { n: 0, rows: new Set() });
            const c = byCol.get(col.key);
            c.n += 1;
            c.rows.add(rowId);
          });
          return;
        }
        const attempts = attemptsOf(cell, m);
        // L7c g6 (ex:group-by): a row in several groups (splits, tags)
        // counts once in its column total and the grand total.
        let totalled = false;
        groupsOfRow(row, rowsKey, opts.ctx, runId).forEach((g) => {
          if (!grid.has(g)) {
            grid.set(g, new Map());
            order.push(g);
          }
          const ok = addAttempts(
            get(grid.get(g), col.key),
            rowId,
            attempts,
            m,
            k,
          );
          if (!ok) return;
          addAttempts(get(rowTot, g), `${rowId}|${runId}`, attempts, m, k);
          if (totalled) return;
          totalled = true;
          addAttempts(get(colTot, col.key), rowId, attempts, m, k);
          addAttempts(all, `${rowId}|${runId}`, attempts, m, k);
        });
      });
    });
  });

  if (failedEval) return failedEvalPivot(order, counts, columns);

  const cells = {};
  order.forEach((g) => {
    cells[g] = {};
    columns.forEach((c) => {
      cells[g][c.key] = finish(grid.get(g)?.get(c.key) ?? bucket(), m, agg);
    });
  });
  const fixRows = (cell) => ({
    ...cell,
    rowIds: [...new Set(cell.rowIds.map((x) => x.split("|")[0]))],
    failRowIds: [...new Set(cell.failRowIds.map((x) => x.split("|")[0]))],
    rows: new Set(cell.rowIds.map((x) => x.split("|")[0])).size,
  });
  if (rowsKey === "row")
    order.forEach((g) =>
      columns.forEach((c) => {
        cells[g][c.key].lowSample = false;
      }),
    );
  const rowKeys = rowsKey === "row" ? order : sortKeys(order, rowsKey);
  return {
    rowKeys,
    columns,
    cells,
    rowTotals: Object.fromEntries(
      order.map((g) => [g, fixRows(finish(rowTot.get(g) ?? bucket(), m, agg))]),
    ),
    colTotals: Object.fromEntries(
      columns.map((c) => [
        c.key,
        finish(colTot.get(c.key) ?? bucket(), m, agg),
      ]),
    ),
    all: fixRows(finish(all, m, agg)),
    failedEval: false,
    measure: m,
  };
}

/**
 * An eval's display name: the matrix's `metric_names`, else the key made
 * readable (`check:order_found` → `Order found`).
 */
export function evalNameOf(key, matrix) {
  const named = matrix?.metric_names?.[key];
  if (named) return named;
  // A bare check name (`order_found`) is already the name the page shows.
  if (!/^(eval|check|metric|judge):/.test(String(key ?? ""))) return key;
  const bare = String(key ?? "")
    .replace(/^(eval|check|metric|judge):/, "")
    .replace(/[_-]+/g, " ")
    .trim();
  return bare ? bare.charAt(0).toUpperCase() + bare.slice(1) : String(key);
}

const sortKeys = (keys, rowsKey) =>
  rowsKey === "none"
    ? keys
    : [...keys].sort(
        (a, b) => (a.startsWith("(") ? 1 : 0) - (b.startsWith("(") ? 1 : 0),
      );

/** Rows: Failed eval → attempt counts per first failed eval. */
function failedEvalPivot(order, counts, columns) {
  const total = (key) =>
    [...(counts.get(key)?.values() ?? [])].reduce((s, c) => s + c.n, 0);
  const rowKeys = [...order].sort((a, b) => total(b) - total(a));
  const cell = (c) => ({
    value: c?.n ?? 0,
    n: c?.n ?? 0,
    rows: c?.rows.size ?? 0,
    rowIds: [...(c?.rows ?? [])],
    failRowIds: [...(c?.rows ?? [])],
    lowSample: false,
    ci: null,
  });
  const cells = {};
  const colAcc = new Map(
    columns.map((c) => [c.key, { n: 0, rows: new Set() }]),
  );
  const allAcc = { n: 0, rows: new Set() };
  const rowTotals = {};
  rowKeys.forEach((g) => {
    cells[g] = {};
    const acc = { n: 0, rows: new Set() };
    columns.forEach((c) => {
      const v = counts.get(g)?.get(c.key);
      cells[g][c.key] = cell(v);
      if (v) {
        acc.n += v.n;
        v.rows.forEach((r) => acc.rows.add(r));
        colAcc.get(c.key).n += v.n;
        v.rows.forEach((r) => colAcc.get(c.key).rows.add(r));
        allAcc.n += v.n;
        v.rows.forEach((r) => allAcc.rows.add(r));
      }
    });
    rowTotals[g] = cell(acc);
  });
  return {
    rowKeys,
    columns,
    cells,
    rowTotals,
    colTotals: Object.fromEntries(
      columns.map((c) => [c.key, cell(colAcc.get(c.key))]),
    ),
    all: cell(allAcc),
    failedEval: true,
    measure: { key: "failed_attempts", kind: "count", higherIsBetter: false },
  };
}

// ------------------------------------------------------------ read-outs

const better = (measure, a, b) =>
  measure?.higherIsBetter === false ? a < b : a > b;

/** Each column's weakest group (needs ≥ 2 groups with values). */
export function weakestByColumn(pivot) {
  const out = {};
  if (!pivot || pivot.failedEval || pivot.rowKeys.length < 2) return out;
  pivot.columns.forEach((c) => {
    let worst = null;
    pivot.rowKeys.forEach((g) => {
      const v = pivot.cells[g][c.key].value;
      if (v == null) return;
      if (
        worst == null ||
        better(pivot.measure, pivot.cells[worst][c.key].value, v)
      )
        worst = g;
    });
    if (worst != null) out[c.key] = worst;
  });
  return out;
}

/** The group with the largest max − min across columns. */
export function biggestSpread(pivot) {
  if (!pivot || pivot.failedEval || pivot.columns.length < 2) return null;
  let best = null;
  pivot.rowKeys.forEach((g) => {
    const vals = pivot.columns
      .map((c) => pivot.cells[g][c.key].value)
      .filter((v) => v != null);
    if (vals.length < 2) return;
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    if (!best || hi - lo > best.hi - best.lo) best = { group: g, lo, hi };
  });
  return best;
}

/** `failing rows = n × fail rate` per group (F7 sort), summed over columns. */
export const failingOf = (pivot, g) =>
  pivot.columns.reduce((s, c) => {
    const cell = pivot.cells[g][c.key];
    return s + (cell.pass != null ? cell.n - cell.pass : 0);
  }, 0);

/** Row keys in the chosen order (`failing` = most failing attempts first). */
export function sortedRowKeys(pivot, sort) {
  if (!pivot) return [];
  if (sort !== "failing" || pivot.failedEval) return pivot.rowKeys;
  return [...pivot.rowKeys].sort(
    (a, b) => failingOf(pivot, b) - failingOf(pivot, a),
  );
}

export { better as isBetter };
