import { useCallback, useMemo, useRef, useState } from "react";

/**
 * Selection that survives server-side pagination.
 *
 * The whole point: with search/filter/pagination on the backend, the client
 * only ever holds one page of rows and never knows every matching id. So a
 * plain "Set of selected ids" cannot express "select all 2,431 matching" — you
 * would have to fetch every page just to check the boxes. Instead selection is
 * a *predicate*:
 *
 *   mode: "include"  → `ids` are the rows the user explicitly picked.
 *   mode: "all"      → every row matching the current query is selected,
 *                      and `ids` are the exceptions the user un-checked.
 *
 * This is the Gmail model. It's identical whether the list is paged with
 * buttons or infinite-scrolled — the scroll mechanism never touches it — which
 * is exactly why the scroll choice is a rendering decision, not a selection
 * one.
 *
 * Bulk actions read `payload(predicate)`: in "all" mode they hand the backend
 * the active filter/search plus the exclusion list, and the server does the
 * delete/re-run over every match. In "include" mode they hand over the id list.
 * Either way the client never needs all the ids.
 *
 * `total` is the backend's count of rows matching the current query — required
 * for the running "N selected" count in "all" mode, and returned alongside the
 * page (see useScenarioPage).
 */
export default function useSelection(total = 0) {
  const [mode, setMode] = useState("include");
  const [ids, setIds] = useState(() => new Set());

  const isSelected = useCallback(
    (id) => (mode === "all" ? !ids.has(id) : ids.has(id)),
    [mode, ids],
  );

  const toggle = useCallback((id) => {
    setIds((prev) => {
      const next = new Set(prev);
      // In both modes the Set holds "the rows that differ from the mode's
      // default": picked rows in include-mode, un-picked rows in all-mode. So a
      // toggle is the same membership flip regardless of mode.
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Header checkbox on a page: select or clear exactly the rows on screen. Only
  // meaningful in include-mode; in all-mode the page is already all-selected, so
  // the header just clears the whole predicate instead (handled by the caller).
  const setPage = useCallback((pageIds, checked) => {
    setIds((prev) => {
      const next = new Set(prev);
      pageIds.forEach((id) => (checked ? next.add(id) : next.delete(id)));
      return next;
    });
  }, []);

  const selectAllMatching = useCallback(() => {
    setMode("all");
    setIds(new Set());
  }, []);

  const clear = useCallback(() => {
    setMode("include");
    setIds(new Set());
  }, []);

  // A stable ref so callers can reset selection when the query changes without
  // adding `clear` to their effect deps and re-running on every render.
  const clearRef = useRef(clear);
  clearRef.current = clear;

  const count = useMemo(
    () => (mode === "all" ? Math.max(0, total - ids.size) : ids.size),
    [mode, ids, total],
  );

  // Header tri-state for a given page of ids: fully checked, or partial.
  const pageState = useCallback(
    (pageIds) => {
      if (!pageIds.length) return { allChecked: false, someChecked: false };
      const on = pageIds.filter((id) => isSelected(id)).length;
      return {
        allChecked: on === pageIds.length,
        someChecked: on > 0 && on < pageIds.length,
      };
    },
    [isSelected],
  );

  // What a bulk action sends to the backend. `predicate` is the active
  // {search, filters} — the server re-derives the matching set from it.
  const payload = useCallback(
    (predicate) =>
      mode === "all"
        ? { mode: "all", ...predicate, excludeIds: Array.from(ids) }
        : { mode: "include", ids: Array.from(ids) },
    [mode, ids],
  );

  return {
    mode,
    count,
    isSelected,
    toggle,
    setPage,
    selectAllMatching,
    clear,
    clearRef,
    pageState,
    payload,
    // The raw members of the working Set: explicitly-picked ids in include-mode,
    // the exclusion list in all-mode. Callers must read `mode` to interpret it —
    // used by the chat-bus publish, which can name include-mode rows but only
    // counts an all-mode selection.
    idList: Array.from(ids),
    // Exposed for the "some rows on this page, but not the whole match" banner
    // and for tests.
    exceptionCount: ids.size,
  };
}
