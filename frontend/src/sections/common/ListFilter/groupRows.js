// Group rows for components/data-table/DataTable (W9-SCOPE §5 A3): full-width group header rows in
// the same grid (one header, one keyboard model). A row can sit in several
// groups (a dataset with two tags), so each body row gets a grid id of
// `<group key>::<row id>`; selection maps back to the row id, so selecting
// a dataset in one group selects it everywhere. Pure.

export const GROUP_ROW_PREFIX = "__group:";
const SEP = "::";

/** Is this grid row a group header? */
export const isGroupRow = (row) => Boolean(row?.__group);

/**
 * Grid rows: for each group a header row, then its rows unless collapsed.
 * @param {{key: string, label: string, count: number, below_baseline?: number, rows: object[]}[]} groups
 * @param {(row) => string} rowIdOf
 * @param {Iterable<string>} [collapsed]
 */
export function flattenGroups(groups, rowIdOf, collapsed) {
  const closed = new Set(collapsed ?? []);
  const out = [];
  (groups ?? []).forEach((g) => {
    const isClosed = closed.has(g.key);
    out.push({
      id: `${GROUP_ROW_PREFIX}${g.key}`,
      __group: { ...g, collapsed: isClosed },
    });
    if (isClosed) return;
    (g.rows ?? []).forEach((row) =>
      out.push({
        ...row,
        __gridId: `${g.key}${SEP}${rowIdOf(row)}`,
        __rowId: rowIdOf(row),
      }),
    );
  });
  return out;
}

/** The grid id of a flattened row. */
export const gridIdOf = (row) => (isGroupRow(row) ? row.id : row.__gridId);

/**
 * Grid ids to select, given the selected row ids: every copy of each
 * selected row, in every open group.
 */
export function gridSelection(flatRows, selectedRowIds) {
  const want = new Set(selectedRowIds ?? []);
  return (flatRows ?? [])
    .filter((r) => !isGroupRow(r) && want.has(r.__rowId))
    .map((r) => r.__gridId);
}

/**
 * Row ids from a new grid selection. A row picked or dropped in one group
 * is picked or dropped everywhere: compare with the previous selection.
 */
export function rowIdsFromGrid(flatRows, nextGridIds, prevRowIds) {
  const byGrid = new Map(
    (flatRows ?? [])
      .filter((r) => !isGroupRow(r))
      .map((r) => [r.__gridId, r.__rowId]),
  );
  const prev = new Set(prevRowIds ?? []);
  const next = new Set(
    (nextGridIds ?? []).map((g) => byGrid.get(g)).filter(Boolean),
  );
  // A row still selected in one group but dropped in another was dropped.
  const visibleCopies = new Map();
  byGrid.forEach((rowId, gridId) => {
    if (!visibleCopies.has(rowId)) visibleCopies.set(rowId, []);
    visibleCopies.get(rowId).push(gridId);
  });
  const nextGrid = new Set(nextGridIds ?? []);
  const out = new Set();
  next.forEach((rowId) => {
    const copies = visibleCopies.get(rowId) ?? [];
    const allOn = copies.every((g) => nextGrid.has(g));
    if (!prev.has(rowId) || allOn) out.add(rowId);
  });
  // Selected rows whose copies are all in collapsed groups stay selected.
  prev.forEach((rowId) => {
    if (!visibleCopies.has(rowId)) out.add(rowId);
  });
  return [...out];
}

/** `Asha Rao · 4 datasets · 1 below baseline`. */
export function groupHeaderText(group, noun = "dataset") {
  const n = group?.count ?? 0;
  const parts = [group?.label ?? "", `${n} ${noun}${n === 1 ? "" : "s"}`];
  if (group?.below_baseline)
    parts.push(`${group.below_baseline} below baseline`);
  return parts.join(" · ");
}
