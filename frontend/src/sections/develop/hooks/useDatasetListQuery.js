// The dataset list through the the interface contract query (the interface contract §5 A2-A4, A6):
// `GET /model-hub/datasets/query/` with the list's search, sort and page
// plus the structured filter (`f`), Group by (`group`) and starred first.
// The rows are get-datasets' own (normalised the same way); the answer
// adds groups, folders, facets, the metadata key types and the left-out
// counts. useDatasetsList
// stays as it is for its other callers.
//
// L1 (the capability contract): on the real backend there is no datasets/query/.
// The list reads dev's `GET develops/get-datasets/` with only the fields its
// strict DatasetListQuerySerializer takes (page, page_size <= 100,
// search_text, sort) and only sort fields it orders by; filters, Group by,
// folders, stars, facets and the running poll are off (realBackendCaps).
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { RUNNING_POLL_MS, normalizeDatasetRow } from "./useDatasetsList";
import {
  realListSortFields,
  datasetCaps,
} from "src/sections/develop/realBackendCaps";

/** get-datasets' page_size bound (DatasetListQuerySerializer max_value). */
export const REAL_MAX_PAGE_SIZE = 100;

/**
 * The get-datasets params from the list's view state. Pure. Never sends a
 * field dev's strict serializer refuses (filters, group_by, starred_first);
 * a sort field dev cannot order by falls back to newest first.
 */
export function realDatasetListParams({
  page = 0,
  pageSize = 25,
  search = null,
  sortBy = null,
  sortOrder = "desc",
  starred = false,
  folderPath,
} = {}) {
  const p = Number(page);
  const size = Number(pageSize);
  const params = {
    page: Number.isInteger(p) && p > 0 ? p : 0,
    page_size: Math.min(
      REAL_MAX_PAGE_SIZE,
      Number.isInteger(size) && size > 0 ? size : 25,
    ),
  };
  if (starred === true) params.starred = true;
  if (typeof folderPath === "string") params.folder_path = folderPath;
  const q = typeof search === "string" ? search.trim() : "";
  if (q) params.search_text = q;
  const known = realListSortFields().includes(sortBy);
  params.sort = JSON.stringify([
    {
      column_id: known ? sortBy : "created_at",
      type: known && sortOrder === "asc" ? "ascending" : "descending",
    },
  ]);
  return params;
}

/** get-datasets' answer in the list's shape (no groups, folders, facets). Pure. */
export function realDatasetListResult(result) {
  return {
    items: (result?.datasets ?? []).map(normalizeDatasetRow),
    total: result?.total_count ?? 0,
    totalPages: result?.total_pages ?? 0,
    groups: null,
    folders: [],
    facets: {},
    leftOut: {},
  };
}

/**
 * The request's params from the list's view state. Pure.
 * `filters` is the compact JSON the `f` URL key holds (listFilterModel
 * serializeFilters); `sortBy`/`sortOrder` as useDatasetsList takes them.
 */
export function datasetQueryParams({
  page = 0,
  pageSize = 25,
  search = null,
  sortBy = null,
  sortOrder = "desc",
  filters = "",
  groupBy = "",
  starredFirst = true,
} = {}) {
  const params = { page, page_size: pageSize };
  if (search) params.search_text = search;
  if (sortBy)
    params.sort = JSON.stringify([
      {
        column_id: sortBy,
        type: sortOrder === "asc" ? "ascending" : "descending",
      },
    ]);
  if (filters) params.filters = filters;
  if (groupBy) params.group_by = groupBy;
  if (!starredFirst) params.starred_first = "false";
  return params;
}

/** The answer, normalised for the list page. Pure. */
export function datasetQueryResult(result) {
  const groups = Array.isArray(result?.groups)
    ? result.groups.map((g) => ({
        ...g,
        datasets: (g.datasets ?? []).map(normalizeDatasetRow),
      }))
    : null;
  return {
    items: (result?.datasets ?? []).map(normalizeDatasetRow),
    total: result?.total_count ?? 0,
    totalPages: result?.total_pages ?? 0,
    groups,
    folders: result?.folders ?? [],
    facets: result?.facets ?? {},
    // Each metadata key typed over every listed dataset (whatever the
    // search); absent when the answer has none.
    ...(result?.metadata_types ? { metadataTypes: result.metadata_types } : {}),
    leftOut: result?.left_out ?? {},
  };
}

/** True while a listed dataset has a running experiment (poll then). */
export const anyRunning = (data) =>
  [
    ...(data?.items ?? []),
    ...(data?.groups ?? []).flatMap((g) => g.datasets),
  ].some((d) => d?.last_experiment?.status === "Running");

/**
 * The 400 sentence of a refused query (shown under the chips). The app's
 * axios rejects with the body plus `statusCode` (src/utils/axios.js); a raw
 * axios error carries `response` (v2.1 g8, org:filter).
 */
export const queryErrorSentence = (error) => {
  const status = error?.statusCode ?? error?.response?.status;
  if (status !== 400) return null;
  const sentence = error?.response?.data?.result ?? error?.result ?? null;
  return typeof sentence === "string" && sentence ? sentence : null;
};

/** The list query's options for this build (pure; real or the interface contract). */
export function datasetListQueryConfig(args = {}, real = datasetCaps().real) {
  if (real) {
    const params = realDatasetListParams({
      ...args,
      starred: datasetCaps(real).listStars && args.starred === true,
      folderPath: datasetCaps(real).listFolders ? args.folderPath : undefined,
    });
    return {
      queryKey: ["datasets", "list", "get-datasets", params],
      queryFn: async () => {
        const { data } = await axios.get(endpoints.develop.getDatasets(), {
          params,
        });
        return realDatasetListResult(data?.result);
      },
      keepPreviousData: true,
      // No running poll: get-datasets carries no experiment status.
      refetchInterval: false,
    };
  }
  const params = datasetQueryParams(args);
  return {
    queryKey: ["datasets", "query", params],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.develop.w9.datasetsQuery, {
        params,
      });
      return datasetQueryResult(data?.result);
    },
    keepPreviousData: true,
    // As the list: poll while an experiment runs (paused when hidden).
    refetchInterval: (query) =>
      anyRunning(query?.state?.data) ? RUNNING_POLL_MS : false,
  };
}

export function useDatasetListQuery(args = {}, options = {}) {
  return useQuery({ ...datasetListQueryConfig(args), ...options });
}
