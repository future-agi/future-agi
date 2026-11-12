// Dataset Download… (W3-6 B2, D32): the rows of one version — every row, a
// split or the Data tab's current filter — read from the existing table
// reads (latest = get-dataset-table, an older version = N3 `…/versions/
// {id}/table/`), paged until every row is in, then written by writers.js.
// The model below is pure; `fetchDatasetRows` is the only network call.
import axios, { endpoints } from "src/utils/axios";
import { tableRequestFor } from "src/sections/develop-detail/DataTab/Versions/versionedSource";
import { splitsFilter } from "src/api/develop/experiment-create";
import {
  EXPORT_FORMATS,
  exportFileName,
  toCsv,
  toJson,
  toJsonl,
  toOpenAiEvals,
  toOpenAiFineTune,
  toParquet,
  conversationTurns,
} from "./writers";

// The table reads cap a page at 500 rows (get-dataset-table, N3).
export const EXPORT_PAGE_SIZE = 500;

const USER_ROLES = new Set(["input", "expected", "metadata"]);
const GENERATED_ROLES = new Set(["output", "eval", "human"]);
const JSON_TYPES = new Set(["json", "array", "object", "conversation"]);

/** A column's role, as the grid reads it (`null` = no role). */
export const roleOf = (c) => c?.role ?? null;

const isUserColumn = (c) =>
  Boolean(c?.id) &&
  !GENERATED_ROLES.has(roleOf(c)) &&
  (USER_ROLES.has(roleOf(c)) || !roleOf(c)) &&
  [undefined, null, "OTHERS", "others"].includes(c.origin_type);
const isReasonColumn = (c) => c?.origin_type === "evaluation_reason";

/** User data columns (input / expected / metadata / no role), in order. */
export function userColumnsOf(columnConfig) {
  return (columnConfig ?? []).filter(isUserColumn);
}

/**
 * Generated columns (outputs, evals, human labels, dynamic columns), in
 * order; an eval's reason column rides with its eval.
 */
export function generatedColumnsOf(columnConfig) {
  return (columnConfig ?? []).filter(
    (c) => c?.id && !isUserColumn(c) && !isReasonColumn(c),
  );
}

/** The reason column of an eval column (same `source_id`), or null. */
const reasonColumnOf = (columns, evalColumn) =>
  evalColumn?.source_id
    ? (columns ?? []).find(
        (c) => isReasonColumn(c) && c.source_id === evalColumn.source_id,
      ) ?? null
    : null;

const isEvalColumn = (c) =>
  roleOf(c) === "eval" || c?.origin_type === "evaluation";

const parseJsonCell = (column, value) => {
  if (typeof value !== "string" || !JSON_TYPES.has(column?.data_type))
    return value;
  const t = value.trim();
  if (!t) return null;
  try {
    return JSON.parse(t);
  } catch {
    return value;
  }
};

const cellOf = (row, column) => row?.[column.id] ?? null;
const valueOf = (row, column) => {
  const v = cellOf(row, column)?.cell_value;
  return v === undefined || v === "" ? null : v;
};
const reasonOf = (row, column, reasonColumn) =>
  cellOf(row, column)?.value_infos?.reason ??
  (reasonColumn ? valueOf(row, reasonColumn) : null) ??
  null;

// Values typed by the column's data_type, for Parquet and JSON (CSV keeps
// the cell text byte for byte). A column is typed only when every value in
// the export parses; otherwise it stays text. Boolean cells read
// true/false, or Passed/Failed as the grid shows pass/fail evals.
const NUMBER_RE = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/;
const INTEGER_RE = /^[-+]?\d+$/;
const BOOLEAN_WORDS = new Map([
  ["true", true],
  ["false", false],
  ["passed", true],
  ["failed", false],
]);
const PARSERS = {
  float: (v) => {
    if (typeof v === "number") return Number.isFinite(v) ? v : undefined;
    const t = String(v).trim();
    return NUMBER_RE.test(t) ? Number(t) : undefined;
  },
  integer: (v) => {
    const n = typeof v === "number" ? v : String(v).trim();
    if (typeof n === "string" && !INTEGER_RE.test(n)) return undefined;
    const num = Number(n);
    return Number.isSafeInteger(num) ? num : undefined;
  },
  boolean: (v) =>
    typeof v === "boolean"
      ? v
      : BOOLEAN_WORDS.get(String(v).trim().toLowerCase()),
};
const PARQUET_TYPES = { float: "DOUBLE", integer: "INT64", boolean: "BOOLEAN" };

/**
 * `{columnId: parse}` for the float / integer / boolean columns whose every
 * value in these rows parses (empty cells count as parsed).
 */
export function typedColumnsOf(columns, rows) {
  const out = {};
  (columns ?? []).forEach((c) => {
    const parse = PARSERS[c?.data_type];
    if (!parse) return;
    const ok = (rows ?? []).every((r) => {
      const v = valueOf(r, c);
      return v === null || parse(v) !== undefined;
    });
    if (ok) out[c.id] = parse;
  });
  return out;
}

/** Row metadata keys present on any row (`row_meta.metadata`), sorted. */
export function rowMetadataKeys(rows) {
  const keys = new Set();
  (rows ?? []).forEach((r) =>
    Object.keys(r?.row_meta?.metadata ?? {}).forEach((k) => keys.add(k)),
  );
  return [...keys].sort();
}

/**
 * The file's records and flat columns for one choice.
 * JSON / JSONL records follow the column roles: `{row_id, row, splits,
 * input{}, expected{}, metadata{}, fields{} (no role), generated{},
 * row_metadata{}}`; CSV / Parquet get the same values flat, one column each
 * (eval reasons in `<eval> reason`). `flatRows` hold the cell text (CSV);
 * `typedRows` and the records hold float / integer / boolean columns as
 * numbers and booleans (typedColumnsOf), and those flat columns carry
 * their Parquet `type`.
 * @param {{columns: object[], rows: object[], splitNames?: Object<string,string>,
 *   generated?: boolean, rowMetadata?: boolean}} p
 */
export function datasetRecords({
  columns,
  rows,
  splitNames = {},
  generated = true,
  rowMetadata = false,
}) {
  const user = userColumnsOf(columns);
  const gen = generated ? generatedColumnsOf(columns) : [];
  const metaKeys = rowMetadata ? rowMetadataKeys(rows) : [];
  const reasons = Object.fromEntries(
    gen.map((c) => [c.id, reasonColumnOf(columns, c)]),
  );
  const typed = typedColumnsOf([...user, ...gen], rows);
  const typedValue = (c, raw) => (raw === null ? null : typed[c.id](raw));
  // JSON value of a cell: typed, else parsed JSON for json columns, else text.
  const jsonValue = (c, raw) =>
    typed[c.id] ? typedValue(c, raw) : parseJsonCell(c, raw);
  const typeOf = (c) =>
    typed[c.id] ? { type: PARQUET_TYPES[c.data_type] } : {};
  const flat = [
    ...user.map((c) => ({ key: `u:${c.id}`, label: c.name, ...typeOf(c) })),
    ...gen.flatMap((c) =>
      isEvalColumn(c)
        ? [
            { key: `g:${c.id}`, label: c.name, ...typeOf(c) },
            { key: `r:${c.id}`, label: `${c.name} reason` },
          ]
        : [{ key: `g:${c.id}`, label: c.name, ...typeOf(c) }],
    ),
    ...metaKeys.map((k) => ({ key: `m:${k}`, label: `row_metadata.${k}` })),
  ];
  // Keep dataset headers verbatim; only exporter-added metadata may move.
  // Reserve every emitted label first, using the importer's trimmed comparison.
  const usedLabels = new Set(
    flat.map((c) => String(c.label ?? c.key).trim()),
  );
  const metadataColumns = ["row_id", "row", "splits"].map((key) => {
    let label = key;
    for (let suffix = 1; usedLabels.has(label); suffix += 1) {
      label = `${key} (export${suffix === 1 ? "" : ` ${suffix}`})`;
    }
    usedLabels.add(label);
    return { key, label };
  });
  flat.unshift(...metadataColumns);
  const flatRows = [];
  const typedRows = [];
  const records = (rows ?? []).map((r) => {
    const splits = (r?.row_meta?.split_ids ?? []).map(
      (id) => splitNames[id] ?? id,
    );
    const rowNumber = Number.isFinite(r?.order) ? r.order + 1 : null;
    const flatRow = {
      row_id: r?.row_id ?? null,
      row: rowNumber,
      splits: splits.join(";"),
    };
    const typedRow = { ...flatRow };
    const rec = { row_id: r?.row_id ?? null, row: rowNumber, splits };
    const groups = { input: {}, expected: {}, metadata: {}, fields: {} };
    user.forEach((c) => {
      const raw = valueOf(r, c);
      flatRow[`u:${c.id}`] = raw;
      typedRow[`u:${c.id}`] = typed[c.id] ? typedValue(c, raw) : raw;
      groups[USER_ROLES.has(roleOf(c)) ? roleOf(c) : "fields"][c.name] =
        jsonValue(c, raw);
    });
    Object.entries(groups).forEach(([k, v]) => {
      if (Object.keys(v).length) rec[k] = v;
    });
    if (gen.length) {
      rec.generated = {};
      gen.forEach((c) => {
        const v = valueOf(r, c);
        flatRow[`g:${c.id}`] = v;
        typedRow[`g:${c.id}`] = typed[c.id] ? typedValue(c, v) : v;
        if (isEvalColumn(c)) {
          const reason = reasonOf(r, c, reasons[c.id]);
          flatRow[`r:${c.id}`] = reason;
          typedRow[`r:${c.id}`] = reason;
          rec.generated[c.name] = { value: jsonValue(c, v), reason };
        } else rec.generated[c.name] = jsonValue(c, v);
      });
    }
    if (metaKeys.length) {
      const meta = r?.row_meta?.metadata ?? {};
      rec.row_metadata = {};
      metaKeys.forEach((k) => {
        flatRow[`m:${k}`] = meta[k] ?? null;
        typedRow[`m:${k}`] = meta[k] ?? null;
        rec.row_metadata[k] = meta[k] ?? null;
      });
    }
    flatRows.push(flatRow);
    typedRows.push(typedRow);
    return rec;
  });
  return { records, flat, flatRows, typedRows };
}

/**
 * Default message roles for the OpenAI formats from the column roles: the
 * first input that is not a conversation is the user message, the first
 * input conversation the prior turns, the first text expected the
 * assistant reply, an expected list of tool calls the assistant's
 * tool_calls. Missing pieces stay null (the format names what it needs).
 */
export function defaultMessageRoles(columns, rows = []) {
  const user = userColumnsOf(columns);
  const isConversation = (c) =>
    rows.some((r) => {
      const v = valueOf(r, c);
      const turns = conversationTurns(v);
      return Array.isArray(turns) && turns.length > 0;
    });
  const inputs = user.filter((c) => roleOf(c) === "input");
  const expected = user.filter((c) => roleOf(c) === "expected");
  const turns = inputs.find((c) => isConversation(c)) ?? null;
  const userCol = inputs.find((c) => c !== turns) ?? turns ?? null;
  const textExpected =
    expected.find((c) => !JSON_TYPES.has(c.data_type)) ?? expected[0] ?? null;
  const toolCalls =
    expected.find((c) => c !== textExpected && JSON_TYPES.has(c.data_type)) ??
    null;
  return {
    user: userCol?.id ?? null,
    turns: turns && turns !== userCol ? turns.id : null,
    assistant: textExpected?.id ?? null,
    toolCalls: toolCalls?.id ?? null,
  };
}

/** Why a format cannot be written for these columns, or null. */
export function formatBlocker(format, roles) {
  if (format !== "openai_ft" && format !== "openai_evals") return null;
  if (!roles?.user) return "Needs an Input column";
  if (!roles?.assistant) return "Needs an Expected column";
  return null;
}

/**
 * The file for one format: `{data, mime, name}`. `data` is text, or bytes
 * for Parquet. CSV writes the cell text (`flatRows`); Parquet the typed
 * values (`typedRows`).
 */
export function datasetFile({
  format,
  records,
  flat,
  flatRows,
  typedRows,
  rows,
  columns,
  roles,
  system,
  meta,
  nameParts,
}) {
  const byId = Object.fromEntries((columns ?? []).map((c) => [c.id, c]));
  const messageRows = () =>
    (rows ?? []).map((r) => {
      const out = {};
      Object.values(roles ?? {}).forEach((id) => {
        if (id && byId[id]) out[id] = valueOf(r, byId[id]);
      });
      return out;
    });
  const messageRoles = {
    system: system || undefined,
    user: roles?.user,
    assistant: roles?.assistant,
    turns: roles?.turns ?? undefined,
    toolCalls: roles?.toolCalls ?? undefined,
  };
  let data;
  switch (format) {
    case "json":
      data = toJson({ meta, rows: records });
      break;
    case "jsonl":
      data = toJsonl(records);
      break;
    case "parquet":
      data = toParquet(typedRows ?? flatRows, flat);
      break;
    case "openai_ft":
      data = toOpenAiFineTune(messageRows(), messageRoles);
      break;
    case "openai_evals":
      data = toOpenAiEvals(messageRows(), messageRoles);
      break;
    default:
      data = toCsv(flatRows, flat);
  }
  return {
    data,
    mime: EXPORT_FORMATS[format]?.mime ?? "text/plain",
    name: exportFileName(nameParts, format),
  };
}

/**
 * Every row of one version (and filter), paged. Latest reads get-dataset-
 * table; an older version reads N3. Returns `{columns, rows, total,
 * datasetName, datasetConfig, version}`.
 * @param {{datasetId: string, version?: {id: string, number: number}|null,
 *   isLatest?: boolean, splitIds?: string[], filters?: object[], search?: string,
 *   onProgress?: Function}} p
 */
export async function fetchDatasetRows({
  datasetId,
  version = null,
  isLatest = true,
  splitIds = [],
  filters = null,
  search = "",
  onProgress,
}) {
  const allFilters = [...(filters ?? []), ...splitsFilter(splitIds)];
  const rows = [];
  let columns = [];
  let total = null;
  let datasetName = null;
  let datasetConfig = null;
  let meta = null;
  for (let page = 0; ; page += 1) {
    const { url, params } = tableRequestFor({
      datasetId,
      versionNumber: isLatest ? null : version?.number ?? null,
      versionId: isLatest ? null : version?.id ?? null,
      params: {
        filters: JSON.stringify(allFilters),
        current_page_index: page,
        page_size: EXPORT_PAGE_SIZE,
        ...(search
          ? {
              search: JSON.stringify({
                key: search,
                type: ["text", "image", "audio"],
              }),
            }
          : {}),
      },
    });
    // eslint-disable-next-line no-await-in-loop
    const res = await axios.get(url, { params });
    const result = res?.data?.result ?? {};
    if (page === 0) {
      columns = result.column_config ?? [];
      total = result.metadata?.total_rows ?? null;
      datasetName = result.metadata?.dataset_name ?? null;
      datasetConfig = result.dataset_config ?? null;
      meta = result.metadata ?? null;
    }
    const table = result.table ?? [];
    rows.push(...table);
    onProgress?.(rows.length, total);
    const more =
      result.metadata?.has_more ??
      (total != null ? rows.length < total : table.length === EXPORT_PAGE_SIZE);
    if (!more || !table.length) break;
  }
  // N3 lists an older version's user columns without their `role`; the
  // roles are the dataset's (latest column_config), so read them once.
  if (!isLatest && columns.some((c) => c.role === undefined)) {
    const res = await axios.get(endpoints.develop.getDatasetDetail(datasetId), {
      params: { column_config_only: true },
    });
    const roles = Object.fromEntries(
      (res?.data?.result?.column_config ?? []).map((c) => [c.id, c.role]),
    );
    columns = columns.map((c) =>
      c.role === undefined && roles[c.id] !== undefined
        ? { ...c, role: roles[c.id] }
        : c,
    );
  }
  return {
    columns,
    rows,
    total: total ?? rows.length,
    datasetName,
    datasetConfig,
    version: meta?.version ?? null,
  };
}

/** Row count of one version and split (one 1-row page; `metadata.total_rows`). */
export async function countDatasetRows({
  datasetId,
  version,
  isLatest,
  splitIds = [],
  filters = null,
  search = "",
}) {
  const { url, params } = tableRequestFor({
    datasetId,
    versionNumber: isLatest ? null : version?.number ?? null,
    versionId: isLatest ? null : version?.id ?? null,
    params: {
      filters: JSON.stringify([...(filters ?? []), ...splitsFilter(splitIds)]),
      current_page_index: 0,
      page_size: 1,
      ...(search
        ? {
            search: JSON.stringify({
              key: search,
              type: ["text", "image", "audio"],
            }),
          }
        : {}),
    },
  });
  const res = await axios.get(url, { params });
  return res?.data?.result?.metadata?.total_rows ?? 0;
}

/** Save a file the browser way (an object URL on a temporary link). */
export function saveFile({ data, mime, name }) {
  const blob = new Blob([data], { type: mime });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", name);
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Keep cleanup tied to the URL implementation that created the resource,
  // even if the page's global binding is replaced before the timer fires.
  const revokeObjectURL = window.URL.revokeObjectURL.bind(window.URL);
  window.setTimeout(() => revokeObjectURL(url), 1000);
  return blob.size;
}
