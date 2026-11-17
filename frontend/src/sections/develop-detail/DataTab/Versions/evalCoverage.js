// What the Evaluations step knows about the rows an experiment will score
// (IT-R1 G5 P6). Pure.
//
// - `selectedRowValues`: each selected row as `{[columnId]: value}`, from the
//   pending change-set (unsaved door rows) or the sample table. null when
//   the selection is larger than what the drawer has read (no guessing).
// - `emptyReferenceWarnings`: an eval that reads the reference column
//   (`Expected`) on rows where it is empty skips them; say so once, by name.
// - `foldedEvalNames`: an eval that is also one of another eval's checks
//   (`Task reply rubric v1` inside `Task success`) is shown once, inside.

const cellOf = (row, colId) => {
  const c = row?.[colId];
  return c && typeof c === "object" && "cell_value" in c ? c.cell_value : c;
};

export const isEmptyValue = (v) =>
  v == null ||
  (typeof v === "string" && !v.trim()) ||
  (Array.isArray(v) && !v.length);

/**
 * @param {{rowIds?: string[], ops?: object[], rows?: object[],
 *   exampleCount?: number|null}} s
 * @returns {Array<{id: string, cells: (colId: string) => any}>|null}
 */
export function selectedRowValues({ rowIds, ops, rows, exampleCount }) {
  const adds = new Map(
    (ops ?? [])
      .filter((o) => o?.op === "add_row" && o.temp_id)
      .map((o) => [o.temp_id, o]),
  );
  const sample = new Map((rows ?? []).map((r) => [r.row_id, r]));
  const ids = (rowIds ?? []).filter(Boolean);
  if (ids.length) {
    const out = [];
    for (const id of ids) {
      if (adds.has(id)) {
        const cells = adds.get(id).cells ?? {};
        out.push({ id, cells: (colId) => cells[colId] });
      } else if (sample.has(id)) {
        const row = sample.get(id);
        out.push({ id, cells: (colId) => cellOf(row, colId) });
      } else return null; // a selected row the drawer has not read
    }
    return out;
  }
  if (!rows?.length || exampleCount == null || rows.length < exampleCount)
    return null;
  return rows.map((row) => ({
    id: row.row_id,
    cells: (colId) => cellOf(row, colId),
  }));
}

const columnOfMapping = (v) => String(v ?? "").split("::")[0];

/**
 * `[{evalIndex, name, column, empty, total}]` for every eval that maps the
 * reference column (role `expected`, or the drawer's Reference column) and
 * finds it empty on some selected rows.
 */
export function emptyReferenceWarnings({
  evals,
  columns,
  referenceColumnId,
  selected,
}) {
  if (!selected?.length) return [];
  const refIds = new Set(
    (columns ?? [])
      .filter((c) => c.role === "expected" || c.id === referenceColumnId)
      .map((c) => c.id),
  );
  const out = [];
  (evals ?? []).forEach((e, evalIndex) => {
    const mapping = e?.config?.mapping ?? e?.mapping ?? {};
    const colId = Object.values(mapping)
      .map(columnOfMapping)
      .find((id) => refIds.has(id));
    if (!colId) return;
    const empty = selected.filter((r) => isEmptyValue(r.cells(colId))).length;
    if (!empty) return;
    out.push({
      evalIndex,
      name: e?.name ?? e?.evalTemplateName ?? "This evaluation",
      column: (columns ?? []).find((c) => c.id === colId)?.name ?? "expected",
      empty,
      total: selected.length,
    });
  });
  return out;
}

/** `Correctness needs expected_reply — empty on 6 of 6 selected rows; it will skip them.` */
export const emptyReferenceSentence = (w) =>
  `${w.name} needs ${w.column} — empty on ${w.empty} of ${w.total} selected ${w.total === 1 ? "row" : "rows"}; it will skip ${w.empty === 1 ? "it" : "them"}.`;

/**
 * Names of selected evals that are also a check inside another eval's
 * composition on the sample row: `{[evalName]: hostEvalName}`.
 * @param {Array<{name: string, checkNames: string[]}>} cards
 */
export function foldedEvalNames(cards) {
  const out = {};
  const names = new Set((cards ?? []).map((c) => c.name));
  (cards ?? []).forEach((host) =>
    (host.checkNames ?? []).forEach((n) => {
      if (n !== host.name && names.has(n) && !out[host.name])
        out[n] = host.name;
    }),
  );
  return out;
}
