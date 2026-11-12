import { create } from "zustand";
import axios, { endpoints } from "src/utils/axios";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import {
  datasetVersionsQueryOptions,
  latestOf,
  parseVersionParam,
  versionByNumber,
} from "src/api/develop/dataset-versions";

/**
 * Where the Data grid reads its rows from (PRODUCT-SPEC §3.5; owned by L3).
 *
 * tableRequestFor({ datasetId, versionNumber, versionId, compare, change,
 *                   params }) -> { url, params, asOf, latestView }
 * - Latest (no versionNumber): GET develops/{id}/get-dataset-table/.
 * - As of a version: GET datasets/{id}/versions/{version_id}/table/ (N3),
 *   read-only, user-data columns only. URLs carry the version number
 *   (`?version=5`); the API takes the version id from the N1 list. Without
 *   it the request stays on latest.
 * - `compare` ("since" | "in") and `change` ("added" | "modified" |
 *   "deleted") narrow the N3 rows (the AsOfBanner chips).
 *   `compare:"since", change:"added"` is the latest grid filtered to rows
 *   added after the version (`latestView: true`).
 * `params` are the get-dataset-table query keys (filters, sort, search,
 * page_size, current_page_index, ...); N3 accepts the same keys.
 *
 * The grid wiring (DevelopDataV2) uses `versionedQueryOptions` in place of
 * getDatasetQueryOptions, `useAsOfColumns` to drop generated columns and
 * editing, and the `version*ClassRules` / `versionCellTooltip` marks.
 */
export function tableRequestFor({
  datasetId,
  versionNumber = null,
  versionId = null,
  compare = null,
  change = null,
  params = {},
} = {}) {
  if (versionNumber == null || !versionId) {
    return {
      url: endpoints.develop.getDatasetDetail(datasetId),
      params,
      asOf: false,
    };
  }
  const narrowing = {
    ...(compare && compare !== "since" ? { compare } : {}),
    ...(change ? { compare: compare ?? "since", change } : {}),
  };
  return {
    url: endpoints.develop.versions.table(datasetId, versionId),
    params: { ...params, ...narrowing },
    asOf: true,
    ...(compare !== "in" && change === "added" ? { latestView: true } : {}),
  };
}

// ------------------------------------------------------------ view state
// Per dataset: which banner chip is on. `compare` "since" (vs latest) or
// "in" (vs the parent version); `change` null | "added" | "modified" |
// "deleted"; `addedSince` = a version number while the latest grid is
// filtered to "Rows added after v5".
const EMPTY_VIEW = Object.freeze({
  compare: "since",
  change: null,
  addedSince: null,
});

export const useAsOfViewStore = create((set) => ({
  views: {},
  setView: (datasetId, patch) =>
    set((s) => ({
      views: {
        ...s.views,
        [datasetId]: { ...(s.views[datasetId] ?? EMPTY_VIEW), ...patch },
      },
    })),
  resetView: (datasetId) =>
    set((s) => {
      const { [datasetId]: _gone, ...rest } = s.views;
      return { views: rest };
    }),
}));

export const getAsOfView = (datasetId) =>
  useAsOfViewStore.getState().views[datasetId] ?? EMPTY_VIEW;

export const useAsOfView = (datasetId) =>
  useAsOfViewStore((s) => s.views[datasetId] ?? EMPTY_VIEW);

/** `?version=` of the current page, read at call time (grid datasource). */
export const versionParamNow = () =>
  typeof window === "undefined"
    ? null
    : parseVersionParam(
        new URLSearchParams(window.location.search).get("version"),
      );

/**
 * What the grid should read right now, or null for plain latest:
 * `{versionNumber, compare, change}`.
 */
export function currentTableView(datasetId, versionNumber = versionParamNow()) {
  const view = getAsOfView(datasetId);
  if (versionNumber != null)
    return { versionNumber, compare: view.compare, change: view.change };
  if (view.addedSince != null)
    return {
      versionNumber: view.addedSince,
      compare: "since",
      change: "added",
    };
  return null;
}

/**
 * Drop-in for getDatasetQueryOptions inside the grid datasource: latest
 * reads stay on get-dataset-table; an as-of view (or the "added since"
 * filter) reads N3. The version id is resolved from N1 inside the query, so
 * a cold `?version=5` load never shows latest rows first. Keys start with
 * "dataset-detail" so refreshGrid invalidates them too.
 */
export function versionedQueryOptions(
  queryClient,
  datasetId,
  pageNumber,
  filters,
  sort,
  search,
  extra = {},
) {
  const view = currentTableView(datasetId);
  if (!view)
    return getDatasetQueryOptions(
      datasetId,
      pageNumber,
      filters,
      sort,
      search,
      extra,
    );
  const pageSize = extra?.pageSize || 30;
  const { pageSize: _pageSize, ...rest } = extra ?? {};
  const params = {
    current_page_index: pageNumber,
    filters: JSON.stringify(filters ?? []),
    sort: JSON.stringify(sort ?? []),
    ...(search && {
      search: JSON.stringify({ key: search, type: ["text", "image", "audio"] }),
    }),
    page_size: pageSize,
  };
  return {
    queryKey: [
      "dataset-detail",
      datasetId,
      "version",
      view.versionNumber,
      view.compare,
      view.change,
      pageNumber,
      filters,
      sort,
      search,
    ],
    queryFn: async () => {
      const res = await queryClient.fetchQuery(
        datasetVersionsQueryOptions(datasetId),
      );
      const versions = res?.data?.result?.versions ?? [];
      const latest = latestOf(versions);
      const version = versionByNumber(versions, view.versionNumber);
      const onLatest =
        !version || (version.number === latest?.number && !view.change);
      const req = tableRequestFor({
        datasetId,
        versionNumber: onLatest ? null : view.versionNumber,
        versionId: onLatest ? null : version.id,
        compare: view.compare,
        change: view.change,
        params,
      });
      return axios.get(req.url, { params: req.params });
    },
    staleTime: Infinity,
    ...rest,
  };
}

// ------------------------------------------------------------ grid marks
const changeOf = (params) => params?.data?.row_meta?.change ?? null;
const fieldOf = (params) =>
  params?.colDef?.field ?? params?.column?.getColId?.();

/** The cell's value changed between this version and the other side. */
export function isChangedCell(params) {
  const change = changeOf(params);
  return (
    change?.kind === "modified" &&
    (change.changed_column_ids ?? []).includes(fieldOf(params))
  );
}

export const VERSION_CELL_CLASS = "dsv-cell-changed";
export const VERSION_ROW_DELETED_CLASS = "dsv-row-deleted";

/** defaultColDef.cellClassRules: dashed outline on changed cells. */
export const versionCellClassRules = {
  [VERSION_CELL_CLASS]: isChangedCell,
};

/** rowClassRules: struck-through rows deleted later / in this version. */
export const versionRowClassRules = {
  [VERSION_ROW_DELETED_CLASS]: (params) =>
    ["deleted_later", "deleted"].includes(changeOf(params)?.kind),
};

const quote = (value) => {
  if (value == null || value === "") return "empty";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const flat = text.replace(/\s+/g, " ").trim();
  return `“${flat.length > 160 ? `${flat.slice(0, 159)}…` : flat}”`;
};

/**
 * defaultColDef.tooltipValueGetter: the other side of a changed cell —
 * "Latest (v6): “…”" when comparing since this version, "Before (v4): “…”"
 * when showing what this version changed; "Deleted in v6" on deleted rows.
 */
export function versionCellTooltip(params) {
  const change = changeOf(params);
  if (!change) return undefined;
  if (change.kind === "deleted_later")
    return `Deleted after this version${change.against ? ` · not in v${change.against}` : ""}`;
  if (change.kind === "deleted") return `Deleted in v${change.version}`;
  if (!isChangedCell(params)) return undefined;
  const field = fieldOf(params);
  if (change.latest_values && field in change.latest_values)
    return `Latest (v${change.against}): ${quote(change.latest_values[field])}`;
  if (change.previous_values && field in change.previous_values)
    return `Before (v${change.against}): ${quote(change.previous_values[field])}`;
  return undefined;
}

// Generated columns: Run Prompt, evals, annotations and dynamic columns.
// They are not versioned (C28), so as-of views leave them out.
export const GENERATED_ORIGIN_TYPES = [
  "run_prompt",
  "evaluation",
  "evaluation_reason",
  "optimization",
  "annotation_label",
  "retrieval",
  "extracted_entities",
  "extracted_json",
  "python_code",
  "classification",
  "api_call",
  "conditional",
];

/** True for user-data columns (the only columns a version holds, C28). */
export const isUserDataColumn = (column) =>
  !GENERATED_ORIGIN_TYPES.includes(column?.origin_type ?? column?.originType);
