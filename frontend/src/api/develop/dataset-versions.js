// Dataset versions (PRODUCT-SPEC §3.5, §6.2 N1-N5). One saved change-set =
// one version (`v7`); a tag (`golden-v4`) points at one version. URLs carry
// the version NUMBER (`?version=5`); the API takes the version id, so callers
// resolve numbers through the N1 list (`versionByNumber`).
import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { rowViewKeys } from "./row-view";
// L1 owed (the capability contract): dev has no dataset versions; on the real
// backend the N1 list is never read (it answers 404 on every dataset page).
import { datasetCaps } from "src/sections/develop/realBackendCaps";

export const datasetVersionsKey = (datasetId) => [
  "develop",
  "dataset-versions",
  datasetId,
];

export const versionChangesKey = (datasetId, versionId, against, rowId) => [
  "develop",
  "dataset-version-changes",
  datasetId,
  versionId,
  against ?? "parent",
  rowId ?? null,
];

/** react-query options for N1, shared by the hook and imperative callers. */
export const datasetVersionsQueryOptions = (datasetId) => ({
  queryKey: datasetVersionsKey(datasetId),
  queryFn: () => axios.get(endpoints.develop.versions.list(datasetId)),
  select: (res) => res?.data?.result?.versions ?? [],
  staleTime: 30 * 1000,
  enabled: Boolean(datasetId),
  // the interface contract: a re-test still running refreshes its result line.
  refetchInterval: (query) =>
    (query?.state?.data?.data?.result?.versions ?? []).some((v) =>
      (v.retests ?? []).some((r) => !r.finished),
    )
      ? 3000
      : false,
});

/** N1 · versions of a dataset, newest first. `data` is the array. */
export function useDatasetVersions(datasetId, options = {}) {
  const base = datasetVersionsQueryOptions(datasetId);
  return useQuery({
    ...base,
    ...options,
    enabled: datasetCaps().saveVersion && (options.enabled ?? base.enabled),
  });
}

export const latestOf = (versions) =>
  versions?.find((v) => v.is_latest) ?? versions?.[0] ?? null;

export const versionByNumber = (versions, number) =>
  number == null
    ? null
    : versions?.find((v) => v.number === Number(number)) ?? null;

/** The version holding a tag, if any. */
export const versionWithTag = (versions, tag) =>
  tag ? versions?.find((v) => (v.tags ?? []).includes(tag)) ?? null : null;

/** `?version=5` on the Data tab, or null. */
export function parseVersionParam(raw) {
  if (raw == null || raw === "") return null;
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

/**
 * The as-of state of the Data tab: `?version=` resolved against N1.
 * `isAsOf` is false for latest and for an unknown number.
 */
export function useAsOfVersion(datasetId) {
  const [searchParams] = useSearchParams();
  const asOfNumber = parseVersionParam(searchParams.get("version"));
  const query = useDatasetVersions(datasetId);
  const versions = query.data;
  return useMemo(() => {
    const latest = latestOf(versions);
    const version = versionByNumber(versions, asOfNumber);
    const isAsOf = Boolean(
      version && latest && version.number !== latest.number,
    );
    return {
      asOfNumber: isAsOf ? asOfNumber : null,
      version: isAsOf ? version : latest,
      latest,
      isAsOf,
      versions: versions ?? [],
      isLoading: query.isLoading,
    };
  }, [versions, asOfNumber, query.isLoading]);
}

/**
 * N4 · changes of `versionId` against `against` ("parent" | "latest" |
 * another version id), optionally for one row.
 */
export function useVersionChanges(
  datasetId,
  versionId,
  { against = "parent", rowId, enabled = true } = {},
) {
  return useQuery({
    queryKey: versionChangesKey(datasetId, versionId, against, rowId),
    queryFn: () =>
      axios.get(endpoints.develop.versions.changes(datasetId, versionId), {
        params: { against, ...(rowId ? { row_id: rowId } : {}), page: 0 },
      }),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(datasetId && versionId && enabled),
    staleTime: 30 * 1000,
  });
}

/**
 * Current state of a few rows at `versionId` (N3 `row_ids`), for the Save
 * dialog's change list: row labels, column names and split names.
 */
export function useVersionRows(datasetId, versionId, rowIds, options = {}) {
  const ids = [...new Set(rowIds ?? [])].sort();
  return useQuery({
    queryKey: ["develop", "dataset-version-rows", datasetId, versionId, ids],
    queryFn: () =>
      axios.get(endpoints.develop.versions.table(datasetId, versionId), {
        params: {
          row_ids: ids.join(","),
          page_size: 500,
          current_page_index: 0,
        },
      }),
    select: (res) => {
      const result = res?.data?.result ?? {};
      return {
        rows: result.table ?? [],
        columns: result.column_config ?? [],
        splits: result.metadata?.splits ?? [],
      };
    },
    enabled: Boolean(datasetId && versionId && ids.length),
    staleTime: 0,
    ...options,
  });
}

/** The message of a rejected API call (`{status:false, result:"…"}`). */
export const apiErrorMessage = (error, fallback = "Something went wrong.") =>
  (typeof error?.result === "string" && error.result) ||
  (typeof error?.message === "string" && error.message) ||
  fallback;

/** Everything a saved version can change on screen. */
export function invalidateAfterVersionChange(queryClient, datasetId) {
  queryClient.invalidateQueries({ queryKey: datasetVersionsKey(datasetId) });
  queryClient.invalidateQueries({
    queryKey: ["develop", "dataset-version-changes", datasetId],
  });
  queryClient.invalidateQueries({ queryKey: ["dataset-detail"] });
  queryClient.invalidateQueries({ queryKey: ["dataset-detail-average"] });
  // the interface contract: the dataset's table summary (status counts, schema) is per version.
  queryClient.invalidateQueries({
    queryKey: ["get-dataset-detail", datasetId],
  });
  queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
  queryClient.invalidateQueries({
    queryKey: ["develop", "dataset-runs", datasetId],
  });
  // the interface contract-8b: goldens a saved version regenerated no longer cite changed text.
  queryClient.invalidateQueries({ queryKey: ["kb-changes", datasetId] });
  // An open Row view re-reads the row at the new latest version.
  queryClient.invalidateQueries({ queryKey: rowViewKeys.all(datasetId) });
}

/**
 * N2 · save a version: `{base_version_id, description, tag, operations}`.
 * Resolves with `{version, row_id_map}`; a 409 rejects with
 * `error.isVersionConflict = true`.
 */
export function useSaveDatasetVersion(datasetId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body) => {
      try {
        const res = await axios.post(
          endpoints.develop.versions.list(datasetId),
          body,
        );
        return res?.data?.result;
      } catch (error) {
        // The axios interceptor rejects with `{...body, statusCode}`.
        if (
          error?.statusCode === 409 ||
          error?.result === "dataset_version_conflict"
        ) {
          const conflict = new Error("dataset_version_conflict");
          conflict.isVersionConflict = true;
          throw conflict;
        }
        // the interface contract Reject: 422 `{code: "schema_violation", violations}`.
        if (error?.result?.code === "schema_violation") {
          const rejected = new Error(
            error.result.message ?? "schema_violation",
          );
          rejected.schemaViolations = error.result.violations ?? [];
          throw rejected;
        }
        throw error;
      }
    },
    onSuccess: () => invalidateAfterVersionChange(queryClient, datasetId),
  });
}

/**
 * N5 · `{versionId, tag}` (set or move; null removes) and/or
 * `{versionId, description}`. Resolves with `{version, tag_moved_from}`.
 */
export function useUpdateDatasetVersion(datasetId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ versionId, ...patch }) => {
      const res = await axios.patch(
        endpoints.develop.versions.detail(datasetId, versionId),
        patch,
      );
      return res?.data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: datasetVersionsKey(datasetId),
      });
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-runs", datasetId],
      });
    },
  });
}
