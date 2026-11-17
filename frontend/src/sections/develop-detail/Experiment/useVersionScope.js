// v2.1 (data:rows:split, door:run-on-rows:version-history, data:version,
// review:empty-input-warning): the Data step and Review read the pinned
// dataset version, not the latest. Split chips count each split's rows in
// that version (the same versioned table request the sample and the run
// read: tableRequestFor + the `system:splits` filter), and the Review's
// empty-input warning reads the rows in scope of that version.
import { useQueries, useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { tableRequestFor } from "src/sections/develop-detail/DataTab/Versions/versionedSource";
import { splitsFilter } from "src/api/develop/experiment-create";

/** The pinned version's `{number, id}` or nulls for the latest (pure). */
export function pinnedVersionOf(versions = [], versionId = null) {
  const list = versions ?? [];
  const latest = list.reduce(
    (m, v) => (Number(v?.number) > Number(m?.number ?? -1) ? v : m),
    null,
  );
  const v = list.find((x) => x?.id === versionId) ?? null;
  if (!v || !latest || v.number === latest.number)
    return { versionNumber: null, versionId: null };
  return { versionNumber: v.number, versionId: v.id };
}

/** The table request of `splitIds` at a version (pure). */
export function scopeRequestOf({
  datasetId,
  pinned,
  splitIds = [],
  pageSize = 1,
}) {
  return tableRequestFor({
    datasetId,
    versionNumber: pinned?.versionNumber ?? null,
    versionId: pinned?.versionId ?? null,
    params: {
      filters: JSON.stringify(splitsFilter(splitIds)),
      page_size: pageSize,
      current_page_index: 0,
    },
  });
}

/**
 * `{[splitId]: rows in the pinned version}`; null for the latest version
 * (the split list's own counts are the latest's).
 */
export function useVersionSplitCounts(datasetId, versions, versionId, splits) {
  const pinned = pinnedVersionOf(versions, versionId);
  const on = Boolean(datasetId && pinned.versionId);
  const list = on ? splits ?? [] : [];
  const results = useQueries({
    queries: list.map((s) => {
      const { url, params } = scopeRequestOf({
        datasetId,
        pinned,
        splitIds: [s.id],
      });
      return {
        queryKey: [
          "experiment-create",
          "version-split-count",
          datasetId,
          pinned.versionId,
          s.id,
        ],
        queryFn: () => axios.get(url, { params }),
        select: (res) => res?.data?.result?.metadata?.total_rows ?? null,
        staleTime: 30_000,
      };
    }),
  });
  if (!on) return null;
  const out = {};
  list.forEach((s, i) => {
    const n = results[i]?.data;
    if (n != null) out[s.id] = n;
  });
  return out;
}

/** The rows of `splitIds` in the pinned version (Review's warnings). */
export function useVersionScopeRows({
  datasetId,
  versions,
  versionId,
  splitIds = [],
  all = false,
  pageSize = 50,
}) {
  const pinned = pinnedVersionOf(versions, versionId);
  return useQuery({
    queryKey: [
      "experiment-create",
      "review-split-rows",
      datasetId,
      pinned.versionId ?? "latest",
      [...splitIds].sort().join(","),
      all ? "all" : "splits",
      pageSize,
    ],
    queryFn: () => {
      const { url, params } = scopeRequestOf({
        datasetId,
        pinned,
        splitIds,
        pageSize,
      });
      return axios.get(url, { params });
    },
    select: (res) => res?.data?.result?.table ?? [],
    enabled: Boolean(datasetId) && (splitIds.length > 0 || all),
    staleTime: 30_000,
  });
}
