import { getRandomId } from "src/utils/utils";
import { useDevelopFilterStore } from "src/sections/develop-detail/states";
import { validateFilter } from "../DevelopFilters/common";

// One-click filters from headers, the stats popover and the cell context
// menu (G5, G7). They land in the existing filter panel's store, so they
// show as FilterChips and can be edited in the panel like any other filter.

const sameTarget = (a, b) =>
  a.columnId === b.columnId &&
  a.filterConfig.filterOp === b.filterConfig.filterOp;

/**
 * Add (or replace) one column filter and refresh the grid.
 * filterType: "text" | "number" | "boolean"; filterOp per filter-contract.
 */
export function addColumnFilter(
  api,
  { columnId, filterType, filterOp, filterValue },
) {
  const next = {
    id: getRandomId(),
    columnId,
    filterConfig: { filterType, filterOp, filterValue: filterValue ?? "" },
    _meta: { parentProperty: "" },
  };
  useDevelopFilterStore
    .getState()
    .setFilters((prev) => [
      ...(prev ?? []).filter((f) => validateFilter(f) && !sameTarget(f, next)),
      next,
    ]);
  api?.onFilterChanged?.();
}

/** Display text of a cell value for `Filter to “…”` (clipped). */
export function filterValueText(value, max = 28) {
  if (value == null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** The filter a header/stats action applies, or null. */
export function statsActionFilter(action, column) {
  const columnId = column?.id;
  if (!columnId) return null;
  switch (action) {
    case "failed":
      return {
        columnId,
        filterType: "text",
        filterOp: "equals",
        filterValue: "Failed",
      };
    case "below":
      return column?.stats?.threshold == null
        ? null
        : {
            columnId,
            filterType: "number",
            filterOp: "less_than",
            filterValue: column.stats.threshold,
          };
    case "empty":
      return {
        columnId,
        filterType: "text",
        filterOp: "is_null",
        filterValue: "",
      };
    default:
      return null;
  }
}
