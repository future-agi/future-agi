// The reads behind the Matrix tab: R3 `shape=matrix` (polls while a run in
// view is still filling cells), R1 with config + lineage (run objects), R5
// `include=root_causes` (the PatternCallout) and, when the experiment was
// started from an earlier one, that experiment's matrix (the Pareto
// strip's second sentence).
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { analysisKey, analysisParams } from "src/api/develop/analysis";
import { configKeyOf, useMatrix, useRunConfigs } from "src/api/develop/r2";
import { paretoModel } from "./matrixModel";

const POLL_MS = 2000;

const isPending = (matrix) =>
  (matrix?.rows ?? []).some((r) =>
    Object.values(r.cells ?? {}).some((c) =>
      (c.trials ?? []).some((t) => t === null || t === undefined),
    ),
  );

/** R5 with `include=root_causes` (pattern + causes). */
export function useRootCauses({ datasetId, runIds, baselineId }, opts = {}) {
  const params = {
    ...analysisParams({ runIds, baselineId }),
    include: "root_causes",
  };
  return useQuery({
    queryKey: [...analysisKey(datasetId), "root_causes", params],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.analysis(datasetId), { params }),
    select: (res) => ({
      pattern: res.data?.result?.pattern ?? null,
      causes: res.data?.result?.root_causes ?? [],
    }),
    enabled:
      Boolean(datasetId && baselineId) &&
      (runIds ?? []).length > 1 &&
      opts.enabled !== false,
    staleTime: 30_000,
  });
}

/**
 * The experiment this one carried rows over from: its explicit
 * `carry_over_from_experiment_id`, else the latest earlier experiment on an
 * older dataset version that ran the same setups (config keys).
 */
export function parentExperimentRuns(allRuns, inView) {
  const runs = allRuns ?? [];
  const current = inView.filter(Boolean);
  const expIds = [...new Set(current.map((r) => r.experiment_id))];
  if (!current.length || expIds.length !== 1) return [];
  const explicit =
    current.find((r) => r.carry_over_from_experiment_id)
      ?.carry_over_from_experiment_id ?? null;
  const ordinal = current[0].experiment_ordinal ?? 0;
  const version = current[0].dataset_version?.number ?? null;
  const keys = new Set(current.map(configKeyOf));
  const byExp = new Map();
  runs.forEach((r) => {
    if (!r.experiment_id || r.experiment_id === expIds[0]) return;
    if (!byExp.has(r.experiment_id)) byExp.set(r.experiment_id, []);
    byExp.get(r.experiment_id).push(r);
  });
  if (explicit) return byExp.get(explicit) ?? [];
  const candidates = [...byExp.values()]
    .filter((list) => {
      const r0 = list[0];
      if ((r0.experiment_ordinal ?? 0) >= ordinal) return false;
      if (version != null && (r0.dataset_version?.number ?? 0) >= version)
        return false;
      return list.filter((r) => keys.has(configKeyOf(r))).length >= 2;
    })
    .sort(
      (a, b) => (b[0].experiment_ordinal ?? 0) - (a[0].experiment_ordinal ?? 0),
    );
  return candidates[0] ?? [];
}

const shortHarness = (run) => {
  const name =
    run?.config?.harness?.name ?? run?.config?.agent?.name ?? run?.name ?? "";
  return String(name).replace(/\s+CLI$/i, "");
};

/**
 * `The 6 production rows moved the coding harness off the frontier.` or null.
 * @param {object} current  matrix in view
 * @param {object} parent  the parent experiment's matrix
 */
export function frontierShiftSentence({
  current,
  parent,
  runIds,
  parentRunIds,
  runsById,
  parentRunsById,
}) {
  if (!current || !parent || !parentRunIds?.length) return null;
  // Only the setups the parent ran: a new iteration is not "the rows".
  const parentKeys = new Set(
    parentRunIds.map((id) => configKeyOf(parentRunsById[id])),
  );
  const same = (runIds ?? []).filter(
    (id) =>
      parentKeys.has(configKeyOf(runsById[id])) &&
      !runsById[id]?.parent_run_id &&
      !runsById[id]?.iteration,
  );
  const now = paretoModel(current, { runIds: same, runsById });
  const before = paretoModel(parent, {
    runIds: parentRunIds,
    runsById: parentRunsById,
  });
  const wasOn = new Set(
    before.points
      .filter((p) => p.frontier)
      .map((p) => configKeyOf(parentRunsById[p.id])),
  );
  const moved = now.points.find(
    (p) => !p.frontier && wasOn.has(configKeyOf(runsById[p.id])),
  );
  if (!moved) return null;
  const parentRows = new Set((parent.rows ?? []).map((r) => r.row_id));
  const added = (current.rows ?? []).filter(
    (r) => !parentRows.has(r.row_id) && r.source === "session",
  ).length;
  if (!added) return null;
  return `The ${added} production ${added === 1 ? "row" : "rows"} moved ${shortHarness(runsById[moved.id])} off the frontier.`;
}

export default function useMatrixData({ datasetId, runIds, baselineId }) {
  const matrixQ = useMatrix(
    { datasetId, runIds, baselineId, measure: "pass_at_1" },
    {
      refetchInterval: (query) =>
        isPending(query.state.data?.data?.result) ? POLL_MS : false,
    },
  );
  const runsQ = useRunConfigs(datasetId);
  const allRuns = useMemo(() => runsQ.data?.results ?? [], [runsQ.data]);
  const runsById = useMemo(
    () => Object.fromEntries(allRuns.map((r) => [r.run_id, r])),
    [allRuns],
  );
  const inView = useMemo(
    () => (runIds ?? []).map((id) => runsById[id]).filter(Boolean),
    [runIds, runsById],
  );
  const rootQ = useRootCauses({ datasetId, runIds, baselineId });
  const parentRuns = useMemo(
    () => parentExperimentRuns(allRuns, inView),
    [allRuns, inView],
  );
  const parentIds = parentRuns.map((r) => r.run_id);
  const parentQ = useMatrix(
    { datasetId, runIds: parentIds, measure: "pass_at_1" },
    { enabled: parentIds.length > 1 },
  );
  const shift = useMemo(
    () =>
      parentQ.data && matrixQ.data
        ? frontierShiftSentence({
            current: matrixQ.data,
            parent: parentQ.data,
            runIds,
            parentRunIds: parentIds,
            runsById,
            parentRunsById: runsById,
          })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [parentQ.data, matrixQ.data, runsById, (runIds ?? []).join(",")],
  );
  return {
    matrix: matrixQ.data ?? null,
    isLoading: matrixQ.isLoading,
    isError: matrixQ.isError,
    refetch: matrixQ.refetch,
    running: isPending(matrixQ.data),
    allRuns,
    runsById,
    inView,
    rootCauses: rootQ.data ?? null,
    frontierShift: shift,
  };
}
