// Workbench model (R2-SPEC §6.3). Pure: URL state, the rail's option
// lists and the pivot every chart and the table read. Every number is
// computed here from R3 `shape=matrix` cells with the runStats.js counting
// rules (pass / fail / error scored, `infra` and not-run excluded), so a
// count on screen always equals the rows its link opens (C10).
import { parsePath, resolvePath } from "src/sections/common/Values/faPath";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";

// ------------------------------------------------------------------ state

/** Params the Workbench owns (Reset clears exactly these). */
export const WB_KEYS = Object.freeze([
  "measure",
  "agg",
  "rows",
  "cols",
  "chart",
  "cand",
  "across",
  "config",
  "transpose",
  "sort",
  "xcfg",
  "bycols",
  "wbtab",
  "board",
]);
export const WB_AGGS = Object.freeze([
  "avg",
  "median",
  "p90",
  "min",
  "max",
  "std",
]);
export const AGG_LABELS = {
  avg: "Average",
  median: "Median",
  p90: "p90",
  min: "Minimum",
  max: "Maximum",
  std: "Std dev",
};
// `api`: the W5 path a tab needs (endpoints.develop.w5); a tab whose path
// is not registered stays absent (exploreTabs).
export const EXPLORE_TABS = Object.freeze([
  { key: "pivot", label: "Pivot & charts" },
  { key: "board", label: "Charts" },
  { key: "config", label: "Config" },
  { key: "run_metrics", label: "Run metrics", api: "derivedMetrics" },
  { key: "classification", label: "Classification", api: "classification" },
  { key: "best_of", label: "Best of N", api: "bestOf" },
]);

/** The tabs whose W5 path exists (`has(name)` from w5Api.hasW5). */
export const exploreTabs = (has = () => false) =>
  EXPLORE_TABS.filter((t) => !t.api || has(t.api));

/** The Explore tab from the URL (`pivot` when absent or unknown). */
export const readExploreTab = (sp, tabs = EXPLORE_TABS) => {
  const v = sp?.get?.("wbtab");
  return tabs.some((t) => t.key === v) ? v : "pivot";
};

/** W5 B5: Explore-only chart kinds (two-run scatter, delta bars). */
export const WB_EXTRA_CHARTS = Object.freeze(["scatter2", "delta_bars"]);
export const LOW_SAMPLE_ATTEMPTS = 10;

/**
 * The Workbench state from the URL (readR2Params + `agg`, `transpose`,
 * `sort`). Chart defaults to `bars` when the URL names none.
 */
export function readWorkbench(searchParams) {
  const sp =
    searchParams instanceof URLSearchParams
      ? searchParams
      : new URLSearchParams(searchParams ?? "");
  const r2 = readR2Params(sp);
  const agg = sp.get("agg");
  return {
    measure: r2.measure || "pass_at_1",
    agg: WB_AGGS.includes(agg) ? agg : "avg",
    rows: r2.rowsAxis || "none",
    cols: r2.cols,
    chart: WB_EXTRA_CHARTS.includes(sp.get("chart"))
      ? sp.get("chart")
      : sp.get("chart")
        ? r2.chart
        : "bars",
    cand: r2.cand,
    across: r2.across,
    config: r2.config,
    transpose: sp.get("transpose") === "1",
    sort: sp.get("sort") === "failing" ? "failing" : "default",
    extraConfigs: (sp.get("xcfg") || "").split("|").filter(Boolean),
    byCols: sp.get("bycols") || null,
  };
}

// --------------------------------------------------------------- measures

const isCheckKey = (key) => String(key).startsWith("check:");
const isRateDef = (def) =>
  ["pass_fail", "thumbs", "pairwise"].includes(def?.output_type);

/**
 * Measure picker options. `metricDefs` = R1 `metric_defs`; `k` = trials.
 * Each: `{key, label, short, kind:"rate"|"score"|"numeric"|"count",
 * higherIsBetter, evalType?:"Check"|"Judge", def?, unit?, disabled?}`.
 */
export function measureOptions(metricDefs, { k = 1, hasTurns = true } = {}) {
  const evals = (metricDefs ?? [])
    .filter((d) => d?.kind === "eval")
    .map((d) => ({
      key: d.key,
      label: d.name,
      short: d.name,
      kind: isRateDef(d) ? "rate" : "score",
      higherIsBetter: d.higher_is_better !== false,
      evalType: isCheckKey(d.key) ? "Check" : "Judge",
      def: d,
    }));
  return [
    {
      key: "pass_at_1",
      label: "Pass rate (pass@1)",
      short: "pass@1",
      kind: "rate",
      higherIsBetter: true,
    },
    {
      key: "pass_hat_k",
      label: "Passes every trial (pass^k)",
      short: `pass^${Math.max(2, k)}`,
      kind: "rate",
      higherIsBetter: true,
      rowLevel: true,
      disabled: k < 2 ? "Needs runs with 2 or more trials" : null,
    },
    ...evals,
    {
      key: "cost",
      label: "Cost per attempt",
      short: "cost",
      kind: "numeric",
      unit: "usd",
      higherIsBetter: false,
    },
    {
      key: "latency",
      label: "Latency",
      short: "latency",
      kind: "numeric",
      unit: "s",
      higherIsBetter: false,
    },
    {
      key: "steps",
      label: "Steps",
      short: "steps",
      kind: "numeric",
      higherIsBetter: false,
    },
    {
      key: "tool_calls",
      label: "Tool calls",
      short: "tool calls",
      kind: "numeric",
      higherIsBetter: false,
    },
    {
      key: "tool_errors",
      label: "Tool errors",
      short: "tool errors",
      kind: "count",
      higherIsBetter: false,
    },
    {
      key: "turns",
      label: "Turns",
      short: "turns",
      kind: "numeric",
      higherIsBetter: false,
      disabled: hasTurns ? null : "No conversation turns in these runs",
    },
  ];
}

/** The matrix `measure=` param for a measure key (evals only). */
export const matrixMeasureOf = (key) =>
  key?.startsWith("eval:") || key?.startsWith("check:") ? key : undefined;

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const scored = (t) => t === 1 || t === 0 || t === "error";

/**
 * One cell's attempts for a measure: per trial `null` (not scored) or
 * `{pass?: 0|1, x?: number}`.
 */
export function attemptsOf(cell, measure) {
  const trials = cell?.trials ?? [];
  const key = measure?.key ?? "pass_at_1";
  return trials.map((t, i) => {
    if (!scored(t)) return null;
    const ts = cell.trace_summary?.[i] ?? null;
    switch (key) {
      case "pass_at_1":
      case "pass_hat_k":
        return { pass: t === 1 ? 1 : 0 };
      case "cost":
        return num(cell.cost_usd?.[i]) == null ? null : { x: cell.cost_usd[i] };
      case "latency":
        return num(cell.wall_ms?.[i]) == null
          ? null
          : { x: cell.wall_ms[i] / 1000 };
      case "steps": {
        const x = num(ts?.steps) ?? num(cell.steps);
        return x == null ? null : { x };
      }
      case "tool_calls": {
        const x = num(ts?.tool_calls) ?? num(cell.tool_calls);
        return x == null ? null : { x };
      }
      case "tool_errors":
        return { x: ts?.status === "error" || cell.error?.[i] ? 1 : 0 };
      case "turns": {
        const x = num(ts?.turns);
        return x == null ? null : { x };
      }
      default: {
        const v = cell.values?.[i];
        if (v == null || typeof v === "string") return null;
        const x = typeof v === "boolean" ? Number(v) : num(v);
        if (x == null) return null;
        if (measure?.kind === "rate") return { pass: x >= 0.5 ? 1 : 0 };
        const th = measure?.def?.pass_threshold;
        return { x, pass: num(th) == null ? undefined : x >= th ? 1 : 0 };
      }
    }
  });
}

// ------------------------------------------------------------ rows axis

const FIXED_GROUPS = ["category", "difficulty", "split", "source"];
const GROUP_LABELS = {
  none: "None",
  row: "Row",
  "meta:category": "Category",
  "meta:difficulty": "Difficulty",
  "meta:split": "Split",
  source: "Source (file / session / trace)",
  failed_eval: "Failed eval",
  tag: "Tag",
  dataset: "Dataset",
};
export const MISSING = "(missing)";
const MAX_GROUP_VALUES = 50;

const isPrimitive = (v) =>
  v == null || ["string", "number", "boolean"].includes(typeof v);

/** `Category`, `Metadata · customer_tier`, `Column · intent`, `Path · a.b`. */
export function groupLabel(key) {
  if (!key) return "None";
  if (GROUP_LABELS[key]) return GROUP_LABELS[key];
  if (key.startsWith("meta:")) return `Metadata · ${key.slice(5)}`;
  if (key.startsWith("col:")) return `Column · ${key.slice(4)}`;
  if (key.startsWith("path:")) return `Path · ${pathOfKey(key).display}`;
  if (key.startsWith("expr:")) return `Expression · ${key.slice(5)}`;
  if (key.startsWith("cc:")) return `Custom column · ${key.slice(3)}`;
  return key;
}

/** `path:<col>:<path>` → `{column, path, display}`. */
export function pathOfKey(key) {
  const rest = String(key).slice(5);
  const at = rest.indexOf(":");
  const column = at >= 0 ? rest.slice(0, at) : "";
  const path = at >= 0 ? rest.slice(at + 1) : rest;
  const display = column
    ? `${column}${path.startsWith("[") ? "" : "."}${path}`
    : path;
  return { column, path, display };
}

/** Rows-axis key of an FA Path typed in `Path…` (`start_state.orders[0].status`). */
export function pathKeyOf(text, columns) {
  const parsed = parsePath(text, { columns });
  if (!parsed.ok || !parsed.steps.length) return null;
  const src = String(text)
    .trim()
    .replace(/^\$\.?/, "");
  if (!parsed.column) return `path::${src}`;
  const rest = src.slice(parsed.column.length).replace(/^\./, "");
  return rest ? `path:${parsed.column}:${rest}` : `col:${parsed.column}`;
}

/**
 * Rows-axis options for a matrix: fixed keys, then metadata keys, columns
 * with ≤ 50 values, Path…, Dataset (All datasets only), None. Each
 * `{key, label, disabled?}`.
 */
export function groupOptions(matrix, { across = "runs" } = {}) {
  const rows = matrix?.rows ?? [];
  if (across === "datasets")
    return [
      { key: "dataset", label: "Dataset" },
      { key: "none", label: "None" },
    ];
  const metaKeys = new Set();
  const colValues = new Map();
  rows.forEach((r) => {
    Object.keys(r.group_values ?? {}).forEach((k) => metaKeys.add(k));
    Object.entries(r.values ?? {}).forEach(([k, v]) => {
      if (!colValues.has(k)) colValues.set(k, new Set());
      if (isPrimitive(v)) colValues.get(k).add(v == null ? MISSING : String(v));
      else colValues.get(k).add(Symbol.for("object"));
    });
  });
  const distinct = (k) =>
    new Set(rows.map((r) => String(r.group_values?.[k] ?? MISSING))).size;
  const usefulMeta = [...metaKeys].filter(
    (k) =>
      !FIXED_GROUPS.includes(k) &&
      distinct(k) <= MAX_GROUP_VALUES &&
      distinct(k) < rows.length,
  );
  const cols = [...colValues.entries()]
    .filter(
      ([k, set]) =>
        !metaKeys.has(k) &&
        !FIXED_GROUPS.includes(k) &&
        ![...set].some((v) => typeof v === "symbol") &&
        set.size <= MAX_GROUP_VALUES &&
        set.size < rows.length,
    )
    .map(([k]) => k);
  const hasTags = rows.some((r) => (r.tags ?? r.group_values?.tags)?.length);
  return [
    { key: "row", label: "Row" },
    { key: "meta:category", label: "Category" },
    { key: "meta:difficulty", label: "Difficulty" },
    { key: "meta:split", label: "Split" },
    { key: "source", label: GROUP_LABELS.source },
    { key: "failed_eval", label: "Failed eval" },
    {
      key: "tag",
      label: "Tag",
      disabled: hasTags ? null : "No tags on these rows",
    },
    ...usefulMeta.map((k) => ({
      key: `meta:${k}`,
      label: groupLabel(`meta:${k}`),
    })),
    ...cols.map((k) => ({ key: `col:${k}`, label: groupLabel(`col:${k}`) })),
    { key: "path", label: "Path…" },
    // W5 B1: an ad-hoc grouping expression (workbench/matrixScope.js).
    { key: "expr", label: "Expression…" },
    { key: "none", label: "None" },
  ];
}

/**
 * The rows axis when the URL names none (R3 analysis fix): the first
 * built-in dimension that splits the tasks into 2–12 groups (Category, then
 * Difficulty), so a first look shows where runs differ instead of one
 * `All rows` cluster; `none` when no dimension splits them.
 */
export function defaultRowsAxis(matrix) {
  const rows = matrix?.rows ?? [];
  for (const k of ["category", "difficulty"]) {
    const values = new Set(
      rows.map((r) => r.group_values?.[k]).filter((v) => v != null && v !== ""),
    );
    if (values.size >= 2 && values.size <= 12 && values.size < rows.length)
      return `meta:${k}`;
  }
  return "none";
}

/**
 * `30 rows` or `30 rows · 90 trials` — what a pivot cell's `n` counts, so
 * a chart's `30` and a table's `90` never read as two different sizes.
 */
export function sampleText(cell) {
  const rows = cell?.rows ?? 0;
  const n = cell?.n ?? 0;
  const fmt = (v) => Number(v).toLocaleString("en-US");
  const rowsText = `${fmt(rows)} ${rows === 1 ? "row" : "rows"}`;
  if (!n || n === rows) return rowsText;
  return `${rowsText} · ${fmt(n)} ${n === 1 ? "trial" : "trials"}`;
}

const textOf = (v) => (v == null || v === "" ? MISSING : String(v));

/** Group values of one matrix row (arrays: split, tags, `[*]` paths). */
export function groupsOfRow(row, key, ctx = {}, runId = null) {
  if (!key || key === "none") return ["All rows"];
  if (key === "row") return [row.label ?? `Row ${row.n}`];
  const gv = row.group_values ?? {};
  if (key === "source")
    return [textOf(gv.source ?? row.source?.kind ?? row.source)];
  if (key === "tag") {
    const tags = row.tags ?? gv.tags ?? [];
    return tags.length ? tags.map(String) : ["(no tag)"];
  }
  if (key === "dataset") return [ctx.datasetName ?? "This dataset"];
  // W5 B1: buildPivot hands the matrix's expression grouper in ctx.
  if (key.startsWith("expr:"))
    return ctx.exprGroups ? ctx.exprGroups(row) : [MISSING];
  // W5 B1: a custom column's values (the caller fetched them; per run for a
  // run column).
  if (key.startsWith("cc:"))
    return ctx.customGroups ? ctx.customGroups(row, runId) : [MISSING];
  if (key.startsWith("meta:")) {
    const k = key.slice(5);
    const v = gv[k] ?? row.values?.[k];
    if (k === "split")
      return v
        ? String(v)
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : ["(no split)"];
    return [textOf(v)];
  }
  if (key.startsWith("col:")) return [textOf(row.values?.[key.slice(4)])];
  if (key.startsWith("path:")) {
    const { column, path } = pathOfKey(key);
    const base = column ? row.values?.[column] : row.values;
    const parsed = parsePath(path.startsWith("[") ? `$${path}` : path);
    if (!parsed.ok) return [MISSING];
    const r = resolvePath(base, parsed.steps, { all: true });
    if (!r.found) return [MISSING];
    const vals = r.multi ? r.values ?? [] : [r.value];
    if (!vals.length) return [MISSING];
    return [
      ...new Set(
        vals.map((v) => (isPrimitive(v) ? textOf(v) : JSON.stringify(v))),
      ),
    ];
  }
  return [textOf(gv[key])];
}

// ------------------------------------------------------------- columns axis

export const COL_LABELS = {
  run: "Runs",
  harness: "Harness",
  model: "Model",
  prompt_version: "Prompt version",
  agent_version: "Agent version",
  run_kind: "Run kind",
  iteration: "Iteration",
  dataset_version: "Dataset version",
};
