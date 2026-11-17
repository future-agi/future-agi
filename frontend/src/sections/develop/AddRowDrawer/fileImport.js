// W3-1: a file added to an existing dataset goes through IM1 (`source.kind:
// "file"`), like every other import. The browser parses with the ONE parser
// (importFile.parseImport) for the preview; IM1 maps the roles, matches IDs,
// checks the schema and answers pending N2 operations. Pure helpers.
import { extOf } from "../AddDatasetDrawer/importFile";

const n = (x) => Number(x ?? 0).toLocaleString("en-US");
const rows = (x) => `${n(x)} ${Number(x) === 1 ? "row" : "rows"}`;
const plural = (x, one, many) => `${n(x)} ${Number(x) === 1 ? one : many}`;

/** The IM1 body for a file read in the browser. */
export function fileImportBody({
  datasetId,
  newDataset,
  fileName,
  text,
  roles = {},
  flatten = false,
  parseJson = false,
  notInFile = "keep",
  archivedMatches = "keep",
}) {
  return {
    source: {
      kind: "file",
      file_name: fileName,
      format: extOf(fileName),
      text,
    },
    target: datasetId ? { dataset_id: datasetId } : { new: newDataset ?? {} },
    mapping: {
      roles,
      flatten: Boolean(flatten),
      parse_json: Boolean(parseJson),
      not_in_file: notInFile,
      archived_matches: archivedMatches,
    },
  };
}

/**
 * `54 rows in the file · 30 new · 12 update existing rows by ticket_id ·
 * 10 unchanged · 2 duplicate IDs in the file (last one kept)`.
 */
export function importSummaryText(summary, idColumn) {
  if (!summary) return "";
  const parts = [`${rows(summary.in_file ?? summary.total)} in the file`];
  parts.push(`${n(summary.add)} new`);
  if (idColumn) {
    parts.push(
      summary.update === 1
        ? `1 updates an existing row by ${idColumn}`
        : `${n(summary.update)} update existing rows by ${idColumn}`,
      `${n(summary.unchanged)} unchanged`,
    );
    if (summary.duplicates_in_file)
      parts.push(
        `${plural(summary.duplicates_in_file, "duplicate ID", "duplicate IDs")} in the file (last one kept)`,
      );
  }
  if (summary.invalid)
    parts.push(`${n(summary.invalid)} don't match the schema`);
  return parts.join(" · ");
}

/** Extra lines under the summary (new columns, new splits, schema). */
export function importNotes(summary) {
  if (!summary) return [];
  const out = [];
  const cols = summary.new_columns ?? [];
  if (cols.length)
    out.push(
      `${plural(cols.length, "new column", "new columns")}: ${cols.join(", ")}`,
    );
  const splits = summary.new_splits ?? [];
  if (splits.length)
    out.push(
      `${plural(splits.length, "new split", "new splits")} when you save: ${splits.join(", ")}`,
    );
  if (summary.invalid)
    out.push(
      `${summary.invalid === 1 ? "1 row doesn't" : `${n(summary.invalid)} rows don't`} match the schema and won't be imported`,
    );
  return out;
}

/**
 * `1 row in the file matches an archived row (Row 5):` (W3-3), or null.
 * Those rows are updated either way; Restore brings them back.
 */
export function archivedMatchText(summary) {
  const k = summary?.archived_matches ?? 0;
  if (!k) return null;
  const rowsList = (summary.archived_match_rows ?? [])
    .filter((x) => x != null)
    .slice(0, 3)
    .map((x) => `Row ${x}`);
  const more = k > rowsList.length ? `, +${n(k - rowsList.length)}` : "";
  return `${k === 1 ? "1 row in the file matches an archived row" : `${n(k)} rows in the file match archived rows`}${rowsList.length ? ` (${rowsList.join(", ")}${more})` : ""}:`;
}

/** Why the add button is off, or null. */
export function addBlockReason(
  summary,
  notInFile = "keep",
  archivedMatches = "keep",
) {
  if (!summary) return null;
  const deletes =
    notInFile === "delete" || notInFile === "archive" ? summary.not_in_file : 0;
  const restores =
    archivedMatches === "restore" ? summary.archived_matches ?? 0 : 0;
  if (!summary.add && !summary.update && !deletes && !restores)
    return `Nothing changes · ${n(summary.add)} new · ${n(summary.update)} updated`;
  return null;
}

/** `Add 42 rows to pending changes` (+ updates and deletes when asked). */
export function addButtonLabel(
  summary,
  fallbackRows,
  notInFile = "keep",
  archivedMatches = "keep",
) {
  if (!summary) return `Add ${rows(fallbackRows)} to pending changes`;
  const parts = [];
  if (summary.add) parts.push(rows(summary.add));
  if (summary.update) parts.push(plural(summary.update, "update", "updates"));
  if (notInFile === "delete" && summary.not_in_file)
    parts.push(plural(summary.not_in_file, "delete", "deletes"));
  if (notInFile === "archive" && summary.not_in_file)
    parts.push(plural(summary.not_in_file, "archive", "archives"));
  if (archivedMatches === "restore" && summary.archived_matches)
    parts.push(plural(summary.archived_matches, "restore", "restores"));
  if (!parts.length) return `Add ${rows(0)} to pending changes`;
  const last = parts.pop();
  return `Add ${parts.length ? `${parts.join(", ")} and ${last}` : last} to pending changes`;
}

/**
 * IM1 commit operations → change-set ops: unique temp ids, cells by column
 * id, the split names / tags / source kept, the version note hint carried.
 */
export function toPendingOps(operations = [], { stamp = "", hint } = {}) {
  let i = 0;
  return operations.map((op) => {
    // Every op of one import carries its note (an upsert with updates only
    // still saves as `Uploaded … · 2 rows updated by ticket_id`).
    if (op.op !== "add_row")
      return { ...op, ...(hint ? { description_hint: hint } : {}) };
    i += 1;
    return {
      op: "add_row",
      temp_id: `file-${stamp}${i}`,
      kind: op.kind ?? "row",
      source: op.source ?? null,
      cells: { ...(op.cells_by_column_id ?? {}) },
      split_ids: [],
      ...(op.split_names?.length ? { split_names: op.split_names } : {}),
      ...(op.metadata ? { metadata: op.metadata } : {}),
      ...(op.status ? { status: op.status } : {}),
      ...(hint ? { description_hint: hint } : {}),
    };
  });
}

/** The schema issues of rejected items, most frequent first (`[{message, rows}]`). */
export function topIssues(items = [], n = 2) {
  const counts = new Map();
  items
    .filter((it) => it.status === "invalid")
    .forEach((it) =>
      [...new Set((it.issues ?? []).map((x) => x.message))].forEach((m) =>
        counts.set(m, (counts.get(m) ?? 0) + 1),
      ),
    );
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([message, rows]) => ({ message, rows }));
}

/** Rejected items (schema) as JSON lines for `Download them`. */
export const invalidItemsText = (items = []) =>
  items
    .filter((it) => it.status === "invalid")
    .map((it) =>
      JSON.stringify({
        ...it.cells,
        _issues: it.issues?.map((x) => x.message),
      }),
    )
    .join("\n");

/** W3-4: what the transformations changed, most frequent first. */
export function transformNotes(items = [], n = 3) {
  const counts = new Map();
  items.forEach((it) =>
    (it.transform_notes ?? []).forEach((m) =>
      counts.set(m, (counts.get(m) ?? 0) + 1),
    ),
  );
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([message, rows]) => ({ message, rows }));
}

/**
 * v2.1 g8 (rows:upload-file): the import preview's query key. It carries the
 * dataset's latest version and its pending change count, so the same file
 * uploaded again after a save (or a new pending change) recomputes the
 * preview instead of showing the earlier answer. Pure.
 */
export function filePreviewKey({
  datasetId,
  versionKey = null,
  pendingCount = 0,
  source,
  roles,
  flatten,
  parseJson,
  notInFile,
  archivedMatches,
} = {}) {
  return [
    "im1-file-preview",
    datasetId,
    versionKey,
    pendingCount,
    source?.name,
    source?.text?.length,
    roles,
    flatten,
    parseJson,
    notInFile,
    archivedMatches,
  ];
}
