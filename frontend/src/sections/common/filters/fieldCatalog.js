// W5-1 · field catalogues (W5-SCOPE §5 A0): one name list per scope, shared
// by the validator, the evaluator, autocomplete and the help. Pure.
//
// A catalogue is
//   { kind: "results" | "grid" | "runs", fields: Field[], prefixes: Prefix[],
//     runs: [{id, label}], baselineId, custom: CustomDef[] }
// Field  = { name, scope: "row" | "run", type, read, values?, eval?, doc? }
// Prefix = { prefix: "output.", scope, type, read }   (open paths)
// `type`: text · number · boolean · list · json · eval · any. `read` is a
// descriptor the scope module interprets (resultsScope / gridScope /
// runsScope); the catalogue never reads data itself.

/** `Tool-call accuracy` → `tool_call_accuracy` (the eval's field name). */
import {
  PREFERENCE_FIELDS,
  VERDICTS,
  columnType,
  evalMeta,
  fieldName,
  rowFieldsFor,
  runFieldsFor,
  withCustom,
  withRowFields,
} from "./catalogFields";

export {
  MOVEMENTS,
  RUN_STATUSES,
  VERDICTS,
  evalMeta,
  evalThreshold,
  fieldName,
  quoteName,
  quoteSegment,
} from "./catalogFields";
export function resultsCatalog({
  runs = [],
  baselineId = null,
  metricDefs = [],
  inputColumns = [],
  expectedColumns = [],
  metadataKeys = [],
  humanLabels = [],
  decisionKeys = [],
  reasons = false,
  customColumns = [],
  rowFields = [],
  hasStatus = false,
} = {}) {
  const fields = withCustom(
    withRowFields(
      [
        ...rowFieldsFor({
          inputColumns,
          expectedColumns,
          metadataKeys,
          hasStatus,
        }),
        ...(runs.length >= 2 ? PREFERENCE_FIELDS : []),
        ...runFieldsFor({ metricDefs, humanLabels, decisionKeys, reasons }),
      ],
      rowFields,
    ),
    customColumns,
  );
  return {
    kind: "results",
    fields,
    prefixes: [
      {
        prefix: "output.",
        scope: "run",
        type: "any",
        read: { k: "output_path" },
      },
    ],
    runs: runs.map((r) => ({
      id: r.run_id ?? r.id,
      label: r.label ?? r.run_id ?? r.id,
    })),
    baselineId,
    custom: customColumns,
  };
}

/** The grid catalogue: the same row fields over dataset rows (every column). */
export function gridCatalog({
  columns = [],
  rowFields = [],
  customColumns = [],
  hasStatus = false,
} = {}) {
  const live = columns.filter((c) => !c.deleted);
  const byRole = (role) => live.filter((c) => c.role === role);
  const base = rowFieldsFor({
    inputColumns: byRole("input"),
    expectedColumns: byRole("expected"),
    metadataKeys: byRole("metadata").map((c) => c.name),
    hasStatus,
  }).map((f) =>
    f.read.k === "metadata"
      ? {
          ...f,
          read: {
            k: "metadata",
            key: f.read.key,
            id: live.find((c) => c.name === f.read.key)?.id,
          },
        }
      : f,
  );
  const named = new Set(base.map((f) => f.name));
  live.forEach((c) => {
    if (named.has(c.name)) return;
    const isEval = c.role === "eval";
    const meta = isEval
      ? evalMeta({
          key: c.generated?.metric_key ?? c.id,
          name: c.name,
          output_type: c.data_type === "boolean" ? "pass_fail" : "score",
          pass_threshold: c.generated?.threshold ?? null,
        })
      : undefined;
    base.push({
      name: c.name,
      scope: "row",
      type: isEval ? "eval" : columnType(c.data_type),
      read: { k: "column", id: c.id, col: c.name },
      eval: meta,
      doc: c.role
        ? `${c.role[0].toUpperCase()}${c.role.slice(1)} column`
        : "Column",
    });
    if (isEval)
      base.push({
        name: `${c.name}.verdict`,
        scope: "row",
        type: "text",
        values: VERDICTS,
        read: { k: "column_verdict", id: c.id },
        eval: meta,
        doc: `${c.name} verdict`,
      });
  });
  return {
    kind: "grid",
    fields: withCustom(
      withRowFields(base, rowFields),
      customColumns.filter((d) => d.applies_to === "row"),
    ),
    prefixes: [],
    runs: [],
    baselineId: null,
    custom: customColumns,
  };
}

/** The runs catalogue (leaderboards, tags): one row per run. */
export function runsCatalog({
  metricDefs = [],
  metadataKeys = [],
  datasetMetadataKeys = [],
} = {}) {
  const f = (name, type, read, doc, values) => ({
    name,
    scope: "row",
    type,
    read,
    doc,
    values,
  });
  const fields = [
    f("label", "text", { k: "label" }, "Run label (#2B)"),
    f("name", "text", { k: "name" }, "Run name"),
    f("experiment", "text", { k: "experiment" }, "Experiment name"),
    f("kind", "text", { k: "kind" }, "What ran", [
      "prompt",
      "agent",
      "code",
      "harness",
      "voice",
      "external",
      "http",
    ]),
    f("model", "text", { k: "model" }, "Model"),
    f("status", "text", { k: "status" }, "Run status"),
    f("created_at", "text", { k: "created_at" }, "Created (ISO time)"),
    f("created_by", "text", { k: "created_by" }, "Created by"),
    f("tags", "list", { k: "tags" }, "Run tags"),
    f("dataset", "text", { k: "dataset" }, "Dataset name"),
    f("dataset_version", "number", { k: "dataset_version" }, "Dataset version"),
    f("cost_usd", "number", { k: "cost_usd" }, "Mean cost per row (USD)"),
    f("latency_p50", "number", { k: "latency_p50" }, "Median latency (ms)"),
    f("is_baseline", "boolean", { k: "is_baseline" }, "The dataset's baseline"),
    // HO R8: the run's setup (kind | target | model), By dataset's one key.
    f(
      "config_key",
      "text",
      { k: "config_key" },
      "The run's setup: kind | target | model (By dataset's key)",
    ),
  ];
  metadataKeys.forEach((key) =>
    fields.push(
      f(`metadata.${key}`, "any", { k: "metadata", key }, "Run metadata"),
    ),
  );
  datasetMetadataKeys.forEach((key) =>
    fields.push(
      f(
        `dataset_metadata.${key}`,
        "any",
        { k: "dataset_metadata", key },
        "Dataset metadata",
      ),
    ),
  );
  metricDefs.forEach((def) => {
    const n = fieldName(def.name ?? def.key);
    fields.push({
      ...f(
        n,
        "number",
        { k: "metric", key: def.key },
        `${def.name ?? def.key} (the run's aggregate)`,
      ),
      eval: evalMeta(def),
    });
    fields.push(
      f(
        `${n}.n`,
        "number",
        { k: "metric_n", key: def.key },
        `${def.name ?? def.key} rows scored`,
      ),
    );
  });
  return {
    kind: "runs",
    fields,
    prefixes: [
      {
        prefix: "metadata.",
        scope: "row",
        type: "any",
        read: { k: "metadata_path" },
      },
      {
        prefix: "dataset_metadata.",
        scope: "row",
        type: "any",
        read: { k: "dataset_metadata_path" },
      },
    ],
    runs: [],
    baselineId: null,
    custom: [],
  };
}

// ---------------------------------------------------------------- lookup
const INDEX = new WeakMap();
function indexOf(catalog) {
  let idx = INDEX.get(catalog);
  if (idx) return idx;
  idx = { row: new Map(), run: new Map(), rowCi: new Map(), runCi: new Map() };
  catalog.fields.forEach((fld) => {
    const exact = fld.scope === "run" ? idx.run : idx.row;
    const ci = fld.scope === "run" ? idx.runCi : idx.rowCi;
    if (!exact.has(fld.name)) exact.set(fld.name, fld);
    const lower = fld.name.toLowerCase();
    if (!ci.has(lower)) ci.set(lower, fld);
  });
  INDEX.set(catalog, idx);
  return idx;
}

/**
 * The field a path names, or null. `wantRun`: the path follows a run ref,
 * so per-run fields are tried first.
 * @returns {object|null} the Field, or for an open prefix `{...prefix, name, rest[]}`
 */
export function lookupField(catalog, path, { wantRun = false } = {}) {
  const name = path.join(".");
  const idx = indexOf(catalog);
  const order = wantRun ? ["run", "row"] : ["row", "run"];
  for (const s of order) {
    const hit = idx[s].get(name) ?? idx[`${s}Ci`].get(name.toLowerCase());
    if (hit) return hit;
  }
  for (const p of catalog.prefixes ?? []) {
    const root = p.prefix.slice(0, -1).split(".");
    if (
      path.length > root.length &&
      root.every(
        (seg, i) => seg.toLowerCase() === String(path[i]).toLowerCase(),
      )
    )
      return { ...p, name, rest: path.slice(root.length) };
  }
  return null;
}

/** Every name, catalogue order (did-you-mean, autocomplete). */
export const catalogNames = (catalog) => [
  ...new Set(catalog.fields.map((f) => f.name)),
];

/**
 * A run ref → `{id}` (label, attempt label or id, case-insensitive), or
 * `{quant: "any"|"all"}`, or `{error}`.
 */
export function resolveRunRef(catalog, run) {
  if (!run) return { quant: "default" };
  if (run.kind === "any" || run.kind === "all") return { quant: run.kind };
  const runs = catalog.runs ?? [];
  const inView = runs.map((r) => r.label).join(", ");
  if (!runs.length) return { error: "Runs are not part of this view." };
  if (run.kind === "baseline") {
    if (!catalog.baselineId || !runs.some((r) => r.id === catalog.baselineId))
      return { error: `No baseline in view. Runs in view: ${inView}.` };
    return { id: catalog.baselineId };
  }
  const ref = run.kind === "label" ? run.label : run.ref;
  const lower = String(ref).toLowerCase();
  const hit = runs.find(
    (r) => r.id === ref || String(r.label).toLowerCase() === lower,
  );
  if (hit) return { id: hit.id };
  const shown = run.kind === "label" ? ref : `run(${JSON.stringify(ref)})`;
  return { error: `Unknown run “${shown}”. Runs in view: ${inView}.` };
}
