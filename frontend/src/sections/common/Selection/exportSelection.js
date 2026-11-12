// W5-7 (W5-SCOPE §7 C3, finding 11): export a selection through W3 B's two
// Download… dialogs without editing them. A results selection is the page
// view narrowed by a `rows` pill (`Selected rows (n)`); a dataset selection
// is the grid's `system:row_ids` filter labelled `n selected rows`. Both
// are the shapes those dialogs already read (`view`, `currentFilter`).
import { rowsPill } from "src/sections/common/Results/filterPills";
import { rowIdsFilter } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";

export const SELECTED_ROWS_LABEL = "Selected rows";

const uniqueIds = (ids) => [...new Set((ids ?? []).filter(Boolean))];

/** `3 selected rows`, `1 selected row`. */
export function selectedRowsText(count) {
  return `${count.toLocaleString("en-US")} selected row${count === 1 ? "" : "s"}`;
}

/**
 * ResultsExportDialog `view`: the same runs, sort and pills, with the
 * selection as the one `rows` pill (a selection lies inside the view, so it
 * replaces any earlier rows pill rather than intersecting two).
 */
export function resultsSelectionView(view, ids) {
  const keep = (view?.pills ?? []).filter((p) => p?.type !== "rows");
  return {
    ...view,
    pills: [...keep, rowsPill(uniqueIds(ids), { label: SELECTED_ROWS_LABEL })],
  };
}

/** DatasetExportDialog `currentFilter`: exactly these rows, no search. */
export function datasetSelectionFilter(ids) {
  const rows = uniqueIds(ids);
  const label = selectedRowsText(rows.length);
  return { filters: [rowIdsFilter(rows, { label })], search: "", label };
}
