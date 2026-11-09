import { useEffect, useMemo, useRef } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { useSearchParams } from "react-router-dom";
import { getRandomId } from "src/utils/utils";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
} from "src/sections/develop-detail/states";
import { transformFilter, validateFilter } from "../DevelopFilters/common";
import {
  DEFAULT_DENSITY,
  DEFAULT_SYSTEM_COLS,
  normalizeDensity,
  partitionInboundFilters,
  readStored,
  splitIdsFromNames,
  splitNamesParam,
  storageKey,
  systemFilters,
  writeStored,
} from "./dataGridView";

// Per-dataset view state of the versioned Data grid: the toolbar's system
// toggles (Failed only, Splits), density and system-column visibility.
// The URL (`failed=1`, `splits=hard,smoke`) and localStorage
// (`fa.ds.<id>.density`, `fa.ds.<id>.systemCols`) mirror it.

// R2 one-shot system filters (set from a `filters=` drill-down or a page).
const R2_SYSTEM_DEFAULTS = {
  rowIds: [],
  rowIdsLabel: null,
  sourceKind: null,
  sourceProject: null,
  sourceProjectName: null,
  invalidOnly: false,
  // W3-3: `Status is` (draft / needs_review / approved / archived).
  statuses: [],
  // W3-1: `Tags` (row metadata.tags).
  tags: [],
  // W4 §9 (M6): one live collaboration view `{key, value, label}`.
  collab: null,
  // W5 grid seam G2: `system:expression` (URL `expr=`).
  expression: null,
};

const initialView = (datasetId) => ({
  failedOnly: false,
  failedEvalIds: [],
  cellErrorOnly: false,
  sort: [],
  splitIds: [],
  ...R2_SYSTEM_DEFAULTS,
  density: normalizeDensity(
    readStored(storageKey(datasetId, "density"), DEFAULT_DENSITY),
  ),
  systemCols: {
    ...DEFAULT_SYSTEM_COLS,
    ...readStored(storageKey(datasetId, "systemCols"), {}),
  },
});

export const useDataGridViewStore = create((set, get) => ({
  views: {},
  view: (datasetId) => get().views[datasetId] ?? initialView(datasetId),
  patch: (datasetId, patch) =>
    set((s) => ({
      views: {
        ...s.views,
        [datasetId]: {
          ...(s.views[datasetId] ?? initialView(datasetId)),
          ...patch,
        },
      },
    })),
}));

export const getDataGridView = (datasetId) =>
  useDataGridViewStore.getState().view(datasetId);

/**
 * The full API filter list the Data grid applies right now: the filter
 * panel's column filters, then the toolbar's system filters. The Row view
 * navigation (get-row-data) must send the same list so "12 of 71 filtered"
 * matches the grid.
 */
export function getDataGridFilters(datasetId) {
  const columnFilters = useDevelopFilterStore
    .getState()
    .filters.filter(validateFilter)
    .map(transformFilter);
  return [...columnFilters, ...systemFilters(getDataGridView(datasetId))];
}

export const getDataGridSearch = () => useDevelopSearchStore.getState().search;

/** Reactive view + setters for one dataset. */
export function useDataGridView(datasetId) {
  const { view, patch } = useDataGridViewStore(
    useShallow((s) => ({
      view: s.views[datasetId],
      patch: s.patch,
    })),
  );
  return useMemo(() => {
    const v = view ?? initialView(datasetId);
    return {
      ...v,
      setSort: (sort) => patch(datasetId, { sort }),
      setReady: () => patch(datasetId, { readyAt: Date.now() }),
      setFailedOnly: (failedOnly) =>
        patch(
          datasetId,
          failedOnly ? { failedOnly } : { failedOnly, failedEvalIds: [] },
        ),
      // R3 (DG#12): narrow Failed only to some eval columns ([] = any).
      setFailedEvals: (failedEvalIds) =>
        patch(datasetId, {
          failedEvalIds: failedEvalIds ?? [],
          failedOnly: true,
        }),
      setCellErrorOnly: (cellErrorOnly) => patch(datasetId, { cellErrorOnly }),
      setSplitIds: (splitIds) => patch(datasetId, { splitIds }),
      // A saved or linked view names its splits. useDataGridUrlSync turns the
      // names into splitIds once the split catalog is known.
      setPendingSplitNames: (pendingSplitNames) =>
        patch(datasetId, { pendingSplitNames: pendingSplitNames || null }),
      // R2 system filters (`system:row_ids`, `source_kind`,
      // `source_project`, `invalid`).
      setRowIds: (rowIds, label = null) =>
        patch(datasetId, { rowIds: rowIds ?? [], rowIdsLabel: label }),
      setSourceKind: (sourceKind) => patch(datasetId, { sourceKind }),
      setSourceProject: (sourceProject, name = null) =>
        patch(datasetId, { sourceProject, sourceProjectName: name }),
      setInvalidOnly: (invalidOnly) => patch(datasetId, { invalidOnly }),
      // `hydratedAt` is in the grid's re-query key, so a status change
      // re-reads the rows.
      setStatuses: (statuses) =>
        patch(datasetId, { statuses: statuses ?? [], hydratedAt: Date.now() }),
      setTags: (tags) =>
        patch(datasetId, { tags: tags ?? [], hydratedAt: Date.now() }),
      // W4 §9 (M6): a live view the server answers at query time (it
      // replaces a one-off rowIds set from the same link).
      setCollabView: (collab) =>
        patch(datasetId, {
          collab: collab ?? null,
          ...(collab ? { rowIds: [], rowIdsLabel: null } : {}),
          hydratedAt: Date.now(),
        }),
      // W5 grid seam G2: the expression filter (re-queries the grid).
      setExpression: (expression) =>
        patch(datasetId, {
          expression: expression || null,
          hydratedAt: Date.now(),
        }),
      setDensity: (density) => {
        const d = normalizeDensity(density);
        writeStored(storageKey(datasetId, "density"), d);
        patch(datasetId, { density: d });
      },
      setSystemCol: (key, visible) => {
        const systemCols = { ...v.systemCols, [key]: visible };
        writeStored(storageKey(datasetId, "systemCols"), systemCols);
        patch(datasetId, { systemCols });
      },
    };
  }, [view, patch, datasetId]);
}

/** Store-format filter row (useDevelopFilterStore) from an API filter. */
const toStoreFilter = (f) => {
  const c = f.filter_config ?? {};
  return {
    id: getRandomId(),
    columnId: f.column_id,
    filterConfig: {
      filterType: c.filter_type === "categorical" ? "text" : c.filter_type,
      filterOp: c.filter_op,
      filterValue: c.filter_value ?? "",
    },
    _meta: { parentProperty: "" },
  };
};

/**
 * URL ⇄ view sync, mounted once by the grid. On mount it reads `failed`,
 * `splits` and the one-shot `filters` drill-down (then drops `filters`);
 * afterwards it writes `failed` / `splits` back whenever the view changes.
 */
export function useDataGridUrlSync(datasetId, splits, { enabled = true } = {}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const { failedOnly, splitIds, density, failedEvalIds, expression } =
    useDataGridView(datasetId);
  const search = useDevelopSearchStore((s) => s.search);
  const pendingSplitNames = useDataGridViewStore(
    (s) => s.views[datasetId]?.pendingSplitNames ?? null,
  );
  const hydrated = useRef(false);
  const splitsReady = Boolean(splits) || !searchParams.get("splits");

  // Only a split-name link needs the split catalog. Other filters must also
  // hydrate when splits are unavailable or their capability is disabled.
  useEffect(() => {
    if (!enabled || hydrated.current || !splitsReady) return;
    hydrated.current = true;
    const patch = { cellErrorOnly: false, ...R2_SYSTEM_DEFAULTS };
    const failedParam = searchParams.get("failed");
    patch.failedOnly = failedParam === "1" || failedParam === "true";
    patch.splitIds = splitIdsFromNames(searchParams.get("splits"), splits);
    // R3 (DG#1): search, density and per-eval Failed only live in the URL.
    const fevals = searchParams.get("fevals");
    patch.failedEvalIds = fevals ? fevals.split(",").filter(Boolean) : [];
    const urlDensity = searchParams.get("density");
    if (urlDensity) patch.density = normalizeDensity(urlDensity);
    const q = searchParams.get("q");
    if (q) {
      useDevelopSearchStore.getState().setSearch(q);
      patch.hydratedAt = Date.now(); // re-query with the pasted search
    }
    // W5 grid seam G2: the expression filter rides the URL (`expr=`).
    const expr = searchParams.get("expr");
    if (expr) {
      patch.expression = expr;
      patch.hydratedAt = Date.now();
    }
    const inbound = searchParams.get("filters");
    if (inbound) {
      let list = [];
      try {
        list = JSON.parse(inbound);
      } catch {
        list = [];
      }
      const parts = partitionInboundFilters(list);
      if (parts.failedOnly) patch.failedOnly = true;
      if (parts.cellErrorOnly) patch.cellErrorOnly = true;
      if (parts.splitIds.length) patch.splitIds = parts.splitIds;
      if (parts.rowIds.length) {
        patch.rowIds = parts.rowIds;
        patch.rowIdsLabel = parts.rowIdsLabel;
      }
      if (parts.sourceKind) patch.sourceKind = parts.sourceKind;
      if (parts.sourceProject) {
        patch.sourceProject = parts.sourceProject;
        patch.sourceProjectName = parts.sourceProjectName;
      }
      if (parts.invalidOnly) patch.invalidOnly = true;
      // L7c g1 (url:grid-filters): `hydratedAt` is the grid's re-query key
      // for these pills (like their setters), so a link's Status / Tags /
      // live view filters the rows it names, not only the pill.
      if (parts.statuses?.length) {
        patch.statuses = parts.statuses;
        patch.hydratedAt = Date.now();
      }
      if (parts.tags?.length) {
        patch.tags = parts.tags;
        patch.hydratedAt = Date.now();
      }
      if (parts.collab) {
        patch.collab = parts.collab;
        patch.hydratedAt = Date.now();
      }
      if (parts.expression) {
        patch.expression = parts.expression;
        patch.hydratedAt = Date.now();
      }
      if (parts.columnFilters.length) {
        useDevelopFilterStore
          .getState()
          .setFilters(parts.columnFilters.map(toStoreFilter));
        // Changes the grid's filter key, so a drill-down that only set
        // column filters still re-queries the grid.
        patch.hydratedAt = Date.now();
      }
      const next = new URLSearchParams(window.location.search);
      next.delete("filters");
      setSearchParams(next, { replace: true });
    }
    useDataGridViewStore.getState().patch(datasetId, patch);
  }, [enabled, splitsReady, splits, searchParams, setSearchParams, datasetId]);

  // Write back.
  useEffect(() => {
    if (!enabled || !hydrated.current) return;
    const byId = Object.fromEntries((splits ?? []).map((s) => [s.id, s]));
    const next = new URLSearchParams(window.location.search);
    const want = {
      failed: failedOnly ? "1" : null,
      splits: splitNamesParam(splitIds, byId),
      fevals:
        failedOnly && failedEvalIds?.length ? failedEvalIds.join(",") : null,
      density: density && density !== DEFAULT_DENSITY ? density : null,
      q: search?.trim() ? search.trim() : null,
      expr: expression || null,
    };
    let changed = false;
    Object.entries(want).forEach(([key, value]) => {
      if ((next.get(key) ?? null) === value) return;
      changed = true;
      if (value == null) next.delete(key);
      else next.set(key, value);
    });
    if (changed) setSearchParams(next, { replace: true });
  }, [
    enabled,
    failedOnly,
    splitIds,
    splits,
    setSearchParams,
    density,
    failedEvalIds,
    search,
    expression,
  ]);

  // A saved or linked view's split names filter the grid once the split
  // catalog is known; the write-back above then mirrors them in the URL.
  useEffect(() => {
    if (!enabled || pendingSplitNames == null || !splits) return;
    useDataGridViewStore.getState().patch(datasetId, {
      splitIds: splitIdsFromNames(pendingSplitNames, splits),
      pendingSplitNames: null,
    });
  }, [enabled, pendingSplitNames, splits, datasetId]);
}
