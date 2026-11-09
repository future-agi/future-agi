// IT-R2 G5 P4: after an import, the grid shows the new rows. The Add data
// sheet narrows the grid to them (`New (6)`, the `system:row_ids` view
// filter) and asks for a one-time reveal; the grid's datasource takes the
// request after it renders those rows and scrolls to + flashes them.
import { remapRowIds } from "../Versions/unsavedSelection";
import { useDataGridViewStore } from "./useDataGridView";

export const NEW_ROWS_LABEL = "New";
const requests = new Map(); // datasetId -> row ids

/** Narrow the grid to `rowIds` (pill `New (6)`) and flash them once. */
export function revealNewRows(datasetId, rowIds) {
  const ids = (rowIds ?? []).filter(Boolean);
  if (!datasetId || !ids.length) return;
  requests.set(datasetId, ids);
  useDataGridViewStore
    .getState()
    .patch(datasetId, { rowIds: ids, rowIdsLabel: NEW_ROWS_LABEL });
}

/** The pending reveal for a dataset (consumed), or null. */
export function takeReveal(datasetId) {
  const ids = requests.get(datasetId) ?? null;
  requests.delete(datasetId);
  return ids;
}

/**
 * A save turned temp ids (`imp-…`) into saved row ids: the `New (6)` view
 * filter follows them, so the grid keeps showing the same rows.
 */
export function followSavedRows(datasetId, rowIdMap) {
  const store = useDataGridViewStore.getState();
  const view = store.view(datasetId);
  if (!view.rowIds?.length || !rowIdMap) return;
  if (!view.rowIds.some((id) => rowIdMap[id])) return;
  store.patch(datasetId, { rowIds: remapRowIds(view.rowIds, rowIdMap) });
}

/** Scroll to and flash the rendered rows with these ids (AG Grid api). */
export function flashRows(api, ids) {
  if (!api || !ids?.length) return;
  const nodes = ids.map((id) => api.getRowNode?.(id)).filter(Boolean);
  if (!nodes.length) return;
  try {
    api.ensureNodeVisible?.(nodes[0], "top");
    api.flashCells?.({
      rowNodes: nodes,
      flashDuration: 1200,
      fadeDuration: 800,
    });
  } catch {
    /* a destroyed grid: nothing to reveal */
  }
}
