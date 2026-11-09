// The Row view's navigation set = the Data grid's current request
// (PRODUCT-SPEC §3.4 Navigation): filters (system and split filters
// included), sort and search, built exactly like DevelopDataV2's
// datasource (getDataSource) builds its get-dataset-table params.
import { transformFilter, validateFilter } from "../DevelopFilters/common";

/** `[{column_id, type}]` from AG Grid column state (sorted columns only). */
export function sortFromColumnState(columnState = []) {
  return (columnState ?? []).reduce((acc, { colId, sort }) => {
    if (sort)
      acc.push({
        column_id: colId,
        type: sort === "asc" ? "ascending" : "descending",
      });
    return acc;
  }, []);
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
 * @returns {{filters: object[], sort: object[], search: object|undefined,
 *   total: number|undefined, narrowed: boolean, key: string}}
 *   `narrowed` = filters or search reduce the set ("12 of 71 filtered").
 *   `key` is stable for react-query.
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
  const sort = sortFromColumnState(columnState);
  const s = typeof search === "string" && search.trim() ? search.trim() : "";
  const searchParam = s
    ? { key: s, type: ["text", "image", "audio"] }
    : undefined;
  return {
    filters: f,
    sort,
    search: searchParam,
    total: Number.isFinite(total) ? total : undefined,
    narrowed: f.length > 0 || Boolean(s),
    key: JSON.stringify([f, sort, s]),
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
