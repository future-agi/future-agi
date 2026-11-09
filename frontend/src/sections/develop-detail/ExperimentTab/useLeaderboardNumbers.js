// The leaderboard's numbers and versions (R4 lane Rb MUST 7; D22, D26):
// the matrix of every run on the dataset (the results API answers at most
// 8 runs per read, so it is read in chunks and merged by row), then the
// pure model in leaderboardVersions.js. Every screen piece (headline, name
// cells, Best line, pass^k) reads this one object.
import { useMemo, useRef } from "react";
import { useQueries } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { MATRIX_PAGE_SIZE } from "src/api/develop/r2";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { leaderboardNumbers, mergeMatrices } from "./leaderboardVersions";

/** The results API's per-read run cap (results-wire MAX_RUNS). */
export const MATRIX_RUNS_PER_READ = 8;

/**
 * Each run's progress stamp (`status:done`), by run id: what a matrix read
 * of that run depends on. Pure.
 * @param {object[]} runs
 * @returns {Record<string, string>}
 */
export function runProgressStamps(runs = []) {
  return Object.fromEntries(
    (runs ?? [])
      .filter((r) => r?.run_id)
      .map((r) => [r.run_id, `${r.status ?? ""}:${r.progress?.done ?? ""}`]),
  );
}

export default function useLeaderboardNumbers(view) {
  const { kind } = useDatasetKind(view.datasetId);
  // IT-R2 G2 P2: a voice dataset's rows are calls (`passes more calls`).
  const noun =
    kind === "task_suite"
      ? "task"
      : runKindOf(view.baselineRun) === "voice"
        ? "call"
        : "row";
  const runs = useMemo(() => {
    const list = [...(view.allRuns ?? [])];
    const b = view.baselineRun;
    if (b && !list.some((r) => r.run_id === b.run_id)) list.push(b);
    return list;
  }, [view.allRuns, view.baselineRun]);
  const chunks = useMemo(() => {
    const ids = runs.map((r) => r.run_id).sort();
    const out = [];
    for (let i = 0; i < ids.length; i += MATRIX_RUNS_PER_READ)
      out.push(ids.slice(i, i + MATRIX_RUNS_PER_READ));
    return out;
  }, [runs]);
  // L3 finalize: a run that is still going moves the matrix. Each chunk's
  // key carries its runs' status and done count (the runs list polls while
  // a run is going), so the headline follows the run and reads the finished
  // numbers once it ends, never `0 tasks both ran · still running` after
  // every result is in.
  const stamps = useMemo(() => runProgressStamps(runs), [runs]);
  const results = useQueries({
    queries: chunks.map((ids) => {
      const params = {
        runs: ids.join(","),
        shape: "matrix",
        page: 0,
        page_size: MATRIX_PAGE_SIZE,
      };
      return {
        queryKey: [
          "develop",
          "r4-leaderboard-matrix",
          view.datasetId,
          params,
          ids.map((id) => stamps[id] ?? "").join(","),
        ],
        queryFn: () =>
          axios.get(endpoints.develop.datasetRuns.results(view.datasetId), {
            params,
          }),
        select: (res) => res.data?.result,
        enabled: Boolean(view.datasetId),
        staleTime: 30_000,
      };
    }),
  });
  const parts = results.map((r) => r.data);
  const ready = parts.length > 0 && parts.every(Boolean);
  // Refetches that change cells but not the row count still recompute.
  const key = results.map((r) => r.dataUpdatedAt ?? 0).join("|");
  const fresh = useMemo(
    () => (ready ? mergeMatrices(parts) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ready, key, chunks],
  );
  // IT-R5 G2 P1: a re-read (a new run joins a chunk, a poll) keeps the last
  // merged matrix on screen, so the headline never drops back to its
  // loading line; a failed read is said (the answer falls back to the runs'
  // own numbers instead of waiting for ever).
  const last = useRef({ datasetId: null, matrix: null });
  if (fresh) last.current = { datasetId: view.datasetId, matrix: fresh };
  const matrix =
    fresh ??
    (last.current.datasetId === view.datasetId ? last.current.matrix : null);
  const matrixError = results.some((r) => r.isError);
  // IT-R1 G2 P1: the merged matrix rides along, so the headline compares
  // any picked pair with runNumbers.compareRuns (never a second formula).
  return useMemo(
    () => ({
      ...leaderboardNumbers(runs, matrix, { noun }),
      noun,
      kind,
      matrix,
      matrixError,
    }),
    [runs, matrix, noun, kind, matrixError],
  );
}
