// K-3 (MUST-BUILD BL-06): the publish gate of a version the dock saved, from
// the same reads the result banner makes (R1 configs + quick tests, and R3
// `shape=matrix` of base vs run with the banner's key and poll), so the dock
// and the banner never disagree and nothing is fetched twice.
import { useMemo } from "react";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import { runLabel } from "src/sections/common/Runs/runLabel";
import { agentVersionOf } from "src/sections/common/Runs/provenanceModel";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { candidatePending } from "./fixVerdict";
import useQuickTests from "./useQuickTests";
import {
  carriedFromBaseOf,
  coverageOf,
  gateRunIdOf,
  publishGateOf,
} from "./publishGate";

const NOT_READY = Object.freeze({
  show: false,
  ready: false,
  reason: null,
  state: "waiting",
  waiting: 0,
  carried: 0,
  notRun: 0,
  runId: null,
});

/**
 * @param {{datasetId?: string|null, baseId?: string|null,
 *   runId?: string|null}} args  `runId` = the run that saved the version;
 *   the gate follows its as-is reruns (gateRunIdOf).
 * @returns {ReturnType<typeof publishGateOf> & {runId: string|null}}
 */
export default function usePublishGate({ datasetId, baseId, runId }) {
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const on = Boolean(datasetId && baseId && runId);
  const configs = useRunConfigs(datasetId, { enabled: on });
  const quick = useQuickTests(on ? datasetId : null);
  const runs = useMemo(
    () => [...(configs.data?.results ?? []), ...(quick.data ?? [])],
    [configs.data, quick.data],
  );
  const candId = on ? gateRunIdOf(runs, runId) : null;
  const show = Boolean(on && candId);
  // The banner's read of this pair: same key, same poll while tries score.
  const matrix = useMatrix(
    { datasetId, runIds: show ? [baseId, candId] : [], baselineId: baseId },
    {
      enabled: show,
      refetchInterval: (q) =>
        candidatePending(q.state.data?.data?.result, baseId, candId)
          ? pollIntervalFor({ status: "running" })
          : false,
    },
  );
  return useMemo(() => {
    if (!show || !matrix.data) return NOT_READY;
    const base = runs.find((r) => r.run_id === baseId) ?? null;
    const cand = runs.find((r) => r.run_id === candId) ?? null;
    if (!base || !cand) return NOT_READY;
    const baseLabel = runLabel(base);
    const gate = publishGateOf({
      rerunCmp: compareRuns({
        matrix: matrix.data,
        baselineId: baseId,
        candidateId: candId,
        scope: "rerun",
      }),
      carriedFromBase: carriedFromBaseOf(matrix.data, candId, baseLabel),
      ...coverageOf(matrix.data, baseId, candId),
      draftVersion: agentVersionOf(cand),
      cand,
      baseLabel,
      candLabel: runLabel(cand),
      canEdit,
    });
    return { ...gate, runId: candId };
  }, [show, matrix.data, runs, baseId, candId, canEdit]);
}
