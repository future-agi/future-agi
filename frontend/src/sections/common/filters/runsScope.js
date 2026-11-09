// the interface contract · the runs scope (the interface contract §5 A0 runs catalogue): expressions over
// canonical Run objects (CONTRACT "Canonical Run object"), for leaderboards
// and tags (Lane C, C2). `dataset_metadata.<key>` (the interface contract) is read from the
// run when present. Pure.
//
// ctx = { datasetNames{id: name}, metricValue(run, key) → {value, n} | null,
//         cost(run) → number|null, latencyP50(run) → number|null }
import { compileExpression, matchesExpression } from "./expression";
import { runsCatalog } from "./fieldCatalog";
import { configKeyOf } from "./configKey";
import { getRunStatus } from "src/sections/common/Runs/runStatus";

const personName = (p) =>
  p && typeof p === "object" ? p.name ?? p.id ?? null : p ?? null;

function readRunField(run, ctx, field) {
  const rd = field.read;
  switch (rd.k) {
    case "label":
      return run.label ?? null;
    case "name":
      return run.name ?? null;
    case "experiment":
      return run.experiment_name ?? null;
    case "kind":
      return run.target_type ?? run.kind ?? null;
    case "model":
      return run.target?.model ?? run.target?.model_name ?? run.model ?? null;
    case "status":
      // the capability contract: filter on the run's real outcome (the one derivation),
      // so a failed run stamped Completed never matches `Completed`.
      return run.status == null ? null : getRunStatus(run).key;
    case "created_at":
      return run.created_at ?? null;
    case "created_by":
      return personName(run.created_by);
    case "tags":
      return Array.isArray(run.tags)
        ? run.tags.map((t) => (typeof t === "object" ? t.name : t))
        : [];
    case "dataset":
      return ctx.datasetNames?.[run.dataset_id] ?? run.dataset_name ?? null;
    case "dataset_version":
      return run.dataset_version?.number ?? null;
    case "cost_usd":
      return ctx.cost ? ctx.cost(run) : null;
    case "latency_p50":
      return ctx.latencyP50 ? ctx.latencyP50(run) : null;
    case "is_baseline":
      return !!run.is_dataset_baseline;
    case "config_key":
      return configKeyOf(run);
    case "metadata":
      return run.metadata?.[rd.key] ?? null;
    case "metadata_path":
      return field.rest.reduce(
        (v, k) => (v == null ? null : v[k]),
        run.metadata ?? null,
      );
    case "dataset_metadata":
      return run.dataset_metadata?.[rd.key] ?? null;
    case "dataset_metadata_path":
      return field.rest.reduce(
        (v, k) => (v == null ? null : v[k]),
        run.dataset_metadata ?? null,
      );
    case "metric":
      return ctx.metricValue
        ? ctx.metricValue(run, rd.key)?.value ?? null
        : null;
    case "metric_n":
      return ctx.metricValue ? ctx.metricValue(run, rd.key)?.n ?? null : null;
    default:
      return null;
  }
}

/** The runs catalogue for the runs in a list (metadata keys read from them). */
export function runsCatalogFor(runs, { metricDefs = [] } = {}) {
  const keys = (pick) => [
    ...new Set((runs ?? []).flatMap((r) => Object.keys(pick(r) ?? {}))),
  ];
  return runsCatalog({
    metricDefs,
    metadataKeys: keys((r) => r.metadata),
    datasetMetadataKeys: keys((r) => r.dataset_metadata),
  });
}

/** The evaluation scope of one run. */
export function runRowScope(run, catalog, ctx = {}) {
  return {
    catalog,
    runIds: [],
    baselineId: null,
    readRow: (field) => readRunField(run, ctx, field),
    readRun: () => null,
  };
}

/** Runs matching an expression, or `{error}`. */
export function filterRuns(expr, runs, { metricDefs = [], ...ctx } = {}) {
  const catalog = runsCatalogFor(runs, { metricDefs });
  const compiled = compileExpression(expr, catalog);
  if (compiled.error) return { error: compiled.error };
  return {
    runs: (runs ?? []).filter((r) =>
      matchesExpression(compiled.ast, runRowScope(r, catalog, ctx)),
    ),
  };
}
