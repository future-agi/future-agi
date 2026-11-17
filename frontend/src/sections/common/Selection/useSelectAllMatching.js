// W5-7 (W5-SCOPE §7 C3): the selection of a results view, widened to every
// matching row on request. The grid selects the rows it loaded; when all of
// them are selected and the view has more, `Select all N matching rows`
// reads every id of the view from R3 (`ids_only=true`) and the selection
// becomes those ids. Changing the view or the grid's selection drops it.
import { useCallback, useEffect, useMemo, useState } from "react";
import { runResultsParams } from "src/api/develop/run-results";
import {
  allSelectedText,
  selectAllMatching,
  selectAllOffer,
} from "./selectAllMatching";

/**
 * The bar's words: `12 selected`, or `All 34 selected` once the selection is
 * the whole view (`title` says it in full: `All 34 matching rows selected`).
 * @returns {{text: string, title: string|null}}
 */
export function selectionText({ count, total, widened }) {
  const n = count.toLocaleString("en-US");
  if (widened || (Number.isFinite(total) && total > 0 && count >= total))
    return { text: `All ${n} selected`, title: allSelectedText(count) };
  return { text: `${n} selected`, title: null };
}

/**
 * @param {{datasetId: string, view: object, selectedIds: string[],
 *   loadedCount?: number, total?: number}} args
 * @returns {{ids: string[], text: string, title: string|null,
 *   offer: object|null, selectAll: Function, busy: boolean,
 *   error: string|null}}
 */
export function useSelectAllMatching({
  datasetId,
  view,
  selectedIds,
  loadedCount,
  total,
}) {
  const [widened, setWidened] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const viewKey = useMemo(
    () => (view ? JSON.stringify(runResultsParams(view)) : ""),
    [view],
  );
  const picked = selectedIds ?? [];
  const pickedKey = picked.join(",");
  useEffect(() => {
    setWidened(null);
    setError(null);
  }, [viewKey, pickedKey]);
  const live = Boolean(widened) && widened.key === `${viewKey}|${pickedKey}`;
  const offer = live
    ? null
    : selectAllOffer({ selectedCount: picked.length, loadedCount, total });
  const selectAll = useCallback(async () => {
    if (!datasetId || !view) return;
    setBusy(true);
    setError(null);
    try {
      const ids = await selectAllMatching(datasetId, view);
      setWidened({ key: `${viewKey}|${pickedKey}`, ids });
    } catch (e) {
      setError(e?.message ?? "Could not read the matching rows");
    } finally {
      setBusy(false);
    }
  }, [datasetId, view, viewKey, pickedKey]);
  const ids = live ? widened.ids : picked;
  return {
    ids,
    ...selectionText({ count: ids.length, total, widened: live }),
    offer,
    selectAll,
    busy,
    error,
  };
}
