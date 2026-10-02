// Exact global sorting requires evaluating every matching user before LIMIT.
// At large-tenant scale that is the known unbounded query path, so Users stays
// in its deterministic server cursor order until a bounded sort index exists.
// Keeping the set empty also clears stale persisted AG Grid sort state.
const USER_GLOBAL_SORTABLE_COLUMN_IDS = new Set();

const USER_SORT_DIRECTIONS = new Set(["asc", "desc"]);

export const isUserGlobalSortSupported = (columnId) =>
  USER_GLOBAL_SORTABLE_COLUMN_IDS.has(columnId);

export const sanitizeUserSortModel = (sortModel) => {
  if (!Array.isArray(sortModel)) return [];

  return sortModel
    .filter(
      (sort) =>
        isUserGlobalSortSupported(sort?.colId) &&
        USER_SORT_DIRECTIONS.has(sort?.sort),
    )
    .map(({ colId, sort }) => ({ colId, sort }));
};

export const sanitizeUserColumnState = (columnState) => {
  if (!Array.isArray(columnState)) return [];

  return columnState.map((column) => {
    if (!column || typeof column !== "object") return column;

    const hasValidSort =
      isUserGlobalSortSupported(column.colId) &&
      USER_SORT_DIRECTIONS.has(column.sort);
    if (hasValidSort || (column.sort == null && column.sortIndex == null)) {
      return column;
    }

    return { ...column, sort: null, sortIndex: null };
  });
};
