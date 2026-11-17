// W5-5 (W5-SCOPE §7 C2): leaderboards over the API, plus the editor's
// options read from R1 per chosen dataset (columns = their metric defs; the
// runs catalogue = their runs), so the editor validates `Runs where` with
// the same language and catalogue the server uses.
import { useMemo } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { datasetRunsParams } from "src/api/develop/runs";
import { runsCatalogFor } from "src/sections/common/filters/runsScope";
import { columnOptions } from "./leaderboardModel";

export const LEADERBOARDS_KEY = ["w5", "leaderboards"];

export function useLeaderboardsList() {
  return useQuery({
    queryKey: [...LEADERBOARDS_KEY, "list"],
    queryFn: () => axios.get(endpoints.develop.w5.leaderboards),
    select: (res) => res?.data?.result?.results ?? [],
  });
}

export function useLeaderboard(id) {
  return useQuery({
    queryKey: [...LEADERBOARDS_KEY, "one", id],
    queryFn: () => axios.get(endpoints.develop.w5.leaderboard(id)),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(id),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: LEADERBOARDS_KEY });
}

export function useCreateLeaderboard() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body) => axios.post(endpoints.develop.w5.leaderboards, body),
    onSuccess: done,
  });
}

export function useUpdateLeaderboard() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...patch }) =>
      axios.patch(endpoints.develop.w5.leaderboard(id), patch),
    onSuccess: done,
  });
}

export function useDeleteLeaderboard() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (id) => axios.delete(endpoints.develop.w5.leaderboard(id)),
    onSuccess: done,
  });
}

/** The server's message for a failed write (`{message, errors}` or text). */
export function boardErrorOf(e) {
  const r = e?.response?.data?.result ?? e?.result ?? null;
  if (r && typeof r === "object")
    return { message: r.message ?? null, errors: r.errors ?? {} };
  return { message: r ?? e?.message ?? "Saving failed.", errors: {} };
}

/** Every dataset a board can read (the dataset picker's list). */
export function useBoardDatasets() {
  return useQuery({
    queryKey: ["w5", "leaderboards", "datasets"],
    queryFn: () => axios.get(endpoints.develop.getDatasetList()),
    select: (res) =>
      (res?.data?.result?.datasets ?? []).map((d) => ({
        id: d.dataset_id,
        name: d.name,
      })),
  });
}

/** Column options and the runs catalogue for the chosen datasets (R1). */
export function useBoardOptions(datasetIds) {
  const params = datasetRunsParams({ include: "metrics" });
  const results = useQueries({
    queries: (datasetIds ?? []).map((id) => ({
      queryKey: ["develop", "dataset-runs", id, params],
      queryFn: () =>
        axios.get(endpoints.develop.datasetRuns.list(id), { params }),
      select: (res) => res?.data?.result,
    })),
  });
  const loading = results.some((r) => r.isLoading);
  const key = results.map((r) => r.dataUpdatedAt).join(",");
  return useMemo(() => {
    const data = results.map((r) => r.data).filter(Boolean);
    const columns = columnOptions(data.map((d) => d.metric_defs ?? []));
    const runs = data.flatMap((d) => d.results ?? []);
    return {
      loading,
      columns,
      catalog: runsCatalogFor(runs, { metricDefs: columns }),
    };
    // `key` changes whenever any dataset's R1 read changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, loading]);
}
