import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams, useSearchParams } from "react-router-dom";
import { useExperimentDetail } from "src/api/develop/runs";
import PathsView from "src/sections/experiment-detail/paths/PathsView";

/**
 * `/dashboard/develop/experiment/:experimentId/paths` (R4-SPEC D14): the
 * experiment's runs in the Paths tab. Baseline = `?baseline=` when it is one
 * of the experiment's runs, else the first run.
 */
export default function ExperimentPaths() {
  const { experimentId } = useParams();
  const [searchParams] = useSearchParams();
  const { data: experiment } = useExperimentDetail(experimentId);
  const runIds = (experiment?.runs ?? []).map((r) => r.run_id);
  const requested = searchParams.get("baseline");
  const baselineId = runIds.includes(requested) ? requested : runIds[0] ?? null;
  return (
    <>
      <Helmet>
        <title>Experiment Paths</title>
      </Helmet>
      <PathsView
        datasetId={experiment?.dataset_id}
        experimentId={experimentId}
        runIds={runIds}
        baselineId={baselineId}
      />
    </>
  );
}
