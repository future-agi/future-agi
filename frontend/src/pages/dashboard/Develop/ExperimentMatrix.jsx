import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams, useSearchParams } from "react-router-dom";
import { useExperimentDetail } from "src/api/develop/runs";
import MatrixView from "src/sections/experiment-detail/matrix/MatrixView";

/**
 * `/dashboard/develop/experiment/:experimentId/matrix` (R2-SPEC P1): the
 * experiment's runs in the Matrix. Baseline = `?baseline=` when it is one of
 * the experiment's runs, else the first run.
 */
export default function ExperimentMatrix() {
  const { experimentId } = useParams();
  const [searchParams] = useSearchParams();
  const { data: experiment } = useExperimentDetail(experimentId);
  const runIds = (experiment?.runs ?? []).map((r) => r.run_id);
  const requested = searchParams.get("baseline");
  const baselineId = runIds.includes(requested) ? requested : runIds[0] ?? null;
  return (
    <>
      <Helmet>
        <title>Experiment Matrix</title>
      </Helmet>
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
