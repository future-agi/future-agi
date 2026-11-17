// Chart series for the Workbench (R2-SPEC §6.3.3). Pure, over R3 matrix
// cells with the pivot's counting rules; every bin / dot keeps its row ids.
import {
  configDiff,
  configDiffSentence,
} from "src/sections/common/Runs/configDiff";
import { pareto } from "src/sections/common/Analysis/runStats";
import { attemptsOf, groupsOfRow } from "./workbenchModel";

const PASS_AT_1 = { key: "pass_at_1", kind: "rate" };

function rowRate(attempts) {
  const done = attempts.filter((a) => a && a.pass !== undefined);
  if (!done.length) return null;
  return done.reduce((s, a) => s + a.pass, 0) / done.length;
}

function rowMean(attempts) {
  const xs = attempts.filter((a) => a && a.x !== undefined).map((a) => a.x);
  return xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null;
}

/**
 * Distribution: rate measures → rows by per-row pass rate (0, 1/3, 2/3, 1);
 * values → attempts in 20 bins with the pass threshold and `below` counts.
 * @returns {{kind: "levels"|"bins", bins: Array<{label, from, to,
 *   counts: Object, rows: Object}>, threshold: number|null,
 *   below: Object<string, number>}}
 */
export function distributionSeries(
  matrix,
  columns,
  measure,
  { bins = 20 } = {},
) {
  const rows = matrix?.rows ?? [];
  if (measure?.kind === "rate") {
    const k = Math.max(
      1,
      ...rows.flatMap((r) =>
        Object.values(r.cells ?? {}).map((c) => c.trials?.length ?? 0),
      ),
    );
    const out = Array.from({ length: k + 1 }, (_, i) => ({
      label: `${i}/${k}`,
      from: i / k,
      to: i / k,
      counts: {},
      rows: {},
    }));
    rows.forEach((row) =>
      columns.forEach((col) =>
        col.runIds.forEach((runId) => {
          const cell = row.cells?.[runId];
          if (!cell) return;
          const r = rowRate(attemptsOf(cell, measure));
          if (r == null) return;
          const b = out[Math.round(r * k)];
          b.counts[col.key] = (b.counts[col.key] ?? 0) + 1;
          (b.rows[col.key] ??= []).push(row.row_id);
        }),
      ),
    );
    return { kind: "levels", bins: out, threshold: null, below: {} };
  }
  const xs = [];
  const points = [];
  rows.forEach((row) =>
    columns.forEach((col) =>
      col.runIds.forEach((runId) => {
        const cell = row.cells?.[runId];
        if (!cell) return;
        attemptsOf(cell, measure).forEach((a) => {
          if (!a || a.x === undefined) return;
          xs.push(a.x);
          points.push({ col: col.key, x: a.x, row: row.row_id });
        });
      }),
    ),
  );
  if (!xs.length) return { kind: "bins", bins: [], threshold: null, below: {} };
  const def = measure?.def;
  let lo = Number.isFinite(def?.min) ? def.min : Math.min(...xs);
  let hi = Number.isFinite(def?.max) ? def.max : Math.max(...xs);
  if (measure?.kind === "score" && lo >= 0 && hi <= 1) {
    lo = 0;
    hi = 1;
  }
  if (hi <= lo) hi = lo + 1;
  const w = (hi - lo) / bins;
  const out = Array.from({ length: bins }, (_, i) => ({
    label: String(Math.round((lo + i * w) * 100) / 100),
    from: lo + i * w,
    to: lo + (i + 1) * w,
    counts: {},
    rows: {},
  }));
  points.forEach((p) => {
    const i = Math.min(bins - 1, Math.max(0, Math.floor((p.x - lo) / w)));
    const b = out[i];
    b.counts[p.col] = (b.counts[p.col] ?? 0) + 1;
    (b.rows[p.col] ??= []).push(p.row);
  });
  out.forEach((b) =>
    Object.keys(b.rows).forEach((c) => {
      b.rows[c] = [...new Set(b.rows[c])];
    }),
  );
  const threshold = Number.isFinite(def?.pass_threshold)
    ? def.pass_threshold
    : null;
  const below = {};
  if (threshold != null)
    points.forEach((p) => {
      if (p.x < threshold) below[p.col] = (below[p.col] ?? 0) + 1;
    });
  return { kind: "bins", bins: out, threshold, below };
}

/**
 * Cost vs quality: one dot per column (mean cost per attempt, quality =
 * the measure when it is a rate / score, else pass@1) + the Pareto frontier.
 */
export function costQualityPoints(matrix, columns, measure, pivot) {
  const useMeasure = measure?.kind === "rate" || measure?.kind === "score";
  const pts = columns.map((col) => {
    let cost = 0;
    let n = 0;
    let pass = 0;
    let scored = 0;
    (matrix?.rows ?? []).forEach((row) =>
      col.runIds.forEach((runId) => {
        const cell = row.cells?.[runId];
        if (!cell) return;
        attemptsOf(cell, { key: "cost", kind: "numeric" }).forEach((a) => {
          if (a) {
            cost += a.x;
            n += 1;
          }
        });
        attemptsOf(cell, PASS_AT_1).forEach((a) => {
          if (a) {
            scored += 1;
            pass += a.pass;
          }
        });
      }),
    );
    const quality = useMeasure
      ? pivot?.colTotals?.[col.key]?.value ?? null
      : scored
        ? pass / scored
        : null;
    return {
      id: col.key,
      col,
      cost: n ? cost / n : null,
      quality,
      n: useMeasure ? pivot?.colTotals?.[col.key]?.n : scored,
    };
  });
  const front = pareto(
    pts.map((p) => ({ id: p.id, cost: p.cost, quality: p.quality })),
  );
  return pts.map((p, i) => ({
    ...p,
    frontier: front[i]?.frontier ?? false,
    dominatedBy: front[i]?.dominated_by ?? null,
  }));
}

/**
 * Paired scatter (rows set, exactly 2 columns): one bubble per (x, y) pair
 * of per-row values, x = baseline column, y = the other; with the counts
 * above / below / on the diagonal.
 */
export function pairedPoints(matrix, [baseCol, candCol], measure) {
  const map = new Map();
  const sides = { better: [], worse: [], same: [] };
  (matrix?.rows ?? []).forEach((row) => {
    const val = (col) => {
      const vals = col.runIds
        .map((id) => row.cells?.[id])
        .filter(Boolean)
        .map((c) => {
          const a = attemptsOf(c, measure);
          return measure?.kind === "rate" ? rowRate(a) : rowMean(a);
        })
        .filter((v) => v != null);
      return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    };
    const x = val(baseCol);
    const y = val(candCol);
    if (x == null || y == null) return;
    const key = `${x.toFixed(4)}|${y.toFixed(4)}`;
    if (!map.has(key)) map.set(key, { x, y, rows: [], labels: [] });
    const p = map.get(key);
    p.rows.push(row.row_id);
    p.labels.push(row.label ?? `Row ${row.n}`);
    const up = measure?.higherIsBetter === false ? y < x : y > x;
    const down = measure?.higherIsBetter === false ? y > x : y < x;
    sides[up ? "better" : down ? "worse" : "same"].push(row.row_id);
  });
  return { points: [...map.values()], sides };
}

const CHIP_MAX = 22;
const short = (s) => (s.length > CHIP_MAX ? `${s.slice(0, CHIP_MAX - 1)}…` : s);

/** Hop label between two runs: `6 rows rerun` / `all 30 rows` / the change. */
export function hopLabel(prev, cur) {
  if (!prev || !cur) return { hop: null, detail: null };
  const chips = configDiff(prev, cur);
  const detail = chips.length
    ? [chips.map((c) => c.label).join(" · "), configDiffSentence(chips)]
        .filter(Boolean)
        .join(" ")
    : null;
  const it = cur.iteration;
  if (it && cur.parent_run_id) {
    const hop =
      it.scope === "all"
        ? `all ${it.rows_rerun ?? cur.config?.dataset?.rows ?? ""} rows`.replace(
            "  ",
            " ",
          )
        : `${it.rows_rerun} rows rerun`;
    return { hop, detail };
  }
  if (!chips.length) return { hop: null, detail: null };
  if (chips[0].axis === "kind")
    return { hop: chips[0].detail?.to ?? "new kind", detail };
  const first = chips[0].label.replace(/^(\w+) /, "");
  return {
    hop: short(chips.length > 1 ? `${first} +${chips.length - 1}` : first),
    detail,
  };
}

/**
 * Trend points: the columns in order (cols=run → runs by start time), the
 * column total as the value, a hop label from the previous point.
 */
export function trendPoints(pivot, { axis, runsById }) {
  const cols = [...(pivot?.columns ?? [])];
  if (axis === "run" || !axis)
    cols.sort(
      (a, b) =>
        String(runsById?.[a.key]?.created_at ?? "").localeCompare(
          String(runsById?.[b.key]?.created_at ?? ""),
        ) ||
        String(runsById?.[a.key]?.label ?? "").localeCompare(
          String(runsById?.[b.key]?.label ?? ""),
        ),
    );
  return cols.map((col, i) => {
    const run = col.runIds.length === 1 ? runsById?.[col.runIds[0]] : null;
    const prevCol = cols[i - 1];
    const prev =
      prevCol && prevCol.runIds.length === 1
        ? runsById?.[prevCol.runIds[0]]
        : null;
    const { hop, detail } =
      i > 0 && run && prev ? hopLabel(prev, run) : { hop: null, detail: null };
    return {
      index: i,
      key: col.key,
      label: run?.label ?? col.label,
      name: run?.name ?? null,
      col,
      cell: pivot.colTotals[col.key],
      hop,
      hopDetail: detail,
    };
  });
}

/** Group values of the rows (for a highlight / slice lookup). */
export const rowsInGroup = (matrix, key, value) =>
  (matrix?.rows ?? [])
    .filter((r) => groupsOfRow(r, key).includes(value))
    .map((r) => r.row_id);
