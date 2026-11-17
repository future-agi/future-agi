// The leaderboard's numbers and versions (MUST 7; D22, D26):
// the matrix of every run on the dataset, then the pure model in
// leaderboardVersions.js. Every screen piece (headline, name cells, Best
// line, pass^k) reads this one object.
// The results API answers runs of ONE comparison group per read (same
// dataset version, plan and evaluators; results_selection.py) and at most 8
// runs, so runs are read per group, 8 at a time, and merged by row. Each
// read says baseline=none: without it the API adds the dataset's pinned
// baseline, and a pin in another group makes the read refuse
// (results_catalog.py pinned_baseline).
import { useMemo, useRef } from "react";
import { useQueries } from "@tanstack/react-query";
import { endpoints } from "src/utils/axios";
import { fetchReadPages, readEnvelope } from "src/api/develop/runs";
import { MATRIX_PAGE_SIZE } from "src/api/develop/r2";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { comparisonKeyOf } from "src/sections/experiment-detail/matrix/comparableRuns";
import { leaderboardNumbers, mergeMatrices } from "./leaderboardVersions";
import { RECORDED_REASON } from "./recordedTaskNumbers";

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

/**
 * The matrix reads for `runs`: one list of run ids per read, each from one
 * comparison group, at most MATRIX_RUNS_PER_READ long, sorted so the keys
 * stay stable. A run with no comparison group (`null`: no immutable plan)
 * cannot be read; it keeps its own `success_rate`. Pure.
 * @param {object[]} runs
 * @returns {string[][]}
 */
export function matrixReadsOf(runs = []) {
  const groups = new Map();
  (runs ?? []).forEach((r) => {
    if (!r?.run_id || r.comparison_group === null) return;
    const key = comparisonKeyOf(r);
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key).add(r.run_id);
  });
  const out = [];
  [...groups.keys()].sort().forEach((key) => {
    const ids = [...groups.get(key)].sort();
    for (let i = 0; i < ids.length; i += MATRIX_RUNS_PER_READ)
      out.push(ids.slice(i, i + MATRIX_RUNS_PER_READ));
  });
  return out;
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
  const chunks = useMemo(() => matrixReadsOf(runs), [runs]);
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
        baseline: "none",
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
        queryFn: ({ signal }) =>
          fetchReadPages(
            endpoints.develop.datasetRuns.results(view.datasetId),
            params,
            { collection: "rows", signal },
          ),
        select: readEnvelope,
        enabled: Boolean(view.datasetId),
        staleTime: 30_000,
      };
    }),
  });
  // Every read has answered or failed: the reads that answered are merged,
  // so one group's failed read leaves the other groups' numbers on screen
  // (that group's runs keep their own numbers; matrixError says it).
  const settled =
    results.length > 0 && results.every((r) => r.data || r.isError);
  const parts = results.map((r) => r.data).filter(Boolean);
  const ready = settled && parts.length > 0;
  // Refetches that change cells but not the row count still recompute.
  const key = results.map((r) => r.dataUpdatedAt ?? 0).join("|");
  const fresh = useMemo(
    () =>
      ready
        ? {
            ...mergeMatrices(parts),
            // Runs whose read holds more rows than it returned.
            partialRunIds: parts
              .filter((p) => p?.pagination?.has_more)
              .flatMap((p) => Object.keys(p?.columns ?? {})),
          }
        : null,
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
  // A failed refresh cannot use cached rows for a current recommendation.
  // This also covers runs with no recorded plan (never read).
  const failedKey = results.map((r) => (r.isError ? 1 : 0)).join("");
  const unreadRuns = useMemo(() => {
    const out = {};
    runs.forEach((r) => {
      if (r?.run_id && r.comparison_group === null)
        out[r.run_id] = RECORDED_REASON.notRead;
    });
    chunks.forEach((ids, i) => {
      if (failedKey[i] === "1")
        ids.forEach((id) => {
          out[id] = RECORDED_REASON.readFailed;
        });
    });
    return out;
  }, [runs, chunks, failedKey]);
  // IT-R1 G2 P1: the merged matrix rides along, so the headline compares
  // any picked pair with runNumbers.compareRuns (never a second formula).
  return useMemo(
    () => ({
      ...leaderboardNumbers(runs, matrix, {
        noun,
        partialRunIds: matrix?.partialRunIds,
        unreadRuns,
      }),
      noun,
      kind,
      matrix,
      matrixError,
    }),
    [runs, matrix, noun, kind, matrixError, unreadRuns],
  );
}
