// Run metrics (W5-4 B2) over the API: the dataset's definitions, their
// values on the runs in view, a draft formula's preview, and the save.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { w5Path } from "src/sections/common/Analysis/workbench/w5Api";

const KEY = "w5-derived-metrics";

/**
 * {metrics, values, errors} for `runIds` (definitions only without runs).
 * `opts.enabled` (default true) holds the read; `opts.keepPrevious` keeps
 * the last answer while a new run set loads (HO R7: a leaderboard column
 * stays while a run joins the board); `opts.stamp` reads again when it
 * changes (the runs' own numbers moved).
 */
export function useDerivedMetrics(datasetId, runIds = [], opts = {}) {
  const path = w5Path("derivedMetrics", datasetId);
  const { enabled = true, keepPrevious = false, stamp } = opts;
  return useQuery({
    queryKey: [
      KEY,
      datasetId,
      runIds.join(","),
      ...(stamp != null ? [stamp] : []),
    ],
    queryFn: () =>
      axios.get(path, {
        params: runIds.length ? { runs: runIds.join(",") } : {},
      }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && datasetId) && enabled,
    ...(keepPrevious ? { placeholderData: (prev) => prev } : {}),
  });
}

/** A draft formula's values on the runs (the server evaluates it). */
export function useFormulaPreview(datasetId, runIds, formula) {
  const path = w5Path("derivedMetrics", datasetId);
  const text = String(formula ?? "").trim();
  return useQuery({
    queryKey: [KEY, datasetId, "preview", runIds.join(","), text],
    queryFn: () =>
      axios.get(path, { params: { runs: runIds.join(","), formula: text } }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && datasetId && text && runIds.length),
    placeholderData: (prev) => prev,
  });
}

/** PUT the whole list; refreshes every run-metric read of the dataset. */
export function useSaveDerivedMetrics(datasetId) {
  const qc = useQueryClient();
  const path = w5Path("derivedMetrics", datasetId);
  return useMutation({
    mutationFn: (metrics) => axios.put(path, { metrics }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY, datasetId] }),
  });
}
