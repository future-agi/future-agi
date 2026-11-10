import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
  useDevelopSelectedRowsStore,
} from "../states";
import { transformFilter, validateFilter } from "./DevelopFilters/common";

const PAGE_SIZE = 100;
const MAX_SELECTION_ROWS = 100000;
const sameScope = (left, right) => Boolean(left && right) &&
  JSON.stringify(left) === JSON.stringify(right);
const changedSelection = () => new Error("Selection changed. Select the rows again.");
const incompleteSelection = () => new Error(
  "The selected rows could not be verified. Select fewer rows or select them again.",
);

export const getDatasetSelectionScope = (dataset) => ({
  dataset,
  search: useDevelopSearchStore.getState().search,
  filters: useDevelopFilterStore.getState().filters.filter(validateFilter).map(transformFilter),
  // A query that changes away and back is still a new selection scope.
  queryEpoch: useDevelopSelectedRowsStore.getState().queryEpoch,
});

export const isDatasetSelectionScopeCurrent = (scope, dataset) =>
  sameScope(scope, getDatasetSelectionScope(dataset));

export const recordGridSelection = (api) => {
  const { selectAll, toggledNodes } = api.getServerSideSelectionState() || {};
  const store = useDevelopSelectedRowsStore.getState();
  if (!selectAll && !toggledNodes?.length) {
    store.resetSelectedRows();
    return;
  }
  const context = api.getGridOption("context");
  const scope = context?.selectionScope;
  // Old rows remain visible during debounce/loading. Never bind their clicks
  // to a new query just because the search store has already changed.
  if (!scope || !isDatasetSelectionScopeCurrent(scope, scope.dataset) ||
      !Number.isSafeInteger(context.totalRowCount) || context.totalRowCount <= 0 ||
      (selectAll && context.totalRowCount <= toggledNodes.length)) {
    store.resetSelectedRows();
    api.deselectAll();
    return;
  }
  store.setSelection({ selectAll, toggledNodes }, scope);
};

export const captureRowSelection = (dataset, api) => {
  const store = useDevelopSelectedRowsStore.getState();
  const context = api?.getGridOption("context");
  if (!dataset || !isDatasetSelectionScopeCurrent(store.selectionScope, dataset) ||
      !sameScope(context?.selectionScope, store.selectionScope) ||
      (!store.selectAll && !store.toggledNodes.length)) {
    throw changedSelection();
  }
  const selection = {
    scope: JSON.parse(JSON.stringify(store.selectionScope)),
    revision: store.selectionRevision,
    selectAll: store.selectAll,
    toggledNodes: [...new Set(store.toggledNodes)],
    totalRows: context.totalRowCount,
  };
  if (selection.selectAll && (!Number.isSafeInteger(selection.totalRows) ||
      selection.totalRows <= selection.toggledNodes.length ||
      selection.totalRows > MAX_SELECTION_ROWS)) throw incompleteSelection();
  return selection;
};

export const assertRowSelectionCurrent = (selection, dataset, api) => {
  const current = captureRowSelection(dataset, api);
  if (!selection || selection.revision !== current.revision ||
      !sameScope(selection.scope, current.scope) ||
      (selection.selectAll && selection.totalRows !== current.totalRows)) {
    throw changedSelection();
  }
};

export const resolveSelectedRowIds = async (selection, assertCurrent) => {
  assertCurrent();
  const toggledIds = selection.toggledNodes;
  if (toggledIds.some((id) => typeof id !== "string" || !id)) throw incompleteSelection();
  if (!selection.selectAll) {
    if (!toggledIds.length) throw incompleteSelection();
    return [...toggledIds];
  }

  const { dataset, search, filters } = selection.scope;
  const total = selection.totalRows;
  if (!Number.isSafeInteger(total) || total <= 0 || total > MAX_SELECTION_ROWS) {
    throw incompleteSelection();
  }
  const pageCount = Math.ceil(total / PAGE_SIZE);
  const ids = new Set();
  for (let page = 0; page < pageCount; page += 1) {
    assertCurrent();
    // Call the existing ordinary read directly: cached grid pages may have a
    // different page size and exact_snapshot does not support search/filters.
    const response = await getDatasetQueryOptions(dataset, page, filters, [], search, {
      pageSize: PAGE_SIZE,
    }).queryFn();
    assertCurrent();
    const result = response?.data?.result;
    const metadata = result?.metadata;
    const rows = result?.table;
    const hasMore = page + 1 < pageCount;
    if (response?.data?.status === false || result?.is_processing_data ||
        !Array.isArray(rows) || !metadata ||
        metadata.total_rows !== total || metadata.page_size !== PAGE_SIZE ||
        metadata.current_page_index !== page || metadata.total_pages !== pageCount ||
        metadata.has_more !== hasMore ||
        metadata.next_page_index !== (hasMore ? page + 1 : null) ||
        !Array.isArray(metadata.error_messages) || metadata.error_messages.length ||
        rows.length !== Math.min(PAGE_SIZE, total - page * PAGE_SIZE)) {
      throw incompleteSelection();
    }
    for (const row of rows) {
      const id = row?.row_id ?? row?.rowId;
      // Repeated pages or overlapping offset pages are not a complete result.
      if (typeof id !== "string" || !id || ids.has(id)) throw incompleteSelection();
      ids.add(id);
    }
  }
  if (ids.size !== total || toggledIds.some((id) => !ids.has(id))) throw incompleteSelection();
  toggledIds.forEach((id) => ids.delete(id));
  if (!ids.size) throw incompleteSelection();
  assertCurrent();
  return [...ids];
};
