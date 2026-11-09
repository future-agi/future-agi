// Matrix tab model (R2-SPEC §3.3.1, §3.3.2, §8.2). Pure: no React, no I/O.
//
// The R3 `shape=matrix` read returns cells, not statistics. Everything the
// Matrix shows is counted here over runStats.js, so a count on screen always
// equals the rows it filters to: column blocks (pass@1, pass^k, $ / row,
// p50 / row, ▲ ▼ vs the baseline), the Show sets, sorts, groups, the bottom
// rows, the Pareto points (with a seeded bootstrap CI) and its sentence.
import {
  disagreement,
  groupValueOf,
  pareto,
  passAt1,
  passHatK,
  rowPassRate,
} from "src/sections/common/Analysis/runStats";
import {
  fmtCost,
  fmtCount,
  fmtMs,
  fmtMultiple,
  fmtNumber,
  fmtPct,
  fmtPts,
  ptsBetween,
} from "src/sections/common/format/fmt";
import { runSpokenName } from "src/sections/common/Runs/runLabel";
import { VOCAB } from "src/sections/common/vocab";
import { baselineRoleOf } from "./verdictScope";
import { ptsDelta } from "src/sections/common/Runs/runNumbers";
import { getRunStatus } from "src/sections/common/Runs/runStatus";

// ------------------------------------------------------------- formatting

/** `86.6%` (one decimal) or `—` (G0 fmt.js). */
export function pct1(rate) {
  return fmtPct(rate);
}

/** `93%` (whole percent) or `—`. */
export function pct0(rate) {
  return fmtPct(rate, { dp: 0 });
}

/**
 * `$0.004` · `$0.011` · `$0.21` (G0 fmt.js: two significant figures under
 * $1). IT-R5 G6 P3: under one cent a trailing zero is dropped (`$0.0040` →
 * `$0.004`), so the run header reads 2 significant digits, not a padded 4.
 */
export function fmtMoney(usd) {
  const text = fmtCost(usd);
  const n = Number(usd);
  if (!Number.isFinite(n) || n === 0 || Math.abs(n) >= 0.01) return text;
  return text.replace(/(\.\d*?[1-9])0+$/, "$1");
}

/** `412 ms` · `6.1 s` · `12 s` · `74 s` · `12 m 05 s` (G0 fmt.js). */
export function fmtDuration(ms) {
  return fmtMs(ms);
}

/** `48/90` with en-US thousands. */
const frac = (a, b) => `${fmtNumber(Number(a))}/${fmtNumber(Number(b))}`;

/** `53.3% (48/90)` or `—` when nothing was scored. */
export function rateText(r) {
  if (!r || !r.n) return "—";
  return `${pct1(r.rate)} (${frac(r.pass, r.n)})`;
}

// ------------------------------------------------------------------ trials

const isPass = (t) => t === 1 || t === true;
const isScored = (t) =>
  t === 1 || t === 0 || t === true || t === false || t === "error";
const ran = (t) => isScored(t);

/** Trial dot kind: pass · fail · error · infra · pending. */
export function trialKind(t) {
  if (isPass(t)) return "pass";
  if (t === "error") return "error";
  if (t === "infra") return "infra";
  if (t === 0 || t === false) return "fail";
  return "pending";
}

const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const mean = (xs) =>
  xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

/** Attempt-level numbers of one cell (attempts that ran). */
export function cellNumbers(cell) {
  const trials = cell?.trials ?? [];
  const cost = [];
  const wall = [];
  let pass = 0;
  let scored = 0;
  trials.forEach((t, i) => {
    if (!ran(t)) return;
    scored += 1;
    if (isPass(t)) pass += 1;
    const c = cell?.cost_usd?.[i];
    const w = cell?.wall_ms?.[i];
    if (Number.isFinite(c)) cost.push(c);
    if (Number.isFinite(w)) wall.push(w);
  });
  return {
    pass,
    scored,
    total: trials.length,
    rate: scored ? pass / scored : null,
    cost: mean(cost),
    costSum: cost.reduce((a, b) => a + b, 0),
    wall: mean(wall),
    allPass: scored > 0 && pass === scored && scored === trials.length,
    noPass: scored > 0 && pass === 0,
  };
}

/** The first failed eval of a cell (`order_found`), or null. */
export function firstFailedEval(cell) {
  const list = cell?.failed_evals ?? [];
  for (let i = 0; i < list.length; i += 1) {
    const evals = Array.isArray(list[i]) ? list[i] : list[i] ? [list[i]] : [];
    if (evals.length) return evals[0];
  }
  return null;
}

// ---------------------------------------------------------------- columns

/**
 * Column block of one run.
 * @param {object} matrix  R3 shape=matrix
 * @param {string} runId
 * @param {string|null} baselineId
 * @returns {{runId, k, passAt1, passHatK, passAt1Text, passHatKText,
 *   costPerRow, p50, costText, p50Text, better, worse, infra, errors,
 *   infraText, errorText, attempts}}
 */
export function columnStats(matrix, runId, baselineId) {
  const rows = matrix?.rows ?? [];
  const col = matrix?.columns?.[runId] ?? {};
  const cells = rows.map((r) => r.cells?.[runId]).filter(Boolean);
  const k = Math.max(
    1,
    Number(col.trials) ||
      Math.max(0, ...cells.map((c) => (c.trials ?? []).length)) ||
      1,
  );
  const p1 = passAt1(cells);
  const pk = k > 1 ? passHatK(cells, k) : null;
  const cost = [];
  const wall = [];
  let infra = 0;
  let errors = 0;
  cells.forEach((c) =>
    (c.trials ?? []).forEach((t, i) => {
      if (t === "infra") infra += 1;
      if (t === "error") errors += 1;
      if (!ran(t)) return;
      if (Number.isFinite(c.cost_usd?.[i])) cost.push(c.cost_usd[i]);
      if (Number.isFinite(c.wall_ms?.[i])) wall.push(c.wall_ms[i]);
    }),
  );
  let better = 0;
  let worse = 0;
  const betterRows = [];
  const worseRows = [];
  if (baselineId && baselineId !== runId)
    rows.forEach((r) => {
      const a = r.cells?.[baselineId];
      const b = r.cells?.[runId];
      if (!a || !b) return;
      const ra = rowPassRate(a.trials ?? []);
      const rb = rowPassRate(b.trials ?? []);
      if (ra == null || rb == null) return;
      if (rb > ra) {
        better += 1;
        betterRows.push(r.row_id);
      } else if (rb < ra) {
        worse += 1;
        worseRows.push(r.row_id);
      }
    });
  const costPerRow = mean(cost);
  const p50 = median(wall);
  return {
    runId,
    k,
    label: col.label ?? null,
    name: col.name ?? null,
    passAt1: p1,
    passHatK: pk,
    passAt1Text: rateText(p1),
    passHatKText: pk ? rateText(pk) : "—",
    costPerRow,
    p50,
    costText: fmtMoney(costPerRow),
    p50Text: fmtDuration(p50),
    better,
    worse,
    betterRows,
    worseRows,
    infra,
    errors,
    infraText: infra
      ? `${fmtNumber(infra)} didn't run (setup) · not counted`
      : null,
    errorText: errors
      ? `${fmtNumber(errors)} ${errors === 1 ? "error · counted as a fail" : "errors · counted as fails"}`
      : null,
    attempts: cells.reduce((s, c) => s + (c.trials ?? []).length, 0),
  };
}

// ------------------------------------------------------------- show / sort

// R3 F3 chip names (vocab.js); `term` = the glossary entry of the tooltip.
export const SHOW_OPTIONS = Object.freeze([
  { value: "all", label: VOCAB.CHIP_ALL, tooltip: "Every task in view." },
  {
    // IT-R2 G2 P3: `Pass/fail differs`, never a bare `Differ` next to the
    // verdict's `8 better` (a task can get better without flipping).
    value: "disagree",
    label: "Pass/fail differs",
    term: "pairDiffers",
    rail: "differ",
    tooltip:
      "Pass/fail differs: at least one run passes every try of this task and at least one fails every try. Not the same as better / worse, which also counts a task going from 1 of 3 to 3 of 3.",
  },
  {
    value: "flaky",
    label: VOCAB.CHIP_FLAKY,
    term: "flaky",
    rail: "flaky",
    tooltip: "Flaky: passes some tries and fails others (in at least one run).",
  },
  {
    value: "nobody",
    label: VOCAB.CHIP_NO_RUN_RELIABLE,
    term: "reliable",
    tooltip:
      "No run reliable: every run is flaky or failing on these rows. Check the task and its checks before blaming the runs.",
  },
  {
    value: "everyone",
    label: VOCAB.CHIP_ALL_RELIABLE,
    term: "reliable",
    tooltip: "All reliable: every run passes every try of this task.",
  },
]);

export const SORT_OPTIONS = Object.freeze([
  { value: "disagreement", label: "Most disagreement" },
  { value: "row", label: "Row #" },
  { value: "baseline", label: "Baseline worst first" },
  { value: "flaky", label: "Most flaky" },
  { value: "expensive", label: "Most expensive" },
  { value: "gap", label: "Biggest gap vs baseline" },
]);

export const GROUP_OPTIONS = Object.freeze([
  { value: "none", label: "None" },
  { value: "category", label: "Category" },
  { value: "difficulty", label: "Difficulty" },
  { value: "split", label: "Split" },
  { value: "source", label: "Source" },
  { value: "failed_eval", label: "Failed eval" },
]);

/** `disagreement` (default) · `row` · … · `run:<id>`; unknown → default. */
export function normalizeSort(sort, runIds = []) {
  if (!sort) return "disagreement";
  if (SORT_OPTIONS.some((o) => o.value === sort)) return sort;
  const m = /^run:(.+)$/.exec(sort);
  if (m && runIds.includes(m[1])) return sort;
  return "disagreement";
}

/** The matrix restricted to `runIds` (cells of other runs dropped). */
export function restrictMatrix(matrix, runIds) {
  const keep = new Set(runIds ?? []);
  return {
    ...matrix,
    rows: (matrix?.rows ?? []).map((r) => ({
      ...r,
      cells: Object.fromEntries(
        Object.entries(r.cells ?? {}).filter(([id]) => keep.has(id)),
      ),
    })),
    columns: Object.fromEntries(
      Object.entries(matrix?.columns ?? {}).filter(([id]) => keep.has(id)),
    ),
  };
}

const rateOf = (row, runId) => {
  const c = row.cells?.[runId];
  return c ? rowPassRate(c.trials ?? []) : null;
};

function sortRows(rows, sort, { baselineId, sets, runIds }) {
  const byN = (a, b) => a.n - b.n;
  const list = [...rows];
  if (sort === "row") return list.sort(byN);
  if (sort === "disagreement") {
    const pos = new Map(sets.order.map((id, i) => [id, i]));
    return list.sort(
      (a, b) =>
        (pos.get(a.row_id) ?? 1e9) - (pos.get(b.row_id) ?? 1e9) || byN(a, b),
    );
  }
  const asc = (fn) =>
    list.sort((a, b) => {
      const x = fn(a);
      const y = fn(b);
      if (x == null && y == null) return byN(a, b);
      if (x == null) return 1;
      if (y == null) return -1;
      return x - y || byN(a, b);
    });
  const desc = (fn) => asc((r) => (fn(r) == null ? null : -fn(r)));
  if (sort === "baseline") return asc((r) => rateOf(r, baselineId));
  if (sort.startsWith("run:")) return asc((r) => rateOf(r, sort.slice(4)));
  if (sort === "flaky")
    return desc(
      (r) =>
        runIds.filter((id) => {
          const n = cellNumbers(r.cells?.[id]);
          return n.scored > 0 && n.pass > 0 && n.pass < n.scored;
        }).length,
    );
  if (sort === "expensive")
    return desc((r) => {
      const xs = runIds
        .map((id) => cellNumbers(r.cells?.[id]).cost)
        .filter(Number.isFinite);
      return xs.length ? mean(xs) : null;
    });
  if (sort === "gap")
    return desc((r) => {
      const base = rateOf(r, baselineId);
      if (base == null) return null;
      const gaps = runIds
        .filter((id) => id !== baselineId)
        .map((id) => rateOf(r, id))
        .filter((x) => x != null)
        .map((x) => Math.abs(x - base));
      return gaps.length ? Math.max(...gaps) : null;
    });
  return list.sort(byN);
}

// ------------------------------------------------------------------ groups

const NONE_LABEL = {
  category: "No category",
  difficulty: "No difficulty",
  split: "No split",
  source: "No source",
  failed_eval: "No failed eval",
};

/** Group value of a matrix row (`failed_eval` = its most frequent failed eval). */
export function rowGroupValue(row, group, runIds) {
  if (!group || group === "none") return null;
  if (group === "failed_eval") {
    const counts = new Map();
    runIds.forEach((id) =>
      (row.cells?.[id]?.failed_evals ?? []).forEach((evals) =>
        (Array.isArray(evals) ? evals.slice(0, 1) : [evals])
          .filter(Boolean)
          .forEach((e) => counts.set(e, (counts.get(e) ?? 0) + 1)),
      ),
    );
    if (!counts.size) return NONE_LABEL.failed_eval;
    return [...counts.entries()].sort(
      (a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])),
    )[0][0];
  }
  const v = groupValueOf(row, group);
  return v === "—" ? NONE_LABEL[group] ?? "—" : v;
}

/** Per-run pass@1 of a set of rows (`{rate, pass, n, lowSample}`). */
function groupRates(rows, runIds) {
  return Object.fromEntries(
    runIds.map((id) => {
      const r = passAt1(rows.map((row) => row.cells?.[id]).filter(Boolean));
      return [id, { ...r, lowSample: r.n < 10 }];
    }),
  );
}

/** Rows where some run passes every trial (HAL's saturation row). */
function anySolved(rows, runIds) {
  return rows.filter((r) =>
    runIds.some((id) => cellNumbers(r.cells?.[id]).allPass),
  ).length;
}

// ----------------------------------------------------------- the model

/**
 * Everything the Matrix renders.
 * @param {object} matrix  R3 shape=matrix
 * @param {{runIds: string[], baselineId?: string|null, group?: string,
 *   sort?: string, show?: string, hidden?: string[]}} opts
 */
export function buildMatrixModel(matrix, opts = {}) {
  const hidden = new Set(opts.hidden ?? []);
  const runIds = (opts.runIds ?? []).filter(
    (id) => matrix?.columns?.[id] && !hidden.has(id),
  );
  const baselineId =
    opts.baselineId && runIds.includes(opts.baselineId)
      ? opts.baselineId
      : null;
  const m = restrictMatrix(matrix, runIds);
  const rows = (m.rows ?? []).map((r) => ({
    ...r,
    label: r.label ?? `Row ${r.n}`,
  }));
  const sets = disagreement({ rows });
  const showCounts = {
    all: sets.all.length,
    disagree: sets.disagree.length,
    flaky: sets.flaky.length,
    nobody: sets.nobody.length,
    everyone: sets.everyone.length,
  };
  const show = SHOW_OPTIONS.some((o) => o.value === opts.show)
    ? opts.show
    : "all";
  const sort = normalizeSort(opts.sort, runIds);
  const group = opts.group && opts.group !== "none" ? opts.group : "none";
  const shownIds = new Set(sets[show] ?? sets.all);
  const extraFilter = opts.rowIds ? new Set(opts.rowIds) : null;
  const visible0 = sortRows(
    rows.filter(
      (r) =>
        shownIds.has(r.row_id) && (!extraFilter || extraFilter.has(r.row_id)),
    ),
    sort,
    { baselineId, sets, runIds },
  );
  // IT-R4 G6 P6: on `Pass/fail differs` the tasks where the page candidate
  // got worse than the baseline come first (then the default order), and
  // so do their groups.
  const candId =
    opts.candidateId &&
    opts.candidateId !== baselineId &&
    runIds.includes(opts.candidateId)
      ? opts.candidateId
      : null;
  const candWorse = (r) => {
    if (!candId || !baselineId) return false;
    const c = rateOf(r, candId);
    const b = rateOf(r, baselineId);
    return c != null && b != null && c < b - 1e-9;
  };
  const ordered =
    show === "disagree" && candId && sort === "disagreement"
      ? [
          ...visible0.filter(candWorse),
          ...visible0.filter((r) => !candWorse(r)),
        ]
      : visible0;
  const visible = ordered.map((r) => {
    const solved = runIds.filter((id) => cellNumbers(r.cells?.[id]).allPass);
    const present = runIds.filter((id) => r.cells?.[id]);
    return {
      ...r,
      solvedBy: solved.length,
      runsWithRow: present.length,
      solvedText: `Solved by ${solved.length} of ${present.length}`,
    };
  });

  let groups = null;
  if (group !== "none") {
    const map = new Map();
    // Group order: first appearance in row-number order (stable across sorts).
    [...rows]
      .sort((a, b) => a.n - b.n)
      .forEach((r) => {
        const key = rowGroupValue(r, group, runIds);
        if (!map.has(key)) map.set(key, { key, all: [], rows: [] });
        map.get(key).all.push(r);
      });
    visible.forEach((r) =>
      map.get(rowGroupValue(r, group, runIds))?.rows.push(r),
    );
    const worseIn = (g) => g.rows.filter(candWorse).length;
    groups = [...map.values()]
      .filter((g) => g.rows.length)
      .sort((a, b) =>
        show === "disagree" && candId ? worseIn(b) - worseIn(a) : 0,
      )
      .map((g) => ({
        key: g.key,
        rows: g.rows,
        count: g.rows.length,
        rates: groupRates(g.rows, runIds),
        anySolved: anySolved(g.rows, runIds),
        header: [
          g.key,
          `${g.rows.length} ${g.rows.length === 1 ? "row" : "rows"}`,
          ...runIds.map((id) => pct0(groupRates(g.rows, [id])[id].rate)),
        ].join(" · "),
      }));
  }

  const columns = runIds.map((id) => columnStats(m, id, baselineId));
  const byLabel = (ids) =>
    rows
      .filter((r) => ids.includes(r.row_id))
      .sort((a, b) => a.n - b.n)
      .map((r) => r.label);
  // Closest to solved first (more runs with at least one pass): T19, T10.
  const orderedLabels = (ids) => {
    const someRuns = (r) =>
      runIds.filter((id) => cellNumbers(r.cells?.[id]).pass > 0).length;
    return rows
      .filter((r) => ids.includes(r.row_id))
      .sort((a, b) => someRuns(b) - someRuns(a) || a.n - b.n)
      .map((r) => ({ id: r.row_id, label: r.label }));
  };

  return {
    runIds,
    baselineId,
    show,
    sort,
    group,
    rows: visible,
    allRows: rows,
    groups,
    sets,
    showCounts,
    columns,
    columnsById: Object.fromEntries(columns.map((c) => [c.runId, c])),
    trials: Math.max(1, ...columns.map((c) => c.k)),
    anySolved: anySolved(visible, runIds),
    nobody: orderedLabels(sets.nobody),
    everyone: byLabel(sets.everyone).map((label) => ({
      label,
      id: rows.find((r) => r.label === label)?.row_id,
    })),
    headerText: matrixHeaderText(rows.length, runIds.length, columns),
  };
}

/** `30 rows · 5 runs · 3 trials each` (or `1–3 trials`). */
export function matrixHeaderText(rowCount, runCount, columns) {
  const ks = [...new Set((columns ?? []).map((c) => c.k))];
  const trials =
    ks.length <= 1
      ? `${ks[0] ?? 1} ${ks[0] === 1 ? "trial" : "trials"} each`
      : `${Math.min(...ks)}–${Math.max(...ks)} trials`;
  return `${fmtCount(rowCount, "row")} · ${fmtCount(runCount, "run")} · ${trials}`;
}

/** Background tint alpha for a pass rate (0 → red, 1 → green), or null. */
export function tintFor(rate) {
  if (!Number.isFinite(rate)) return null;
  return { tone: rate >= 0.5 ? "pass" : "fail", rate };
}

// ---------------------------------------------------------- auto pair

/**
 * The run a cell's trajectory is compared with (§3.3.5 default `vs`):
 * the baseline if it passed this row, else the cheapest run that passed all
 * trials, else the run's parent (previous iteration), else the baseline.
 */
export function autoPair(row, runId, { baselineId, runIds, parentOf } = {}) {
  const others = (runIds ?? []).filter((id) => id !== runId);
  if (!others.length) return null;
  const n = (id) => cellNumbers(row?.cells?.[id]);
  if (baselineId && baselineId !== runId && n(baselineId).allPass)
    return baselineId;
  const solved = others
    .filter((id) => n(id).allPass)
    .sort((a, b) => (n(a).cost ?? Infinity) - (n(b).cost ?? Infinity));
  if (solved.length) return solved[0];
  const parent = parentOf?.(runId);
  if (parent && others.includes(parent)) return parent;
  return baselineId && baselineId !== runId ? baselineId : others[0];
}

// ------------------------------------------------------------- bootstrap

/** mulberry32: a small seeded PRNG (deterministic CI bars). */
export function seededRandom(seed = 1) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const hashSeed = (s) =>
  String(s ?? "")
    .split("")
    .reduce((h, ch) => (Math.imul(h, 31) + ch.charCodeAt(0)) >>> 0, 7);

/**
 * 95% CI on pass@1 by a row-level bootstrap (resample rows with
 * replacement, pass@1 = passes / scored attempts), seeded by the run id.
 * @returns {{low: number, high: number}|null}
 */
export function bootstrapCi(cells, { resamples = 2000, seed = 1 } = {}) {
  const per = (cells ?? [])
    .map((c) => cellNumbers(c))
    .filter((x) => x.scored > 0);
  if (!per.length) return null;
  const rand = seededRandom(seed);
  const stats = new Array(resamples);
  for (let s = 0; s < resamples; s += 1) {
    let pass = 0;
    let n = 0;
    for (let i = 0; i < per.length; i += 1) {
      const pick = per[Math.floor(rand() * per.length)];
      pass += pick.pass;
      n += pick.scored;
    }
    stats[s] = n ? pass / n : 0;
  }
  stats.sort((a, b) => a - b);
  const at = (q) => stats[Math.min(resamples - 1, Math.floor(q * resamples))];
  return { low: at(0.025), high: at(0.975) };
}

// ----------------------------------------------------------------- pareto

/** `the coding harness Code` · `LangGraph support agent` · `Support Agent v3` · `v3.1`. */
export function agentShortName(run) {
  const c = run?.config ?? {};
  if (c.harness?.name) return c.harness.name;
  if (c.code) {
    // IT-R3 finalize + L7b absent-sweep: a code run is named by its run name
    // (`LangGraph support agent`, `CrewAI support crew`, as the column and the
    // Summary do), so a verdict reads `#2C LangGraph support agent …`, never
    // the kind (`your … agent`). Only an unnamed run falls back to the kind.
    if (run?.name) return runSpokenName(run);
    const fw = String(c.code.framework ?? "").split(" ")[0];
    return fw ? `your ${fw} agent` : "your code";
  }
  if (c.agent?.name)
    return [
      c.agent.name,
      String(c.agent.version ?? "").replace(/\s*\(.*\)$/, ""),
    ]
      .filter(Boolean)
      .join(" ");
  if (c.prompt?.name)
    return [c.prompt.name, c.prompt.version].filter(Boolean).join(" ");
  return run?.name ?? run?.label ?? "this run";
}

/** The agent version name of an iteration (`v3.1`), else its short name. */
const versionName = (run) => {
  const v = run?.config?.agent?.version;
  return v ? String(v).replace(/\s*\(.*\)$/, "") : agentShortName(run);
};

const ratioText = (x) => fmtMultiple(x);

/**
 * Pareto points, frontier, CI bars and the sentence.
 * @param {object} matrix  R3 shape=matrix
 * @param {{runIds: string[], runsById?: object, x?: "cost"|"time",
 *   y?: "pass_at_1"|"pass_hat_k", seed?: number}} opts
 */
export function paretoModel(matrix, opts = {}) {
  const runIds = (opts.runIds ?? []).filter((id) => matrix?.columns?.[id]);
  const x = opts.x === "time" ? "time" : "cost";
  const y = opts.y === "pass_hat_k" ? "pass_hat_k" : "pass_at_1";
  const runsById = opts.runsById ?? {};
  const m = restrictMatrix(matrix, runIds);
  const pts = runIds.map((id) => {
    const col = columnStats(m, id, null);
    const cells = (m.rows ?? []).map((r) => r.cells?.[id]).filter(Boolean);
    const q = y === "pass_hat_k" ? col.passHatK ?? col.passAt1 : col.passAt1;
    return {
      id,
      label: col.label ?? runsById[id]?.label ?? id,
      cost:
        x === "cost" ? col.costPerRow : col.p50 != null ? col.p50 / 1000 : null,
      costPerRow: col.costPerRow,
      p50: col.p50,
      quality: q?.rate ?? null,
      // Rounded to the displayed one decimal: the sentence subtracts these.
      shown: Number.isFinite(q?.rate) ? Math.round(q.rate * 1000) / 10 : null,
      pass: q?.pass ?? 0,
      n: q?.n ?? 0,
      ci:
        y === "pass_at_1"
          ? bootstrapCi(cells, {
              seed: (opts.seed ?? 20261015) ^ hashSeed(id),
            })
          : null,
      col,
    };
  });
  const front = pareto(
    pts.map((p) => ({ id: p.id, cost: p.cost, quality: p.quality })),
  );
  const byId = Object.fromEntries(front.map((f) => [f.id, f]));
  const points = pts.map((p) => ({
    ...p,
    frontier: byId[p.id]?.frontier ?? false,
    dominatedBy: byId[p.id]?.dominated_by ?? null,
  }));
  const frontier = points
    .filter((p) => p.frontier && Number.isFinite(p.cost))
    .sort((a, b) => a.cost - b.cost);
  const labelOf = (id) => points.find((p) => p.id === id)?.label ?? "";
  const dominatedTooltip = (p) =>
    p.dominatedBy
      ? `Dominated by ${labelOf(p.dominatedBy)}: ${x === "cost" ? "cheaper" : "faster"} and better`
      : null;

  // Sentence: an iteration on the frontier speaks first; else the top two
  // frontier runs by quality.
  let sentence = null;
  const iter = frontier
    .filter((p) => {
      const run = runsById[p.id];
      return run?.iteration || run?.parent_run_id;
    })
    // IT-R5 G6 P2: the iteration with the most solved (as the Matrix header
    // prints it), then the newest — never a weaker sibling of the same
    // version.
    .sort(
      (a, b) =>
        (b.shown ?? -1) - (a.shown ?? -1) ||
        (runsById[b.id]?.iteration?.n ?? 0) -
          (runsById[a.id]?.iteration?.n ?? 0),
    )[0];
  const unit = x === "cost" ? "cost per task" : "time per task";
  if (iter) {
    // Nearest better run outside the iteration's own lineage, on the
    // frontier of the runs outside that lineage (`your LangGraph agent`).
    const lineage = (id) => {
      const r = runsById[id];
      return r?.iteration?.root_run_id ?? r?.parent_run_id ?? id;
    };
    const root = lineage(iter.id);
    const outside = points.filter(
      (p) => lineage(p.id) !== root && Number.isFinite(p.cost),
    );
    const outsideFront = new Set(
      pareto(
        outside.map((p) => ({ id: p.id, cost: p.cost, quality: p.quality })),
      )
        .filter((f) => f.frontier)
        .map((f) => f.id),
    );
    // IT-R5 G6 P2 (R8, rule 36): compared on the numbers the Matrix
    // header prints (one decimal), so 85.6% vs 85.6% reads `matches`.
    const better = outside
      .filter((p) => outsideFront.has(p.id) && p.shown >= iter.shown)
      .sort((a, b) => a.shown - b.shown || a.cost - b.cost)[0];
    if (better) {
      const share = Math.round((iter.cost / better.cost) * 100);
      const what = x === "cost" ? "cost" : "time";
      if (better.shown === iter.shown)
        sentence = `${versionName(runsById[iter.id])} matches ${agentShortName(runsById[better.id])} (${better.shown.toFixed(1)}% each) at ${share}% of its ${what}.`;
      else {
        // L7c g6 (chart:pareto-matrix): one rounding (runNumbers.ptsDelta
        // on the unrounded rates), the delta every verdict prints.
        const gap = fmtPts(ptsDelta(better.quality, iter.quality)).slice(1);
        sentence = `${versionName(runsById[iter.id])} sits on the frontier: ${gap} below ${agentShortName(runsById[better.id])} at ${share}% of its ${what}.`;
      }
    } else
      sentence = `${versionName(runsById[iter.id])} sits on the frontier with the best ${y === "pass_hat_k" ? "pass^k" : "pass@1"} here.`;
  } else if (frontier.length >= 2) {
    const byQ = [...frontier].sort((a, b) => b.quality - a.quality);
    const top = byQ[0];
    const next = byQ[1];
    const gap = fmtPts(ptsDelta(top.quality, next.quality)).slice(1);
    const ratio = next.cost > 0 ? top.cost / next.cost : null;
    sentence = `${agentShortName(runsById[top.id]) || top.label} passes ${gap} more trials than ${next.label} for ${ratioText(ratio)} the ${unit}.`;
  }
  return {
    x,
    y,
    points: points.map((p) => ({ ...p, tooltip: dominatedTooltip(p) })),
    frontier,
    sentence,
    differentKinds:
      new Set(runIds.map((id) => matrix?.columns?.[id]?.kind).filter(Boolean))
        .size > 1,
  };
}

// ------------------------------------------------------------ versions

/**
 * Different dataset versions among the runs: `{versions: ["v1","v2"],
 * text}` or null. `versionOf(runId)` returns a number or `v2`.
 */
export function versionsBanner(runIds, versionOf) {
  const vs = [
    ...new Set(
      (runIds ?? [])
        .map((id) => versionOf?.(id))
        .filter((v) => v != null && v !== ""),
    ),
  ]
    .map((v) => (String(v).startsWith("v") ? String(v) : `v${v}`))
    .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
  if (vs.length < 2) return null;
  const last = vs[vs.length - 1];
  const first = vs[0];
  return {
    versions: vs,
    text: `Runs used different versions (${vs.join(", ")}). Rows only in ${last} show as "not run" for ${first} runs.`,
  };
}

// ------------------------------------------------- R3 frontier + verdict

/** Best value = the cheapest frontier run within this many pts of the top. */
export const BEST_VALUE_PTS = 10;

/**
 * The compact frontier strip (Simple): `{best, value, text}` where `best` is
 * the frontier run that solves most and `value` the cheapest frontier run
 * within BEST_VALUE_PTS of it (null when that is `best` itself).
 * `Frontier: #2D most solved · #2C best value`.
 */
export function frontierSummary(pModel) {
  const front = (pModel?.frontier ?? []).filter((p) =>
    Number.isFinite(p.quality),
  );
  if (!front.length) return null;
  const best = [...front].sort(
    (a, b) => b.quality - a.quality || a.cost - b.cost,
  )[0];
  const value =
    [...front]
      .filter(
        (p) =>
          p.id !== best.id &&
          ptsBetween(best.quality, p.quality) <= BEST_VALUE_PTS &&
          p.cost < best.cost,
      )
      .sort((a, b) => a.cost - b.cost)[0] ?? null;
  const unit = pModel.x === "time" ? "fastest" : "best value";
  return {
    best,
    value,
    text: `Frontier: ${best.label} most solved${value ? ` · ${value.label} ${unit}` : ""}`,
  };
}

/**
 * Verdict inputs for every run in view vs the baseline, from the Matrix
 * column blocks (one definition of Success rate · Cost per task · Time per
 * task · tasks better / worse). Feed each `input` to G0 `verdictOf`.
 * @param {object} model  buildMatrixModel() result
 * @param {object} [runsById]
 * @returns {{runId, label, input, worse, worseRows, better}[]}
 */
export function verdictInputs(model, runsById = {}) {
  const base = model?.columnsById?.[model?.baselineId];
  if (!base) return [];
  const tasks = model.allRows?.length ?? model.rows?.length ?? 0;
  return model.columns
    .filter((c) => c.runId !== model.baselineId && c.passAt1?.n)
    .map((c) => {
      const run = runsById[c.runId];
      const label = run?.label ?? c.label ?? "";
      const name = run ? agentShortName(run) : c.name ?? undefined;
      return {
        runId: c.runId,
        label,
        // L7c merge (banner:verdict): an ended run has no try still to score.
        ended: Boolean(run && getRunStatus(run).terminal),
        worse: c.worse,
        worseRows: c.worseRows,
        better: c.better,
        input: {
          candidate: { label, name },
          baseline: {
            label: runsById[base.runId]?.label ?? base.label ?? "",
          },
          // IT-R2 G2 P5: `baseline` only for the dataset's pinned run.
          baselineRole: baselineRoleOf(run, runsById[base.runId]),
          metrics: {
            success: {
              cand: c.passAt1.rate,
              base: base.passAt1?.rate,
              n: tasks,
              tries: c.k,
              candN: c.passAt1.n,
              baseN: base.passAt1?.n,
            },
            costPerTask:
              Number.isFinite(c.costPerRow) && Number.isFinite(base.costPerRow)
                ? { cand: c.costPerRow, base: base.costPerRow }
                : undefined,
            timePerTask:
              Number.isFinite(c.p50) && Number.isFinite(base.p50)
                ? { cand: c.p50, base: base.p50 }
                : undefined,
          },
          worse: c.worse,
          better: c.better,
        },
      };
    });
}

/**
 * The R5 cause's first divergence as G0's `firstWrongStep` marker
 * (`first_divergence.step_n` is the failing lane's own step, the same number
 * markers() gives), for `firstWrongStepText`. Null without one.
 */
export function causeMarker(cause, laneLabel) {
  const fd = cause?.first_divergence;
  if (!fd) return null;
  const stepN = fd.step_n ?? fd.step_index;
  const stepName = fd.step_name ?? fd.node;
  if (stepN == null || !stepName) return null;
  return { laneLabel, stepN, stepName };
}
