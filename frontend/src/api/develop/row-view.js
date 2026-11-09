// Dataset Row view reads (PRODUCT-SPEC §3.4, §6.2 N7/N8; §6.1 get-row-data).
// One row = one dataset row id (= example_id, stable across versions).
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { isPendingRowId } from "src/sections/develop-detail/DataTab/Versions/pendingRows";
import { parseVersionParam } from "./dataset-versions";

// A pending new row (`new-…`, change-set `add_row`) exists only in the
// browser until a version is saved: never ask the server about it.
const serverRow = (datasetId, rowId) =>
  Boolean(datasetId && rowId) && !isPendingRowId(rowId);

export const rowViewKeys = {
  all: (datasetId) => ["develop", "row-view", datasetId],
  row: (datasetId, rowId, nav) => [
    ...rowViewKeys.all(datasetId),
    "row",
    rowId,
    nav,
  ],
  runs: (datasetId, rowId, params) => [
    ...rowViewKeys.all(datasetId),
    "runs",
    rowId,
    params,
  ],
  activity: (datasetId, rowId) => [
    ...rowViewKeys.all(datasetId),
    "activity",
    rowId,
  ],
  splits: (datasetId) => ["develop", "dataset-splits", datasetId],
};

const statusOf = (error) => error?.statusCode ?? error?.response?.status;

/**
 * integration: the page's `?version=N` (the as-of view), read at call time, or
 * null. Row reads (N7 runs, row comments) pass it so a row deleted later
 * still answers as of that version.
 */
export const asOfVersionParam = () =>
  typeof window === "undefined"
    ? null
    : parseVersionParam(
        new URLSearchParams(window.location.search).get("version"),
      );

// Size of the grid's current set (for `– of 71` when the row is outside it);
// as of a version, the as-of table's (N3) with the same compare / change.
async function setTotal(datasetId, nav) {
  try {
    const url = nav.versionId
      ? endpoints.develop.versions.table(datasetId, nav.versionId)
      : endpoints.develop.getDatasetDetail(datasetId);
    const res = await axios.get(url, {
      params: {
        current_page_index: 0,
        page_size: 1,
        filters: JSON.stringify(nav.filters ?? []),
        sort: JSON.stringify(nav.sort ?? []),
        ...(nav.search && { search: JSON.stringify(nav.search) }),
        ...(nav.versionId && nav.compare && { compare: nav.compare }),
        ...(nav.versionId && nav.change && { change: nav.change }),
      },
    });
    return res.data?.result?.metadata?.total_rows ?? null;
  } catch {
    return null;
  }
}

/**
 * POST get-row-data for one row inside the grid's navigation set. When the
 * row is outside the set (404 with filters on), the row itself is read
 * without filters and `outside` is set; `notFound` when it is gone.
 * `prev` is null when the server sends none (dev's get-row-data answers
 * only `next`): not known, so the Row view asks the grid instead.
 * @returns {{current: object|null, prev: string[]|null, next: string[],
 *   position: {index: number|null, total: number|null}, outside: boolean,
 *   notFound: boolean}}
 */
export async function fetchRowInView(datasetId, rowId, nav = {}) {
  const body = {
    row_id: rowId,
    filters: nav.filters ?? [],
    sort: nav.sort ?? [],
    ...(nav.search && { search: nav.search }),
    // integration: as of a version, the as-of view's narrowing too (N3 keys).
    ...(nav.versionId && { version_id: nav.versionId }),
    ...(nav.versionId && nav.compare && { compare: nav.compare }),
    ...(nav.versionId && nav.change && { change: nav.change }),
  };
  // The row itself, outside the narrowed set (same version).
  const whole = {
    row_id: rowId,
    filters: [],
    sort: [],
    ...(nav.versionId && { version_id: nav.versionId }),
  };
  const post = (b) =>
    axios.post(endpoints.develop.getRowData(datasetId), b).then(async (r) => {
      const result = r.data?.result ?? {};
      if (
        result.current &&
        !nav.versionId &&
        isSurfaceShown("dataset.splits")
      ) {
        const membership = await axios.get(
          `${endpoints.develop.splits.list(datasetId)}membership/`,
          { params: { row_id: rowId } },
        );
        result.current = {
          ...result.current,
          row_meta: {
            ...result.current.row_meta,
            split_ids: membership.data.result.split_ids,
          },
        };
      }
      return {
        current: result.current ?? null,
        prev: result.prev?.row_id ?? null,
        next: result.next?.row_id ?? [],
        position: {
          index: Number.isFinite(result.position?.index)
            ? result.position.index
            : null,
          total: result.position?.total ?? null,
        },
        outside: false,
        notFound: false,
      };
    });
  try {
    return await post(body);
  } catch (error) {
    if (statusOf(error) !== 404) throw error;
    const narrowed =
      body.filters.length > 0 ||
      Boolean(body.search) ||
      body.sort.length > 0 ||
      Boolean(body.change);
    if (!narrowed) {
      return {
        current: null,
        prev: [],
        next: [],
        position: { index: null, total: null },
        outside: false,
        notFound: true,
      };
    }
    try {
      const [alone, total] = await Promise.all([
        post(whole),
        setTotal(datasetId, nav),
      ]);
      return {
        ...alone,
        prev: [],
        next: [],
        position: { index: null, total },
        outside: true,
      };
    } catch (inner) {
      if (statusOf(inner) !== 404) throw inner;
      return {
        current: null,
        prev: [],
        next: [],
        position: { index: null, total: null },
        outside: false,
        notFound: true,
      };
    }
  }
}

/** The open row plus its neighbours in the grid's navigation set. */
export function useRowInView(datasetId, rowId, nav, options = {}) {
  return useQuery({
    queryKey: rowViewKeys.row(datasetId, rowId, nav),
    queryFn: () => fetchRowInView(datasetId, rowId, nav),
    enabled: serverRow(datasetId, rowId),
    meta: { errorHandled: true },
    retry: false,
    staleTime: 0,
    ...options,
  });
}

/**
 * N7 · GET datasets/{id}/rows/{row_id}/runs/?scope=&sort=&version=
 * `data` = `{summary, metrics, runs}`. As of a version (`?version=N` on the
 * page) the row is read as of N (integration: a row deleted later has its runs).
 */
export function useRowRuns(
  datasetId,
  rowId,
  { scope = "all", sort = "newest" } = {},
  options = {},
) {
  const version = asOfVersionParam();
  const params = { scope, sort, ...(version != null && { version }) };
  return useQuery({
    queryKey: rowViewKeys.runs(datasetId, rowId, params),
    queryFn: () =>
      axios.get(endpoints.develop.datasetRows.runs(datasetId, rowId), {
        params,
      }),
    select: (res) => res.data?.result,
    enabled: serverRow(datasetId, rowId),
    placeholderData: keepPreviousData,
    meta: { errorHandled: true },
    ...options,
  });
}

/** N8 · GET datasets/{id}/rows/{row_id}/activity/ → `{events}`. */
export function useRowActivity(datasetId, rowId, options = {}) {
  return useQuery({
    queryKey: rowViewKeys.activity(datasetId, rowId),
    queryFn: () =>
      axios.get(endpoints.develop.datasetRows.activity(datasetId, rowId)),
    select: (res) => res.data?.result?.events ?? [],
    enabled: serverRow(datasetId, rowId),
    meta: { errorHandled: true },
    ...options,
  });
}

/**
 * N6 · GET datasets/{id}/splits/ (read only; L2 owns the split writes).
 * `data` = `{splits: [{id, name, color, description, row_count}], byId}`.
 */
export function useDatasetSplitList(datasetId, options = {}) {
  return useQuery({
    queryKey: rowViewKeys.splits(datasetId),
    queryFn: () => axios.get(endpoints.develop.splits.list(datasetId)),
    select: (res) => {
      const splits = res.data?.result?.splits ?? [];
      return {
        splits,
        byId: Object.fromEntries(splits.map((s) => [s.id, s])),
      };
    },
    enabled: Boolean(datasetId),
    staleTime: 30 * 1000,
    meta: { errorHandled: true },
    ...options,
  });
}
