import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useRowInView } from "src/api/develop/row-view";
import { useAsOfVersion } from "src/api/develop/dataset-versions";
import { useAsOfView } from "../Versions/versionedSource";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
} from "src/sections/develop-detail/states";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { useDataGridView } from "../DataGrid/useDataGridView";
import { systemFilters as toSystemFilters } from "../DataGrid/dataGridView";
import { gridNavQuery, headerPosition } from "./gridNavQuery";
import { useChangeSetStore } from "../Versions/changeSetStore";
import {
  findAddRowOp,
  isPendingRowId,
  pendingRowFromOp,
} from "../Versions/pendingRows";

const ACTIVE = "active-row";

/** Mark the open row in the grid (rows rendered later use getRowClass). */
function markActiveRow(rowId) {
  if (typeof document === "undefined") return;
  document
    .querySelectorAll(`.develop-data-grid .ag-row.${ACTIVE}`)
    .forEach((el) => el.classList.remove(ACTIVE));
  if (!rowId) return;
  document
    .querySelectorAll(`.develop-data-grid .ag-row[row-id="${rowId}"]`)
    .forEach((el) => el.classList.add(ACTIVE));
}

/**
 * Navigation for the dataset Row view (PRODUCT-SPEC §3.4 Navigation).
 * The set is the grid's current filters, sort and search. The open row
 * renders from the grid's loaded node at once; `get-row-data` then gives
 * its position and up to 50 ids each side. J/K replace the URL.
 *
 * @returns {{row, view, isLoading, isError, refetch, position, outside,
 *   notFound, goPrev, goNext, prevDisabled, nextDisabled, nav}}
 */
/**
 * RV#9: `"middle"` when the row sits within 2 rows of the viewport's top or
 * bottom edge (or outside it), else `null` (AG Grid's nearest scroll).
 */
export function scrollPlacement(api, node, margin = 2) {
  const range = api?.getVerticalPixelRange?.();
  const top = node?.rowTop;
  const h = node?.rowHeight;
  if (!range || !Number.isFinite(top) || !Number.isFinite(h)) return null;
  const pad = margin * h;
  return top < range.top + pad || top + h > range.bottom - pad
    ? "middle"
    : null;
}

/**
 * integration (DE-02): ↑ / ↓ in the grid move the open row from the grid's
 * focused cell. After J / K (or a link) opened another row, a focused cell
 * left on the old row would send ↓ back one row; while the keyboard is in
 * the grid, the focused cell moves to the open row (same column).
 */
export function followFocus(api, node) {
  if (typeof document === "undefined" || !api?.getFocusedCell) return;
  const focused = api.getFocusedCell();
  if (!focused || focused.rowIndex === node.rowIndex) return;
  const grid = document.querySelector(".develop-data-grid");
  if (!grid || !grid.contains(document.activeElement)) return;
  api.setFocusedCell?.(node.rowIndex, focused.column, focused.rowPinned);
}

/**
 * The open row as of a version. The grid's loaded copy shows at once
 * (splits / change as of the version; counts are row-wide). integration review:
 * once get-row-data has answered as of the version (`fresh`), its copy
 * wins: after a restore the grid node still says `deleted_later` until the
 * grid re-reads, and that stale change must not drive the banner or the
 * restore.
 */
export function asOfRowOf({ asOf, gridRow, current, fresh }) {
  if (!asOf || !gridRow || !current || fresh) return current;
  return {
    ...current,
    ...gridRow,
    row_meta: {
      ...current.row_meta,
      ...gridRow.row_meta,
      counts: current.row_meta?.counts ?? gridRow.row_meta?.counts,
    },
  };
}

/**
 * PF-2: where J / K go and whether the chevrons are off (pure). Once
 * get-row-data answered, its ids decide; a side it did not send (`null`:
 * dev's get-row-data answers only `next`) falls back to the grid's
 * neighbour, so the previous row stays reachable.
 * @returns {{prev: string|null, next: string|null, prevDisabled: boolean,
 *   nextDisabled: boolean}}
 */
export function rowNavTargets({ loaded, outside, view, gridNeighbour }) {
  const side = (step) => {
    const ids = loaded ? (step > 0 ? view?.next : view?.prev) : null;
    return Array.isArray(ids) ? ids[0] ?? null : gridNeighbour(step);
  };
  const prev = side(-1);
  const next = side(1);
  return {
    prev,
    next,
    prevDisabled: Boolean(outside) || !prev,
    nextDisabled: Boolean(outside) || !next,
  };
}

export default function useRowNavigation({ datasetId, rowId, openRow }) {
  const { gridApi } = useDevelopDetailContext();
  const filters = useDevelopFilterStore((s) => s.filters);
  const search = useDevelopSearchStore((s) => s.search);
  // The toolbar's Failed only / Splits / Errored cells filters (L2).
  const { failedOnly, failedEvalIds, splitIds, cellErrorOnly } =
    useDataGridView(datasetId);
  const sysFilters = useMemo(
    () =>
      toSystemFilters({ failedOnly, failedEvalIds, splitIds, cellErrorOnly }),
    [failedOnly, failedEvalIds, splitIds, cellErrorOnly],
  );
  const [sortTick, setSortTick] = useState(0);

  const api = gridApi?.current ?? null;
  useEffect(() => {
    if (!api?.addEventListener) return undefined;
    const bump = () => setSortTick((t) => t + 1);
    api.addEventListener("sortChanged", bump);
    return () => api.removeEventListener?.("sortChanged", bump);
  }, [api]);

  const nav = useMemo(
    () =>
      gridNavQuery({
        filters,
        systemFilters: sysFilters,
        search,
        columnState: api?.getColumnState?.(),
      }),
    // sortTick re-reads the grid's column state after a sort change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filters, sysFilters, search, api, sortTick],
  );

  // A pending new row (`?row=new-…`) lives only in the change set: its row
  // is built from the `add_row` op (re-rendered as values are typed) and
  // the server is never asked about it (row-view.js skips temp ids).
  const isPending = isPendingRowId(rowId);
  const pendingOp = useChangeSetStore((s) =>
    isPending ? findAddRowOp(s.sets[datasetId]?.ops, rowId) : null,
  );
  const pendingRow = useMemo(
    () => (pendingOp ? pendingRowFromOp(pendingOp) : null),
    [pendingOp],
  );
  const hasPendingOp = Boolean(pendingOp);

  // The grid's loaded copy of the row shows while get-row-data answers.
  const gridRow = rowId ? api?.getRowNode?.(rowId)?.data ?? null : null;
  // As of `?version=n` the grid holds the row at that version (N3: user-data
  // cells + row_meta.change for "Diff vs latest"). integration: get-row-data is
  // asked as of that version too (`version_id` + the banner's compare /
  // change), so a row deleted later opens with its as-of neighbours.
  const [searchParams] = useSearchParams();
  const asOf = Boolean(searchParams.get("version"));
  const asOfState = useAsOfVersion(datasetId);
  const asOfView = useAsOfView(datasetId);
  const versionId = asOfState.isAsOf ? asOfState.version?.id ?? null : null;
  // Until N1 names the version, an as-of row is not asked at latest.
  const waitingForVersion = asOf && asOfState.isLoading;
  // Re-read the grid's copy once its as-of block loads (a `?row=` link),
  // and the grid's neighbour while the server sends no `prev` (PF-2).
  const [, setModelTick] = useState(0);
  const query = useRowInView(
    datasetId,
    rowId,
    {
      filters: nav.filters,
      sort: nav.sort,
      search: nav.search,
      ...(versionId && {
        versionId,
        compare: asOfView.compare,
        change: asOfView.change,
      }),
    },
    {
      enabled:
        Boolean(datasetId && rowId) &&
        !isPendingRowId(rowId) &&
        !waitingForVersion,
      placeholderData: () =>
        gridRow
          ? {
              current: gridRow,
              prev: [],
              next: [],
              position: { index: null, total: null },
              outside: false,
              notFound: false,
              placeholder: true,
            }
          : undefined,
    },
  );
  const view = isPending ? null : query.data ?? null;
  const loaded = Boolean(view && !view.placeholder);
  const prevFromGrid = loaded && view.prev == null;
  useEffect(() => {
    if ((!asOf && !prevFromGrid) || !api?.addEventListener) return undefined;
    const bump = () => setModelTick((t) => t + 1);
    api.addEventListener("modelUpdated", bump);
    return () => api.removeEventListener?.("modelUpdated", bump);
  }, [api, asOf, prevFromGrid]);
  const outside = Boolean(loaded && view.outside);
  const position = loaded ? headerPosition(view, nav) : null;

  // Grid neighbours (loaded block) while get-row-data is in flight.
  const gridNeighbour = useCallback(
    (step) => {
      const node = rowId ? api?.getRowNode?.(rowId) : null;
      if (!node || node.rowIndex == null) return null;
      const next = api.getDisplayedRowAtIndex?.(node.rowIndex + step);
      return next?.data?.row_id ?? next?.id ?? null;
    },
    [api, rowId],
  );

  const targetOf = useCallback(
    (step) => {
      const t = rowNavTargets({ loaded, outside: false, view, gridNeighbour });
      return step > 0 ? t.next : t.prev;
    },
    [loaded, view, gridNeighbour],
  );

  const go = useCallback(
    (step) => {
      const id = targetOf(step);
      if (id) openRow(id, { push: false });
    },
    [targetOf, openRow],
  );

  // Keep the open row highlighted and scrolled into view (RV#9): centred
  // when it is within 2 rows of an edge, else the nearest scroll; one
  // scroll per settle while J/K is held.
  const index0 = loaded ? view.position?.index : null;
  useEffect(() => {
    markActiveRow(rowId);
    if (!api || !rowId || isPending) return undefined;
    const t = setTimeout(() => {
      const node = api.getRowNode?.(rowId);
      if (node && node.rowIndex != null) {
        api.ensureNodeVisible?.(node, scrollPlacement(api, node));
        followFocus(api, node);
      } else if (Number.isFinite(index0) && !outside)
        api.ensureIndexVisible?.(index0, "middle");
    }, 40);
    return () => clearTimeout(t);
  }, [api, rowId, index0, outside, isPending]);
  useEffect(() => () => markActiveRow(null), []);

  // A new row trails the grid's last block, which may not be loaded yet:
  // scroll to the end until AG Grid renders it, then centre and mark it.
  useEffect(() => {
    if (!api?.addEventListener || !isPending || !hasPendingOp) return undefined;
    // A few tries: a processing grid never appends pending rows.
    let tries = 6;
    let done = false;
    const reveal = () => {
      if (done || tries <= 0 || api.isDestroyed?.()) return;
      tries -= 1;
      const node = api.getRowNode?.(rowId);
      if (node && node.rowIndex != null) {
        done = true;
        api.ensureNodeVisible?.(node, "middle");
        markActiveRow(rowId);
        return;
      }
      const count = api.getDisplayedRowCount?.() ?? 0;
      if (count > 0) api.ensureIndexVisible?.(count - 1, "bottom");
    };
    const t = setTimeout(reveal, 60);
    api.addEventListener("modelUpdated", reveal);
    return () => {
      clearTimeout(t);
      api.removeEventListener?.("modelUpdated", reveal);
    };
    // Only on open (not per typed value): the op object changes per edit.
  }, [api, rowId, isPending, hasPendingOp]);

  // DG#31: closing the panel gives keyboard focus back to the row's cell
  // (the cell you were on, else the row's first data column), unless you
  // already moved focus somewhere else on the page.
  const lastRowId = useRef(rowId);
  useEffect(() => {
    const prev = lastRowId.current;
    lastRowId.current = rowId;
    if (rowId || !prev || !api) return undefined;
    const t = setTimeout(() => {
      const ae =
        typeof document !== "undefined" ? document.activeElement : null;
      if (ae && ae !== document.body && ae.isConnected) return;
      const node = api.getRowNode?.(prev);
      if (!node || node.rowIndex == null) return;
      const focused = api.getFocusedCell?.();
      const col =
        (focused?.rowIndex === node.rowIndex && focused?.column) ||
        api
          .getAllDisplayedColumns?.()
          ?.find(
            (c) =>
              !c.getColDef?.()?.checkboxSelection &&
              !String(c.getColId?.() ?? "").startsWith("ag-Grid-"),
          );
      api.ensureNodeVisible?.(node, null);
      if (col) api.setFocusedCell?.(node.rowIndex, col);
    }, 80);
    return () => clearTimeout(t);
  }, [api, rowId]);

  const current = view?.current ?? null;
  const targets = rowNavTargets({ loaded, outside, view, gridNeighbour });
  const row = isPending
    ? pendingRow
    : asOfRowOf({
        asOf,
        gridRow,
        current,
        fresh: loaded && Boolean(versionId),
      });

  if (isPending)
    return {
      row,
      view: null,
      // Nothing to load: the body must not stay dimmed (bodyBusy).
      loaded: true,
      isLoading: false,
      isError: false,
      refetch: query.refetch,
      position: null,
      outside: false,
      notFound: false,
      // The op is gone (saved, discarded, or a reload: the change set is
      // not persisted). RowView closes the panel.
      pendingGone: !hasPendingOp,
      isPending: true,
      goPrev: () => go(-1),
      goNext: () => go(1),
      prevDisabled: !gridNeighbour(-1),
      nextDisabled: !gridNeighbour(1),
      nav,
    };

  return {
    row,
    view,
    loaded,
    isPending: false,
    pendingGone: false,
    isLoading: query.isLoading && !view,
    isError: query.isError,
    refetch: query.refetch,
    position,
    outside,
    notFound: Boolean(loaded && view.notFound),
    goPrev: () => go(-1),
    goNext: () => go(1),
    prevDisabled: targets.prevDisabled,
    nextDisabled: targets.nextDisabled,
    nav,
  };
}
