// The Data toolbar's view state (DG#19, DG#38), shared by the left toolbar
// (search, filters) and the `View` menu on the right (one view, A1-10/11):
// what a saved view stores, whether the view changed (the View button's
// amber dot) and Reset view.
import { useCallback, useEffect, useMemo, useRef } from "react";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { NO_VALUE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { GRID_VIEW_PARAM, readGridViewLink } from "./gridViewLink";
import _ from "lodash";
import { useSearchParams } from "react-router-dom";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import {
  useDevelopFilterStore,
  useDevelopFilterStoreShallow,
  useDevelopSearchStore,
} from "../../states";
import { DEFAULT_DENSITY } from "./dataGridView";
import { useDataGridView } from "./useDataGridView";

/** True when a filter row has a value (the Filter badge counts these). */
export const filterIsActive = (f) =>
  NO_VALUE_FILTER_OPS.includes(f?.filterConfig?.filterOp) ||
  (Array.isArray(f?.filterConfig?.filterValue)
    ? f.filterConfig.filterValue.length > 0
    : f?.filterConfig?.filterValue !== "" &&
      f?.filterConfig?.filterValue != null);

/**
 * v2.1 g8 (grid:saved-views): what a saved view stores — search, Failed
 * only, density, splits, the column filters with a value, the expression
 * and the status / tag pills — and whether the view differs from the
 * default (the View button's amber dot). Pure.
 */
export function toolbarViewState({
  search,
  gridView = {},
  splits = "",
  filters = [],
} = {}) {
  return {
    q: search ?? "",
    failed: Boolean(gridView.failedOnly),
    fevals: gridView.failedEvalIds ?? [],
    density: gridView.density ?? DEFAULT_DENSITY,
    splits: splits ?? "",
    expr: gridView.expression || "",
    filters: (filters ?? []).filter(filterIsActive),
    statuses: gridView.statuses ?? [],
    tags: gridView.tags ?? [],
    sort: gridView.sort ?? [],
  };
}

export const toolbarViewModified = (v) =>
  Boolean(v.q) ||
  v.failed ||
  v.density !== DEFAULT_DENSITY ||
  Boolean(v.splits) ||
  v.filters.length > 0 ||
  Boolean(v.expr) ||
  v.statuses.length > 0 ||
  v.tags.length > 0 ||
  (v.sort ?? []).length > 0;

export default function useDataToolbarView(datasetId) {
  const [searchParams, setSearchParams] = useSearchParams();
  const gridView = useDataGridView(datasetId);
  const [, patchLayout] = useLayoutPrefs("grid", datasetId);
  const appliedLink = useRef(null);
  const { search, setSearch } = useDevelopSearchStore();
  const { filters } = useDevelopFilterStoreShallow((s) => ({
    filters: s.filters,
  }));
  const { gridApi } = useDevelopDetailContext();
  const activeFilters = useMemo(
    () => (filters ?? []).filter(filterIsActive).length,
    [filters],
  );

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const refilter = useCallback(
    _.debounce(() => gridApi?.current?.onFilterChanged?.(), 300),
    [gridApi],
  );
  const setSearchExtended = useCallback(
    (value) => {
      setSearch(value);
      refilter();
    },
    [setSearch, refilter],
  );

  const splits = searchParams.get("splits") ?? "";
  // v2.1 g8 (grid:saved-views): a saved view keeps what the dialog says,
  // "the filters": column filters, the expression and the status / tag
  // pills, besides search, Failed only, splits and density.
  const viewState = toolbarViewState({ search, gridView, splits, filters });
  const modified = toolbarViewModified(viewState);

  const applyView = (v, keepLink = false) => {
    const sort = Array.isArray(v?.sort) ? v.sort : [];
    gridApi?.current?.applyColumnState?.({
      state: sort.map((column, sortIndex) => ({ ...column, sortIndex })),
      defaultState: { sort: null, sortIndex: null },
    });
    gridView.setSort(sort);
    setSearchExtended(v?.q ?? "");
    const failed =
      isSurfaceShown("dataset.filtersSystem.pills") && Boolean(v?.failed);
    gridView.setFailedOnly(failed);
    if (failed)
      gridView.setFailedEvals(Array.isArray(v?.fevals) ? v.fevals : []);
    gridView.setDensity(v?.density ?? DEFAULT_DENSITY);
    gridView.setExpression(v?.expr || null);
    gridView.setStatuses(Array.isArray(v?.statuses) ? v.statuses : []);
    gridView.setTags(Array.isArray(v?.tags) ? v.tags : []);
    const store = useDevelopFilterStore.getState();
    if (Array.isArray(v?.filters) && v.filters.length) {
      store.setFilters(v.filters);
      store.setDevelopFilterOpen(true);
    } else store.resetFilters();
    refilter();
    // Splits keep their own capability. Their names become the split filter
    // once the split catalog has loaded (useDataGridUrlSync).
    const viewSplits =
      isSurfaceShown("dataset.splits") && typeof v?.splits === "string"
        ? v.splits
        : "";
    if (viewSplits || gridView.pendingSplitNames)
      gridView.setPendingSplitNames(viewSplits || null);
    // A view without permitted splits also drops a split filter an earlier
    // view resolved; the URL write-back would otherwise restore it.
    if (!viewSplits && gridView.splitIds?.length) gridView.setSplitIds([]);
    const next = new URLSearchParams(window.location.search);
    if (!keepLink) next.delete(GRID_VIEW_PARAM);
    if (viewSplits) next.set("splits", viewSplits);
    else next.delete("splits");
    if (!v?.expr) next.delete("expr");
    setSearchParams(next, { replace: true });
  };
  useEffect(() => {
    const encoded = searchParams.get(GRID_VIEW_PARAM);
    if (!encoded) {
      appliedLink.current = null;
      return;
    }
    const key = `${datasetId}:${encoded}`;
    if (
      !isSurfaceShown("dataset.savedViews") ||
      !gridView.readyAt ||
      !gridApi?.current ||
      appliedLink.current === key
    )
      return;
    const linked = readGridViewLink(encoded);
    const columns = gridApi.current.getColumns?.() ?? [];
    // Grid-ready precedes the asynchronous column definitions. Do not consume
    // the link until its sort can actually be applied to those columns.
    if (
      !linked ||
      columns.length === 0 ||
      linked.sort.some(
        ({ colId }) => !columns.some((column) => column.getColId() === colId),
      )
    )
      return;
    appliedLink.current = key;
    applyView(linked, true);
    patchLayout((current) => ({
      ...current,
      columns: linked.layout.columns,
    }));
  });
  // Reset view = search, Failed only, splits, density AND filters (the
  // amber dot counts filters, so Reset must clear them too).
  // Reset view also clears the expression and the pills (v2.1 g8).
  const resetView = () => {
    applyView({});
    gridView.setSplitIds([]);
  };

  return {
    gridView,
    search,
    setSearchExtended,
    activeFilters,
    viewState,
    modified,
    applyView,
    resetView,
  };
}
