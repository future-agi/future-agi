import React, { useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { useParams, useSearchParams } from "react-router-dom";
import { useExperimentDetail } from "src/api/develop/runs";
import MatrixView from "src/sections/experiment-detail/matrix/MatrixView";
import ExperimentRunHistory, {
  useExperimentRunHistory,
} from "src/sections/experiment-detail/ExperimentRunHistory";

/**
 * `/dashboard/develop/experiment/:experimentId/matrix` (R2-SPEC P1): the
 * experiment's runs in the Matrix. Baseline = `?baseline=` when it is one of
 * the runs in view, else the first of them.
 * The read lines up only runs that share one dataset version, trial plan
 * and evaluators, so the Matrix shows the newest such group (or the one
 * picked in Run history), as the Data tab and the header do, and the Run
 * history line names the runs it shows.
 */
export default function ExperimentMatrix() {
  const { experimentId } = useParams();
  const [searchParams] = useSearchParams();
  const { data: experiment } = useExperimentDetail(experimentId);
  const allRuns = useMemo(() => experiment?.runs ?? [], [experiment]);
  const history = useExperimentRunHistory(allRuns);
  const runIds = history.runs.map((r) => r.run_id);
  const requested = searchParams.get("baseline");
  const baselineId = runIds.includes(requested) ? requested : runIds[0] ?? null;
  return (
    <>
      <Helmet>
        <title>Experiment Matrix</title>
      </Helmet>
      <ExperimentRunHistory history={history} />
      <MatrixView
        mode="experiment"
        datasetId={experiment?.dataset_id}
        experimentId={experimentId}
        runIds={runIds}
        baselineId={baselineId}
      />
    </>
  );
}
