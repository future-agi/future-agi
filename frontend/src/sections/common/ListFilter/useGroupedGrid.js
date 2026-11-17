// DataTable's group rows (W9-SCOPE §5 A3) as two hooks, so DataTable only
// passes what they return to the grid. With `groups` undefined every value
// is the caller's own (the grid is unchanged).
//   useGroupRows    rows, ids and the selection mapping (before selection)
//   useGroupColumns the first visible column spans group header rows
import React, { useCallback, useMemo } from "react";
import GroupHeaderCell from "./GroupHeaderCell";
import {
  flattenGroups,
  gridIdOf,
  gridSelection,
  isGroupRow,
  rowIdsFromGrid,
} from "./groupRows";

/**
 * @param {{groups?: object[], collapsedGroups?: string[], getRowId?: Function,
 *   getRowClassName?: Function}} p
 */
export function useGroupRows({
  groups,
  collapsedGroups,
  getRowId,
  getRowClassName,
}) {
  const grouped = Array.isArray(groups);
  const rowIdOf = useCallback(
    (row) => (getRowId ? getRowId(row) : row.id),
    [getRowId],
  );
  const rows = useMemo(
    () => (grouped ? flattenGroups(groups, rowIdOf, collapsedGroups) : null),
    [grouped, groups, rowIdOf, collapsedGroups],
  );
  return useMemo(() => {
    if (!rows)
      return {
        grouped: false,
        rows: null,
        getGridRowId: null,
        isRowSelectable: undefined,
        getRowClassName,
        toGridSelection: (ids) => ids,
        fromGridSelection: (next) => next,
        findRow: null,
        rowIdOf,
      };
    return {
      grouped: true,
      rows,
      getGridRowId: gridIdOf,
      isRowSelectable: (params) => !isGroupRow(params.row),
      getRowClassName: (params) =>
        isGroupRow(params.row)
          ? "fa-group-row"
          : getRowClassName?.(params) ?? "",
      toGridSelection: (ids) => gridSelection(rows, ids),
      fromGridSelection: (next, prevIds) => rowIdsFromGrid(rows, next, prevIds),
      findRow: (gridId) =>
        rows.find((r) => !isGroupRow(r) && r.__gridId === gridId) ?? null,
      rowIdOf,
    };
  }, [rows, getRowClassName, rowIdOf]);
}

/**
 * The grid's columns with the first visible one spanning group header rows.
 * @param {{grouped: boolean, columns: object[], visibility: object,
 *   groupNoun?: string, onToggleGroup?: Function}} p
 */
export function useGroupColumns({
  grouped,
  columns,
  visibility,
  groupNoun,
  onToggleGroup,
}) {
  return useMemo(() => {
    if (!grouped) return columns;
    const visible = columns.filter((c) => visibility?.[c.field] !== false);
    const first = visible[0]?.field;
    return columns.map((c) => {
      if (c.field !== first) return c;
      const inner = c.renderCell;
      return {
        ...c,
        colSpan: ({ row }) => (isGroupRow(row) ? visible.length : undefined),
        renderCell: (params) =>
          isGroupRow(params.row)
            ? React.createElement(GroupHeaderCell, {
                group: params.row.__group,
                noun: groupNoun,
                onToggle: onToggleGroup,
              })
            : inner
              ? inner(params)
              : params.value,
      };
    });
  }, [grouped, columns, visibility, groupNoun, onToggleGroup]);
}
