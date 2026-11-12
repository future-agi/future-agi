// AG Grid adapter for column shaping (ONE-VIEW-SPEC §3.2).
//
//   const layout = useColumnLayout({ surface: "results", scopeId, columns });
//   const shaping = useAgColumnShaping({ gridRef, layout, sort, onSort });
//   <AgGridReact ref={gridRef} {...shaping} columnDefs={defs} … />
//
// - applies the layout (order, hide, pinned, user widths) on grid ready,
//   when new columnDefs load and whenever the layout changes;
// - writes back what the USER does in the grid (drag-move, drag-pin, resize,
//   hide) — only finished, UI-sourced events, never our own applyColumnState;
// - `getMainMenuItems` returns the same items as HeaderMenu, so AG Grid's
//   column menu (header ⋮ and header right-click) IS our menu;
// - Shift+F10 / the ContextMenu key on a focused header opens it.
// `onCellContextMenu` stays the screen's. Columns AG knows but the catalog
// doesn't (selection, auto group) are left alone.
import { useCallback, useEffect, useMemo, useRef } from "react";
import { fromAgColumnState, toAgColumnState } from "./columnLayout";
import { headerMenuItems, toAgMenuItems } from "./headerMenuItems";
import { isMenuKey } from "./HeaderMenu";

const USER_SOURCES = new Set([
  "uiColumnMoved",
  "uiColumnDragged",
  "uiColumnResized",
  "toolPanelUi",
  "toolPanelDragAndDrop",
  "columnMenu",
  "contextMenu",
]);

/** True for a finished event the user caused (exported for tests). */
export const isUserColumnEvent = (e) =>
  Boolean(e) && e.finished !== false && USER_SOURCES.has(e.source);

const liveApi = (gridRef) => {
  const api = gridRef?.current?.api;
  return api && !api.isDestroyed?.() ? api : null;
};

/** The AG Grid sort as `{id, desc}` (first sorted column), else null. */
function agSortOf(api) {
  try {
    const s = api
      .getColumnState()
      .filter((c) => c.sort)
      .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))[0];
    return s ? { id: s.colId, desc: s.sort === "desc" } : null;
  } catch {
    return null;
  }
}

/**
 * @param {{gridRef: object, layout: object, sort?: object|null,
 *   onSort?: Function, group?: string|null, onGroup?: Function}} args
 *   without `onSort` the menu sorts with AG Grid's own sort
 * @returns {object} props to spread on `<AgGridReact>`
 */
export default function useAgColumnShaping({
  gridRef,
  layout,
  sort,
  onSort,
  group,
  onGroup,
}) {
  const state = useMemo(
    () => toAgColumnState(layout.columns),
    [layout.columns],
  );
  const stateKey = JSON.stringify(state);
  const live = useRef({});
  live.current = { layout, sort, onSort, group, onGroup, state };
  const applying = useRef(false);

  const apply = useCallback((api) => {
    if (!api || api.isDestroyed?.()) return;
    applying.current = true;
    try {
      api.applyColumnState({ state: live.current.state, applyOrder: true });
    } finally {
      applying.current = false;
    }
  }, []);

  useEffect(() => {
    apply(liveApi(gridRef));
  }, [stateKey, apply, gridRef]);

  const writeBack = useCallback((e) => {
    if (applying.current || !isUserColumnEvent(e)) return;
    const { layout: l } = live.current;
    const next = fromAgColumnState(e.api.getColumnState(), l.catalog);
    if (l.prefs?.widths) next.widths = l.prefs.widths;
    l.setPrefs(next);
  }, []);

  const onColumnResized = useCallback((e) => {
    if (applying.current || !isUserColumnEvent(e)) return;
    const { layout: l } = live.current;
    const known = new Set(l.catalog.map((c) => c.id));
    const cols = e.columns ?? (e.column ? [e.column] : []);
    const widths = { ...(l.prefs?.widths ?? {}) };
    cols.forEach((c) => {
      const id = c.getColId?.();
      if (known.has(id)) widths[id] = Math.round(c.getActualWidth());
    });
    l.setPrefs({ ...(l.prefs ?? {}), widths });
  }, []);

  // Stable (AG reads getMainMenuItems once): always the latest layout.
  const getMainMenuItems = useCallback((params) => {
    const id = params.column?.getColId?.();
    const {
      layout: l,
      sort: s,
      onSort: os,
      group: g,
      onGroup: og,
    } = live.current;
    if (!id || !l.catalog.some((c) => c.id === id)) return params.defaultItems;
    const api = params.api;
    const sortFn =
      os ??
      ((next) =>
        api.applyColumnState({
          state: next
            ? [{ colId: next.id, sort: next.desc ? "desc" : "asc" }]
            : [],
          defaultState: { sort: null },
        }));
    return toAgMenuItems(
      headerMenuItems(l, id, {
        sort: os ? s : agSortOf(api),
        onSort: sortFn,
        group: g,
        onGroup: og,
      }),
    );
  }, []);

  // Shift+F10 / ContextMenu key on a focused header of THIS grid.
  useEffect(() => {
    const onKey = (e) => {
      if (!isMenuKey(e)) return;
      const api = liveApi(gridRef);
      const cell = document.activeElement?.closest?.(".ag-header-cell[col-id]");
      if (!api || !cell) return;
      const id = cell.getAttribute("col-id");
      if (!live.current.layout.catalog.some((c) => c.id === id)) return;
      if (!api.getColumn?.(id)) return;
      const root = cell.closest(".ag-root-wrapper");
      const gridId = api.getGridId?.();
      if (
        root?.getAttribute("grid-id") &&
        gridId &&
        root.getAttribute("grid-id") !== gridId
      )
        return;
      e.preventDefault();
      e.stopPropagation();
      api.showColumnMenu(id);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [gridRef]);

  return useMemo(
    () => ({
      maintainColumnOrder: true,
      onGridReady: (e) => apply(e.api),
      onNewColumnsLoaded: (e) => apply(e.api),
      onColumnMoved: writeBack,
      onColumnPinned: writeBack,
      onColumnVisible: writeBack,
      onColumnResized,
      getMainMenuItems,
    }),
    [apply, writeBack, onColumnResized, getMainMenuItems],
  );
}

export { useAgColumnShaping };
