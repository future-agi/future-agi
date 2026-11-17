// W5-1 · the field entries the catalogues are built from (W5-SCOPE §5 A0):
// row fields, per-run fields, custom columns. fieldCatalog.js assembles them
// per scope and looks names up. Pure.
export const fieldName = (name) =>
  String(name ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

// Names that can be written bare (else they need `back quotes`).
const BARE = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const quoteSegment = (seg) => (BARE.test(seg) ? seg : `\`${seg}\``);
export const quoteName = (name) =>
  String(name).split(".").map(quoteSegment).join(".");

export const columnType = (dataType) =>
  ({
    text: "text",
    string: "text",
    integer: "number",
    float: "number",
    number: "number",
    boolean: "boolean",
    json: "json",
    array: "list",
    datetime: "text",
  })[String(dataType ?? "").toLowerCase()] ?? "any";

export const RUN_STATUSES = [
  "completed",
  "error",
  "running",
  "queued",
  "missing",
];
export const MOVEMENTS = ["improved", "regressed", "unchanged"];
export const VERDICTS = ["pass", "fail", "error", "na"];

/** The value a score passes at: the eval's own threshold, 0.5 for pass/fail. */
export const evalThreshold = (def) =>
  def?.output_type === "pass_fail" || def?.output_type === "thumbs"
    ? 0.5
    : def?.pass_threshold ?? null;

/** Eval meta carried on eval fields (for verdict words and the help). */
export const evalMeta = (def) => ({
  key: def.key,
  name: def.name ?? def.key,
  outputType: def.output_type ?? null,
  threshold: evalThreshold(def),
  higherIsBetter: def.higher_is_better ?? null,
});

export function rowFieldsFor({
  inputColumns = [],
  expectedColumns = [],
  metadataKeys = [],
  rowFields = [],
  hasStatus = false,
}) {
  const out = [];
  inputColumns.forEach((c) => {
    out.push({
      name: c.name,
      scope: "row",
      type: columnType(c.data_type),
      read: { k: "input", col: c.name, id: c.id },
      doc: "Input column",
    });
  });
  inputColumns.forEach((c) => {
    out.push({
      name: `input.${c.name}`,
      scope: "row",
      type: columnType(c.data_type),
      read: { k: "input", col: c.name, id: c.id },
      doc: "Input column",
    });
  });
  if (expectedColumns.length) {
    const first = expectedColumns[0];
    out.push({
      name: "expected",
      scope: "row",
      type: columnType(first.data_type),
      read: { k: "expected", col: first.name, id: first.id },
      doc: `Expected (${first.name})`,
    });
    expectedColumns.forEach((c) => {
      out.push({
        name: `expected.${c.name}`,
        scope: "row",
        type: columnType(c.data_type),
        read: { k: "expected", col: c.name, id: c.id },
        doc: "Expected column",
      });
      if (!inputColumns.some((i) => i.name === c.name))
        out.push({
          name: c.name,
          scope: "row",
          type: columnType(c.data_type),
          read: { k: "expected", col: c.name, id: c.id },
          doc: "Expected column",
        });
    });
  }
  metadataKeys.forEach((m) => {
    const key = typeof m === "string" ? m : m.key;
    out.push({
      name: `metadata.${key}`,
      scope: "row",
      type: "text",
      read: { k: "metadata", key },
      values: typeof m === "string" ? undefined : m.values,
      doc: "Metadata",
    });
  });
  out.push({
    name: "split",
    scope: "row",
    type: "list",
    read: { k: "split" },
    doc: "Splits the row is in",
  });
  out.push({
    name: "tags",
    scope: "row",
    type: "list",
    read: { k: "tags" },
    doc: "Row tags",
  });
  out.push({
    name: "row",
    scope: "row",
    type: "number",
    read: { k: "row" },
    doc: "Row number",
  });
  out.push({
    name: "id",
    scope: "row",
    type: "text",
    read: { k: "id" },
    doc: "Row id",
  });
  if (hasStatus)
    out.push({
      name: "status",
      scope: "row",
      type: "text",
      read: { k: "row_status" },
      doc: "Row status",
    });
  return withRowFields(out, rowFields);
}

/**
 * Append registered row fields (gridScope.registerRowField) after `fields`.
 * A dataset's own column, metadata key, eval or field keeps its name: a
 * provider whose name is taken is not added, in the grid and in results
 * alike (it stays reachable on datasets that have no such column).
 */
export function withRowFields(fields, rowFields = []) {
  const taken = new Set(fields.map((f) => f.name));
  fields.forEach((f) => {
    if (f.read?.k === "metadata" && f.read.key) taken.add(f.read.key);
  });
  rowFields.forEach((f) => {
    if (taken.has(f.name)) return;
    taken.add(f.name);
    fields.push({
      scope: "row",
      doc: "Row field",
      ...f,
      read: { k: "provider", name: f.name },
    });
  });
  return fields;
}

export function runFieldsFor({
  metricDefs = [],
  humanLabels = [],
  decisionKeys = [],
  reasons = false,
}) {
  const out = [
    {
      name: "output",
      scope: "run",
      type: "any",
      read: { k: "output" },
      doc: "The run's output",
    },
  ];
  metricDefs.forEach((def) => {
    const n = fieldName(def.name ?? def.key);
    const meta = evalMeta(def);
    out.push({
      name: n,
      scope: "run",
      type: "eval",
      eval: meta,
      read: { k: "eval", key: def.key, part: "value" },
      doc: `${meta.name} (score; pass / fail by its threshold)`,
    });
    out.push({
      name: `${n}.verdict`,
      scope: "run",
      type: "text",
      values: VERDICTS,
      eval: meta,
      read: { k: "eval", key: def.key, part: "verdict" },
      doc: `${meta.name} verdict`,
    });
    out.push({
      name: `${n}.movement`,
      scope: "run",
      type: "text",
      values: MOVEMENTS,
      eval: meta,
      read: { k: "eval", key: def.key, part: "movement" },
      doc: `${meta.name} vs the baseline`,
    });
    out.push({
      name: `${n}.delta`,
      scope: "run",
      type: "number",
      eval: meta,
      read: { k: "eval", key: def.key, part: "delta" },
      doc: `${meta.name} minus the baseline's`,
    });
    if (reasons)
      out.push({
        name: `${n}.reason`,
        scope: "run",
        type: "text",
        eval: meta,
        read: { k: "eval", key: def.key, part: "reason" },
        doc: `${meta.name} judge reason`,
      });
    if (decisionKeys.includes(def.key)) {
      out.push({
        name: `${n}.p_yes`,
        scope: "run",
        type: "number",
        eval: meta,
        read: { k: "eval", key: def.key, part: "p_yes" },
        doc: `${meta.name} P(yes)`,
      });
      out.push({
        name: `${n}.confidence`,
        scope: "run",
        type: "number",
        eval: meta,
        read: { k: "eval", key: def.key, part: "confidence" },
        doc: `${meta.name} confidence`,
      });
    }
  });
  out.push(
    {
      name: "status",
      scope: "run",
      type: "text",
      values: RUN_STATUSES,
      read: { k: "status" },
      doc: "The run's status on the row",
    },
    {
      name: "error",
      scope: "run",
      type: "text",
      read: { k: "error" },
      doc: "Error message",
    },
    {
      name: "latency_ms",
      scope: "run",
      type: "number",
      read: { k: "latency_ms" },
      doc: "Latency (ms)",
    },
    {
      name: "cost_usd",
      scope: "run",
      type: "number",
      read: { k: "cost_usd" },
      doc: "Cost (USD)",
    },
    {
      name: "tokens",
      scope: "run",
      type: "number",
      read: { k: "tokens" },
      doc: "Tokens",
    },
    {
      name: "steps",
      scope: "run",
      type: "number",
      read: { k: "steps" },
      doc: "Agent steps",
    },
    {
      name: "tool_calls",
      scope: "run",
      type: "number",
      read: { k: "tool_calls" },
      doc: "Tool calls",
    },
    {
      name: "tool_errors",
      scope: "run",
      type: "number",
      read: { k: "tool_errors" },
      doc: "Tool errors",
    },
    {
      name: "trials",
      scope: "run",
      type: "number",
      read: { k: "trials" },
      doc: "Trials scored",
    },
    {
      name: "flaky",
      scope: "run",
      type: "boolean",
      read: { k: "flaky" },
      doc: "Trials disagree",
    },
  );
  humanLabels.forEach((l) => {
    const name = typeof l === "string" ? l : l.name ?? l.key;
    const id = typeof l === "string" ? l : l.id ?? l.key ?? name;
    out.push({
      name: `human.${fieldName(name)}`,
      scope: "run",
      type: "any",
      read: { k: "human", id },
      doc: `Human label ${name}`,
    });
  });
  return out;
}

export const customField = (def) => ({
  name: `col.${def.name}`,
  scope: def.applies_to === "row" ? "row" : "run",
  type: def.type ?? "any",
  read: { k: "custom", id: def.id, name: def.name },
  custom: def,
  doc: def.kind === "path" ? `Column from ${def.path}` : `Column = ${def.expr}`,
});

/** Add the custom columns: `col.<name>`, plus the bare name when it is free. */
export function withCustom(fields, custom = []) {
  const out = [...fields];
  const taken = new Set(fields.map((f) => f.name.toLowerCase()));
  custom.forEach((def) => {
    const f = customField(def);
    out.push(f);
    if (!taken.has(def.name.toLowerCase())) out.push({ ...f, name: def.name });
  });
  return out;
}

/** The results catalogue: row fields + per-run fields over R3 rows. */
// Pairwise preference on the row (the first candidate vs the baseline).
export const PREFERENCE_FIELDS = [
  {
    name: "preference",
    scope: "row",
    type: "text",
    read: { k: "preference", part: "choice" },
    doc: "Preferred run (#2B) or tie",
  },
  {
    name: "preference.source",
    scope: "row",
    type: "text",
    values: ["judge", "human"],
    read: { k: "preference", part: "source" },
    doc: "Who picked it",
  },
  {
    name: "preference.disagree",
    scope: "row",
    type: "boolean",
    read: { k: "preference", part: "disagree" },
    doc: "LLM judge disagrees with reviewers",
  },
];
