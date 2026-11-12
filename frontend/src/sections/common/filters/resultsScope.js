// W5-1 A1 · the results scope (W5-SCOPE §5 A1): expressions over one R3
// results row (`resultsRows` entry: example_id, row_order, splits[],
// metadata{}, values_latest{colId}, values_base{colId}, runs{runId: {status,
// output, values{key}, trials, flaky, movement{key}, delta{key}, human{},
// latency_ms, cost, tokens, steps{}, error}}). Pure FE module; the mock's
// applyPills calls `expressionMatchesRow` (seam S3) and the R3 builder calls
// `expressionPillsError` (seam S6).
//
// ctx (the applyPills ctx): { runIds, baselineId, metricDefs{key: def},
//   runLabels{id: label}, splitNames{id: name}, columns[] (dataset columns
//   with role), customColumns[], rowFields[] (providers), metadataKeys[] }
import { resolvePath } from "src/sections/common/Values/faPath";
import { compileExpression, matchesExpression } from "./expression";
import { resultsCatalog } from "./fieldCatalog";
import { providerFields, providersVersion, readProvider } from "./gridScope";
import {
  failsByTries,
  failsCell,
} from "src/sections/experiment-detail/iterate/failedRule";

const byRole = (columns, role) =>
  (columns ?? []).filter((c) => !c.deleted && c.role === role);

/** Metadata keys with a value on any row (seam S4 stores them on the ctx). */
export const metadataKeysOf = (rows) => [
  ...new Set(
    (rows ?? []).flatMap((r) =>
      Object.entries(r.metadata ?? {})
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k]) => k),
    ),
  ),
];

/**
 * The names an expression can use that only the rows carry (seam S4/S5:
 * `Object.assign(ctx, expressionNamesOf(rows, labelName))`): metadata keys
 * and human labels.
 */
export function expressionNamesOf(rows, labelName = () => null) {
  const labels = [
    ...new Set(
      (rows ?? []).flatMap((r) =>
        Object.values(r.runs ?? {}).flatMap((x) => Object.keys(x?.human ?? {})),
      ),
    ),
  ];
  return {
    metadataKeys: metadataKeysOf(rows),
    humanLabels: labels.map((id) => ({ id, name: labelName(id) ?? id })),
  };
}

const CATALOGS = new WeakMap();

/** The results catalogue for a ctx (cached per ctx object). */
export function resultsCatalogFor(ctx = {}) {
  const hit = CATALOGS.get(ctx);
  if (hit && hit.version === providersVersion()) return hit.catalog;
  const runIds = ctx.runIds ?? [];
  const metadataKeys = [
    ...new Set([
      ...(ctx.metadataKeys ?? []),
      ...byRole(ctx.columns, "metadata").map((c) => c.name),
    ]),
  ];
  const catalog = resultsCatalog({
    runs: runIds.map((id) => ({
      run_id: id,
      label: ctx.runLabels?.[id] ?? id,
    })),
    baselineId: ctx.baselineId ?? null,
    metricDefs: Object.values(ctx.metricDefs ?? {}).filter(
      (d) =>
        d &&
        d.kind !== "ops" &&
        !String(d.key).startsWith("ops:") &&
        !String(d.key).startsWith("agent:"),
    ),
    inputColumns: byRole(ctx.columns, "input"),
    expectedColumns: byRole(ctx.columns, "expected"),
    metadataKeys,
    humanLabels: ctx.humanLabels ?? [],
    decisionKeys: ctx.decisionKeys ?? [],
    reasons: !!ctx.hasReasons,
    customColumns: ctx.customColumns ?? [],
    rowFields: [...resultsProviderFields(), ...(ctx.rowFields ?? [])],
  });
  CATALOGS.set(ctx, { catalog, version: providersVersion() });
  return catalog;
}

/**
 * L7c g1 (expr:w4-fields): row fields a results expression offers. The
 * `language` provider reads a dataset row's input text, which a results row
 * does not carry, so it never matched there; it stays on the Data grid only.
 */
export const RESULTS_SKIPPED_ROW_FIELDS = Object.freeze(["language"]);
export const resultsProviderFields = () =>
  providerFields().filter((f) => !RESULTS_SKIPPED_ROW_FIELDS.includes(f.name));

const asList = (v) => {
  if (Array.isArray(v)) return v;
  if (v == null || v === "") return [];
  if (typeof v === "string")
    return v
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  return [v];
};

function metadataOf(row, key, ctx) {
  const v = row.metadata?.[key];
  if (v !== undefined && v !== null) return v;
  // Results metadata is built from four GS fields (api/review.js); every
  // other dataset reads its metadata-role columns (W5-SCOPE finding 3).
  const col = byRole(ctx.columns, "metadata").find((c) => c.name === key);
  // As inputs: the version the runs ran on first, then the latest.
  return col
    ? row.values_base?.[col.id] ?? row.values_latest?.[col.id] ?? null
    : null;
}

function verdictOf(r, meta) {
  if (!r || r.status === "missing") return null;
  // M7: a per-try pass / fail eval reads the one Failed rule
  // (failedRule.failsCell = results-math failsRow): a row with a failed or
  // errored try is `fail`, so `<eval> = fail` selects exactly the rows of
  // the `Fails <eval>` pill, the dock and the Matrix.
  const def = { output_type: meta.outputType };
  if (failsByTries(def) && failsCell(r, meta.key, def)) return "fail";
  const v = r.values?.[meta.key];
  if (typeof v === "number" && Number.isFinite(v)) {
    if (meta.threshold == null) return null;
    return v >= meta.threshold ? "pass" : "fail";
  }
  if (r.status === "error") return "error";
  if (r.status === "completed") return "na";
  return null;
}

/** Read one row field for a results row. */
function readRow(row, ctx, field) {
  const rd = field.read;
  switch (rd.k) {
    case "input":
      return row.values_base?.[rd.id] ?? row.values_latest?.[rd.id] ?? null;
    case "expected":
      return row.values_base?.[rd.id] ?? null;
    case "metadata":
      return metadataOf(row, rd.key, ctx);
    case "split":
      return (row.splits ?? []).map((id) => ctx.splitNames?.[id] ?? id);
    case "tags":
      return asList(
        row.tags ?? row.metadata?.tags ?? metadataOf(row, "tags", ctx),
      );
    case "row":
      return row.row_order ?? null;
    case "id":
      return row.example_id ?? null;
    case "row_status":
      return row.status ?? null;
    case "provider":
      return readProvider(rd.name, {
        rowId: row.example_id,
        datasetId: ctx.datasetId,
        row,
      });
    case "preference": {
      const p = row.preference;
      if (!p) return null;
      if (rd.part === "source") return p.source ?? null;
      if (rd.part === "disagree")
        return !!(
          p.judge &&
          p.human_choice &&
          p.judge.choice !== p.human_choice
        );
      const c = p.choice_run;
      return c == null ? null : c === "tie" ? "tie" : ctx.runLabels?.[c] ?? c;
    }
    default:
      return null;
  }
}

/** Read one per-run field of a results row. */
function readRun(row, runId, field) {
  const r = row.runs?.[runId];
  const rd = field.read;
  if (rd.k === "status") return r?.status ?? "missing";
  if (!r || r.status === "missing") return null;
  switch (rd.k) {
    case "output":
      return r.output ?? null;
    case "output_path": {
      const hit = resolvePath(
        r.output,
        field.rest.map((seg) =>
          /^\d+$/.test(seg)
            ? { type: "index", index: Number(seg) }
            : { type: "key", key: seg },
        ),
      );
      return hit.found ? (hit.multi ? hit.values : hit.value) : null;
    }
    case "eval":
      switch (rd.part) {
        case "value":
          return r.values?.[rd.key] ?? null;
        case "verdict":
          return verdictOf(r, field.eval);
        case "movement":
          return r.movement?.[rd.key] ?? null;
        case "delta":
          return r.delta?.[rd.key] ?? null;
        case "reason":
          return r.reasons?.[rd.key] ?? null;
        case "p_yes":
        case "confidence":
          return r.decision?.[rd.key]?.[rd.part] ?? null;
        default:
          return null;
      }
    case "error":
      return r.error ?? null;
    case "latency_ms":
      return r.latency_ms ?? null;
    case "cost_usd":
      return r.cost ?? null;
    case "tokens":
      return r.tokens ?? null;
    case "steps":
      return r.steps?.steps ?? r.values?.["agent:steps"] ?? null;
    case "tool_calls":
      return r.steps?.tool_calls ?? null;
    case "tool_errors":
      return r.steps?.tool_errors ?? null;
    case "trials":
      return r.trials ?? null;
    case "flaky":
      return !!r.flaky;
    case "human":
      return r.human?.[rd.id] ?? null;
    default:
      return null;
  }
}

/** The evaluation scope of one results row. */
export function resultsRowScope(row, ctx = {}) {
  return {
    catalog: resultsCatalogFor(ctx),
    runIds: ctx.runIds ?? [],
    baselineId: ctx.baselineId ?? null,
    readRow: (field) => readRow(row, ctx, field),
    readRun: (runId, field) => readRun(row, runId, field),
  };
}

// Compiled pills, by text, per catalogue (parse once per pill).
const COMPILED = new WeakMap();
function compiledFor(expr, ctx) {
  const catalog = resultsCatalogFor(ctx);
  let byText = COMPILED.get(catalog);
  if (!byText) {
    byText = new Map();
    COMPILED.set(catalog, byText);
  }
  if (!byText.has(expr)) byText.set(expr, compileExpression(expr, catalog));
  return byText.get(expr);
}

/** applyPills case "expression" (seam S3): does the row match? */
export function expressionMatchesRow(pill, row, ctx = {}) {
  const compiled = compiledFor(String(pill?.expr ?? ""), ctx);
  if (compiled.error) return false;
  return matchesExpression(compiled.ast, resultsRowScope(row, ctx));
}

/**
 * The first expression pill's error message, or null (seam S6: R3 answers
 * 400 with it). Pass the applyPills ctx itself (`queryResults(...).ctx`),
 * so validation sees the same names as evaluation; or
 * `{runs: run records, metricOf(key), columns, baselineId}` to build one.
 */
export function expressionPillsError(pills, ctxOrOpts = {}) {
  const exprPills = (pills ?? []).filter((p) => p?.type === "expression");
  if (!exprPills.length) return null;
  let ctx = ctxOrOpts;
  if (!Array.isArray(ctxOrOpts.runIds)) {
    const { runs = [], metricOf, ...rest } = ctxOrOpts;
    const live = runs.filter(Boolean);
    const keys = [...new Set(live.flatMap((r) => r.metric_keys ?? []))];
    ctx = {
      runIds: live.map((r) => r.id ?? r.run_id),
      runLabels: Object.fromEntries(
        live.map((r) => [r.id ?? r.run_id, r.label ?? r.id ?? r.run_id]),
      ),
      metricDefs: Object.fromEntries(
        keys
          .map((k) => [k, metricOf ? metricOf(k) : null])
          .filter(([, d]) => d),
      ),
      ...rest,
    };
  }
  for (const pill of exprPills) {
    const compiled = compiledFor(String(pill.expr ?? ""), ctx);
    if (compiled.error) return compiled.error.message;
  }
  return null;
}
