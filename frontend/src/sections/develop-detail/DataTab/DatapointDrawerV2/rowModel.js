// Pure model of one dataset row for the Row view (PRODUCT-SPEC §3.4):
// which column goes in which section, the evals under each generated
// output, eval verdicts and the output footer. No React.
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import { parseJsonString } from "src/sections/common/ValueViewer/valueKind";
import { fmtTimeAbsolute } from "src/sections/common/format/fmt";
import { prettyJson } from "src/sections/common/Values/jsonLines";
import { isSurfaceShown } from "src/launch/launchSwitch";

const USER = "OTHERS";
const RUN_PROMPT = "run_prompt";
// L5 G4: every dynamic column type is a generated output of the row.
const GENERATED_ORIGINS = new Set([
  RUN_PROMPT,
  "vector_db",
  "extracted_entities",
  "extracted_json",
  "classification",
  "api_call",
  "conditional",
  "python_code",
]);
const EVAL = "evaluation";
const EVAL_REASON = "evaluation_reason";
const HUMAN = "annotation_label";
const PASS = 0.5;

/** Row number shown to people (`order` is 0-based). */
export const rowNumberOf = (row) =>
  Number.isFinite(row?.order) ? row.order + 1 : null;

/** Short id for the ID chip: the last 8 hex digits of the uuid. */
export const shortId = (id) =>
  String(id ?? "")
    .replace(/-/g, "")
    .slice(-8);

export const isUserColumn = (c) =>
  (c?.origin_type ?? c?.originType) === USER ||
  (!c?.origin_type && !c?.originType);

export const cellOf = (row, columnId) => row?.[columnId] ?? null;

/** The cell value as text (JSON stays a string). */
export const cellValue = (row, columnId) => {
  const v = cellOf(row, columnId)?.cell_value;
  return v == null ? "" : v;
};

/** Eval value as a number: Passed/Failed -> 1/0, "0.41" -> 0.41. */
export function evalNumber(raw) {
  if (raw == null || raw === "") return null;
  if (typeof raw === "number") return raw;
  if (typeof raw === "boolean") return raw ? 1 : 0;
  const s = String(raw).trim().toLowerCase();
  if (["passed", "pass", "true", "yes"].includes(s)) return 1;
  if (["failed", "fail", "false", "no"].includes(s)) return 0;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/**
 * Verdict of one eval cell (D4: success when good, error when bad).
 * @returns {{status: "pass"|"fail"|"neutral"|"error"|"running"|"empty",
 *   value: number|null, display: string}}
 */
export function evalVerdict(cell, { kind, higherIsBetter = true, threshold }) {
  if (!cell) return { status: "empty", value: null, display: "Not scored" };
  if (cell.status === "running")
    return { status: "running", value: null, display: "Running…" };
  if (cell.status === "error")
    return { status: "error", value: null, display: "Error" };
  const value = evalNumber(cell.cell_value);
  if (value == null) return { status: "empty", value, display: "Not scored" };
  if (kind === "pass_fail") {
    const good = higherIsBetter ? value >= PASS : value < PASS;
    return {
      status: good ? "pass" : "fail",
      value,
      display: value >= PASS ? "Pass" : "Fail",
    };
  }
  const display = Math.abs(value) <= 1 ? value.toFixed(2) : String(value);
  if (!Number.isFinite(threshold)) return { status: "neutral", value, display };
  const good = higherIsBetter ? value >= threshold : value <= threshold;
  return { status: good ? "pass" : "fail", value, display };
}

export const OPS_DEFS = {
  latency: { key: "ops:latency", output_type: "ms" },
  tokens: { key: "ops:tokens", output_type: "count" },
  cost: { key: "ops:cost", output_type: "usd" },
};

/** `793 ms · 341 tok · $0.000075` parts from a generated cell. */
export function outputFooter(cell) {
  const vi = cell?.value_infos ?? {};
  const meta = vi.metadata ?? {};
  const ms =
    meta.response_time ?? vi.runtime ?? cell?.metadata?.response_time_ms;
  const tokens = meta.usage?.total_tokens ?? cell?.metadata?.token_count;
  const cost = meta.cost?.total_cost;
  const parts = [];
  if (Number.isFinite(ms)) parts.push(formatValue(OPS_DEFS.latency, ms));
  if (Number.isFinite(tokens))
    parts.push(`${formatValue(OPS_DEFS.tokens, tokens)} tok`);
  if (Number.isFinite(cost)) parts.push(formatValue(OPS_DEFS.cost, cost));
  return parts;
}

const evalKind = (column, meta) => {
  const t = meta?.output_type;
  if (t === "pass_fail" || column.data_type === "boolean") return "pass_fail";
  if (t === "choices") return "choices";
  return "score";
};

/**
 * Group a row's columns into the Row view sections.
 * @param {object[]} columns  get-dataset-table `column_config`
 * @param {object} row        table row (cells keyed by column id)
 * @param {object[]} [evals]  get_evals_list items (mapping, thresholds)
 */
export function buildRowModel(columns = [], row = null, evals = []) {
  const evalById = new Map(evals.map((e) => [e.id ?? e.user_eval_id, e]));
  const reasonByEval = new Map(
    columns
      .filter((c) => c.origin_type === EVAL_REASON)
      .map((c) => [c.source_id, c.id]),
  );
  const outputs = [];
  const sections = {
    input: [],
    expected: [],
    metadata: [],
    noRole: [],
    evals: [],
    human: [],
  };

  columns.forEach((c) => {
    if (c.origin_type === EVAL_REASON) return;
    if (GENERATED_ORIGINS.has(c.origin_type)) {
      outputs.push({ column: c, cell: cellOf(row, c.id), evals: [] });
    } else if (c.origin_type === HUMAN) {
      sections.human.push({ column: c, cell: cellOf(row, c.id) });
    } else if (c.origin_type === EVAL) {
      const meta = evalById.get(c.source_id) ?? null;
      const cell = cellOf(row, c.id);
      const reasonCol = reasonByEval.get(c.source_id);
      const reason =
        cell?.value_infos?.reason ??
        (reasonCol ? cellOf(row, reasonCol)?.cell_value : null) ??
        null;
      const kind = evalKind(c, meta);
      sections.evals.push({
        column: c,
        cell,
        meta,
        kind,
        reason,
        verdict: evalVerdict(cell, {
          kind,
          higherIsBetter: meta?.higher_is_better ?? true,
          threshold: meta?.pass_threshold,
        }),
        tiedTo: [
          ...Object.values(meta?.mapping ?? meta?.config?.mapping ?? {}),
          ...(c.metadata?.run_prompt_id ?? []),
        ],
      });
    } else if (isUserColumn(c)) {
      const key = ["input", "expected", "metadata"].includes(c.role)
        ? c.role
        : "noRole";
      sections[key].push({ column: c, cell: cellOf(row, c.id) });
    }
  });

  // Evals judged on a generated output sit under that output's card.
  const loose = [];
  sections.evals.forEach((ev) => {
    const out = outputs.find((o) => ev.tiedTo.includes(o.column.id));
    if (out) out.evals.push(ev);
    else loose.push(ev);
  });
  sections.evals = loose;
  return { ...sections, outputs };
}

/**
 * The Expected field an output is compared with by default: same data type
 * first (text output -> text expected), else the first one.
 */
export function defaultExpectedFor(output, expected = []) {
  if (!expected.length) return null;
  const type = output?.column?.data_type ?? "text";
  return (
    expected.find((e) => (e.column.data_type ?? "text") === type) ?? expected[0]
  );
}

/** The row's user cells as `{name: value}` (JSON strings parsed). */
export function rowAsJson(columns = [], row = null) {
  const out = {};
  columns
    .filter((c) => c.origin_type !== EVAL_REASON)
    .forEach((c) => {
      const v = cellOf(row, c.id)?.cell_value;
      out[c.name] = parseJsonString(v) ?? v ?? null;
    });
  return {
    row_id: row?.row_id ?? null,
    row: rowNumberOf(row),
    values: out,
    metadata: row?.row_meta?.metadata ?? {},
  };
}

const THUMB = { up: "👍", down: "👎" };

/** Verdict-like chip text for a human label cell (`{"value":"down"}` -> 👎). */
export function humanVerdict(cell) {
  const raw = cell?.cell_value;
  if (raw == null || raw === "")
    return { status: "empty", value: null, display: "Not labelled" };
  const parsed = typeof raw === "string" ? parseJsonString(raw) ?? raw : raw;
  const v =
    parsed?.value ?? parsed?.rating ?? parsed?.selected?.[0] ?? parsed ?? null;
  if (v === "up" || v === "down")
    return {
      status: v === "up" ? "pass" : "fail",
      value: v === "up" ? 1 : 0,
      display: THUMB[v],
    };
  if (typeof v === "number")
    return { status: "neutral", value: v, display: `${v} ★` };
  return { status: "neutral", value: null, display: String(v) };
}

/**
 * The model with the row's pending edits applied (change-set `byCell` for
 * this row): each user field gains `value` and `edited`; human labels a
 * verdict; `rowMetadata` = `{value, edited}`.
 * @param {object} model   buildRowModel(...)
 * @param {object} [edits] byCell[rowId]
 * @param {object} [rowMetadata] row_meta.metadata
 * @param {string} metaKey change-set key of row-metadata edits
 */
export function withEdits(model, edits = {}, rowMetadata = {}, metaKey) {
  const decorate = (f) => {
    const e = edits?.[f.column.id];
    return {
      ...f,
      value: e ? e.after : f.cell?.cell_value ?? "",
      original: f.cell?.cell_value ?? "",
      edited: Boolean(e),
    };
  };
  const metaEdit = metaKey ? edits?.[metaKey] : null;
  return {
    ...model,
    input: model.input.map(decorate),
    expected: model.expected.map(decorate),
    metadata: model.metadata.map(decorate),
    noRole: model.noRole.map(decorate),
    human: model.human.map((h) => ({ ...h, verdict: humanVerdict(h.cell) })),
    rowMetadata: {
      value: metaEdit ? metaEdit.after : rowMetadata ?? {},
      original: rowMetadata ?? {},
      edited: Boolean(metaEdit),
    },
  };
}

/** The row's split ids with pending `set_splits` ops applied, in order. */
export function effectiveSplitIds(baseIds = [], ops = [], rowId) {
  let ids = [...(baseIds ?? [])];
  (ops ?? []).forEach((o) => {
    if (o.op !== "set_splits" || !o.row_ids?.includes(rowId)) return;
    const remove = new Set(o.remove ?? []);
    ids = ids.filter((id) => !remove.has(id));
    (o.add ?? []).forEach((id) => {
      if (!ids.includes(id)) ids.push(id);
    });
  });
  return ids;
}

/** True when the change set holds anything for this row. */
export function rowHasPending(ops = [], rowId) {
  return (ops ?? []).some(
    (o) =>
      (o.op === "add_row" && o.temp_id === rowId) ||
      ((o.op === "update_cell" || o.op === "set_row_metadata") &&
        o.row_id === rowId) ||
      ((o.op === "set_splits" || o.op === "delete_rows") &&
        o.row_ids?.includes(rowId)),
  );
}

/** `Added v1 · edited v3` from row_meta. */
export function versionPillText(meta) {
  const added = meta?.added_in_version;
  const changed = meta?.last_changed_in_version;
  if (!Number.isFinite(added)) return null;
  return Number.isFinite(changed) && changed > added
    ? `Added v${added} · edited v${changed}`
    : `Added v${added}`;
}

// ---- R3 (P2) pure helpers -------------------------------------------

/** RV#27: a field with nothing in it (`""`, `[]`, `{}`, null). */
export function isEmptyValue(v) {
  if (v == null) return true;
  if (typeof v === "string") {
    const t = v.trim();
    return t === "" || t === "[]" || t === "{}" || t === "null";
  }
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v).length === 0;
  return false;
}

const byVersion = (versions, n) =>
  (versions ?? []).find((v) => v.number === n) ?? null;
const whoWhen = (v) =>
  v
    ? `${v.created_by?.name ?? "someone"}, ${fmtTimeAbsolute(v.created_at)}`
    : null;

/**
 * RV#17: `Added in v1 by Asha Rao, 26 Sep 2026 10:12. Last edited in v4
 * (Meera Iyer, 12 Oct 2026 …). Click for history.`
 */
export function versionPillTooltip(meta, versions) {
  if (!isSurfaceShown("dataset.versions")) return "";
  const added = meta?.added_in_version;
  const edited = meta?.last_changed_in_version;
  const a = byVersion(versions, added);
  const e = byVersion(versions, edited);
  const parts = [];
  if (added) parts.push(`Added in v${added}${a ? ` by ${whoWhen(a)}` : ""}.`);
  if (edited && edited !== added)
    parts.push(`Last edited in v${edited}${e ? ` (${whoWhen(e)})` : ""}.`);
  parts.push("Click for the version history.");
  return parts.join(" ");
}

const SOURCE_LABEL = {
  replay: "Expected from a replay",
  import: "Expected from the import",
  production_output: "Expected = production output",
  human_correction: "Expected corrected by hand",
  empty: "No expected yet",
};

/** R2D-22: where the Expected value came from (`expected_source`). */
export function expectedSourceLabel(src) {
  const kind = typeof src === "string" ? src : src?.kind;
  return SOURCE_LABEL[kind] ?? `Expected from ${kind ?? "unknown"}`;
}

export function expectedSourceTooltip(src) {
  const kind = typeof src === "string" ? src : src?.kind;
  if (kind === "production_output")
    return "The expected value is what production answered. Review it before you trust scores against it.";
  if (kind === "replay")
    return "The expected value was set from a replay of this conversation.";
  if (kind === "human_correction")
    return "Someone corrected the expected value by hand.";
  return "Where this row's expected value came from.";
}

/** Every field of the row in display order, with its JSON text. */
export function rawFields(model) {
  const groups = [
    ["input", model.input],
    ["expected", model.expected],
    ["output", model.outputs.map((o) => ({ ...o, value: o.cell?.cell_value }))],
    ["metadata", model.metadata],
    [null, model.noRole],
  ];
  const out = [];
  groups.forEach(([role, fields]) =>
    (fields ?? []).forEach((f) =>
      out.push({
        id: f.column.id,
        name: f.column.name,
        role: role ?? f.column.role ?? null,
        text: prettyJson(f.value ?? f.cell?.cell_value ?? null),
      }),
    ),
  );
  if (model.rowMetadata?.value && Object.keys(model.rowMetadata.value).length)
    out.push({
      id: "__row_metadata__",
      name: "row_metadata",
      role: "metadata",
      text: prettyJson(model.rowMetadata.value),
    });
  return out;
}
