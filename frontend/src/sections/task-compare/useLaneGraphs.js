// Task compare · each lane's trace as a graph: the trace-detail
// query Observe uses (useTraceTree), the run's Agent Playground nodes
// (graphNodes.withAgentGraphNodes, in memory), graph quality, the view
// recommendation in the lane's name and the node of its First wrong step.
// Up to 4 lanes (the workspace's maximum), so the hooks stay fixed.
import { useMemo } from "react";
import { useRunConfigs } from "src/api/develop/r2";
import useTraceTree from "src/sections/common/Trajectory/useTraceTree";
import {
  captureLabelFor,
  graphQualityFor,
  withAgentGraphNodes,
} from "src/sections/common/Trajectory/graphNodes";
import {
  recommendText,
  recommendView,
} from "src/sections/common/Trajectory/recommendView";
import {
  marksOfLane,
  spanNameOfStep,
} from "src/sections/common/Trajectory/trajectoryMarkers";
import {
  flowNameOf,
  flowQuality,
  isStepFlow,
  stepFlowKind,
  withStepFlow,
} from "src/sections/common/Trajectory/flowGraph";

export const MAX_GRAPH_LANES = 4;

/**
 * @param {{datasetId: string, lanes: object[], names: Map<string,string>}} args
 * @returns {{graphs: object[], recommended: string|null, loading: boolean}}
 *   `graphs[i]` = `{id, name, color, tree, quality, capture, rec, failedName,
 *   traceId, loading}` (tree null when the lane has no trace)
 */
export default function useLaneGraphs({ datasetId, lanes, names }) {
  const { data: cfg } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const ids = (lanes ?? [])
    .slice(0, MAX_GRAPH_LANES)
    .map((l) => l?.trial?.trace_id ?? null);
  const q0 = useTraceTree(ids[0] ?? null);
  const q1 = useTraceTree(ids[1] ?? null);
  const q2 = useTraceTree(ids[2] ?? null);
  const q3 = useTraceTree(ids[3] ?? null);
  const qs = [q0, q1, q2, q3];
  const key = qs.map((q) => (q.spans ? q.rootId : "")).join("|");

  return useMemo(() => {
    const runOf = (id) =>
      (cfg?.results ?? []).find((r) => r.run_id === id) ?? null;
    const graphs = (lanes ?? []).slice(0, MAX_GRAPH_LANES).map((lane, i) => {
      const q = qs[i];
      const name = names?.get?.(lane.id) ?? lane.blindName ?? lane.label;
      const run = runOf(lane.id) ?? { kind: lane.kind };
      const base = {
        id: lane.id,
        name,
        color: lane.color?.solid ?? null,
        traceId: ids[i],
        loading: Boolean(ids[i]) && q.isLoading,
      };
      if (!q.spans?.length) return { ...base, tree: null, quality: null };
      // Harness transcripts and calls draw as a step flow (IT-R1 G4).
      const tree = withStepFlow(withAgentGraphNodes(q.spans, run), run);
      const flow = isStepFlow(tree);
      const quality = flow
        ? flowQuality(graphQualityFor(tree, run), stepFlowKind(tree, run))
        : graphQualityFor(tree, run);
      const marks = marksOfLane({
        id: lane.id,
        label: name,
        steps: lane.steps,
        checks: lane.checks,
      });
      return {
        ...base,
        tree,
        quality,
        capture: captureLabelFor(tree, run),
        rec: recommendView(tree, quality, { subject: name }),
        failedName: Number.isInteger(marks.firstWrongStepN)
          ? (flow ? flowNameOf : (x) => x)(
              spanNameOfStep(tree, marks.firstWrongStepN),
            )
          : null,
      };
    });
    const rec = graphs.find((g) => g.rec?.view === "graph")?.rec ?? null;
    return {
      graphs,
      recommended: recommendText(rec),
      loading: graphs.some((g) => g.loading),
      runOf,
    };
  }, [cfg, lanes, names, key]); // eslint-disable-line react-hooks/exhaustive-deps
}
