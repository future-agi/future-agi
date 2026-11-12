// The Row view's navigation set = the Data grid's current request
// (PRODUCT-SPEC §3.4 Navigation): filters (system and split filters
// included), sort and search, built exactly like DevelopDataV2's
// datasource (getDataSource) builds its get-dataset-table params.
import { isRealBackend } from "src/sections/develop/realBackendCaps";
import { transformFilter, validateFilter } from "../DevelopFilters/common";

/** Full table sort model; the hosted grid applies sortIndex order. */
function tableSortFromColumnState(columnState = []) {
  const sorted = (columnState ?? []).filter(({ sort }) => sort);
  if (isRealBackend())
    sorted.sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0));
  return sorted.map(({ colId, sort }) => ({
    column_id: colId,
    type: sort === "asc" ? "ascending" : "descending",
  }));
}

/** Row-detail reads accept one scalar sort on the hosted backend. */
export function sortFromColumnState(columnState = []) {
  const sort = tableSortFromColumnState(columnState);
  return isRealBackend() ? sort.slice(-1) : sort;
}

/**
 * IT-R2 G5 P5: get-row-data's request contract has no `filter_config.path`
 * (get-dataset-table has), so a nested-path filter (`metadata.customer.tier
 * is enterprise`) made the open row fail its contract check and the Row view
 * stayed a skeleton. The path rides in the column id instead
 * (`<column_id>::customer.tier`, the filter panel's own field id); the
 * prototype's get-row-data reads it back.
 */
export function rowDataFilter(f) {
  const path = f?.filter_config?.path;
  if (!path) return f;
  const { path: _path, ...config } = f.filter_config;
  return {
    ...f,
    column_id: `${f.column_id}::${path}`,
    filter_config: config,
  };
}

/** Valid filter-panel rows as API filters (same transform as the grid). */
export function apiFilters(filters = []) {
  return (filters ?? []).filter(validateFilter).map(transformFilter);
}

/**
 * @param {{filters?: object[], systemFilters?: object[], search?: string,
 *   columnState?: object[], total?: number}} grid
 *   `systemFilters` = the toolbar's API filters (Failed only, Splits,
 *   Errored cells), already in API shape (DataGrid/dataGridView.js).
 * @returns {{filters: object[], sort: object[], tableSort: object[],
 *   useGridNavigation: boolean, search: object|undefined,
 *   total: number|undefined, narrowed: boolean, key: string}}
 *   `narrowed` = filters or search reduce the set ("12 of 71 filtered").
 *   `key` identifies the full navigation set, not the scalar detail read.
 */
export function gridNavQuery({
  filters,
  systemFilters = [],
  search,
  columnState,
  total,
} = {}) {
  const f = [...apiFilters(filters), ...(systemFilters ?? [])].map(
    rowDataFilter,
  );
  const tableSort = tableSortFromColumnState(columnState);
  const sort = isRealBackend() ? tableSort.slice(-1) : tableSort;
  const s = typeof search === "string" && search.trim() ? search.trim() : "";
  const searchParam = s
    ? { key: s, type: ["text", "image", "audio"] }
    : undefined;
  return {
    filters: f,
    sort,
    tableSort,
    // Earlier numeric sorts can exclude rows even after the final scalar
    // sort replaces their ordering. Only the full grid has those neighbours.
    useGridNavigation: isRealBackend() && tableSort.length > 1,
    search: searchParam,
    total: Number.isFinite(total) ? total : undefined,
    narrowed: f.length > 0 || Boolean(s),
    key: JSON.stringify([f, tableSort, s]),
  };
}

/**
 * Header position from a get-row-data result: 1-based index, total, and
 * whether the set is filtered. `index` null = outside the set (`– of 71`).
 */
export function headerPosition(view, nav) {
  if (!view) return null;
  const total = view.position?.total ?? nav?.total;
  if (!Number.isFinite(total)) return null;
  const idx = view.position?.index;
  return {
    index: Number.isFinite(idx) ? idx + 1 : null,
    total,
    filtered: Boolean(nav?.narrowed),
  };
}
