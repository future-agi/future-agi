import React from "react";
import { LinearProgress } from "@mui/material";
import { useParams, useSearchParams } from "react-router-dom";
import { useExperimentDetail } from "src/api/develop/runs";
import ExperimentAnalysisView from "./Analysis/ExperimentAnalysisView";

/**
 * `/dashboard/develop/experiment/:id/summary`: the analysis of the
 * experiment's runs (PRODUCT-SPEC §3.13). Baseline = `baseline=` when it is
 * one of the experiment's runs (or `none`), else the pinned dataset
 * baseline when it is in the experiment, else run A; a one-run
 * experiment shows absolute values.
 */
const ExperimentSummaryView = () => {
  const { experimentId } = useParams();
  const [searchParams] = useSearchParams();
  const { data: experiment, isLoading } = useExperimentDetail(experimentId);

  if (isLoading || !experiment) return <LinearProgress />;

  const runs = experiment.runs ?? [];
  const runIds = runs.map((r) => r.run_id);
  const requested = searchParams.get("baseline");
  // No `baseline=`: the analysis picks the dataset's pinned baseline when it
  // is one of these runs, else run A (R3 P5 MUST 3).
  const baselineId =
    runIds.length < 2
      ? "none"
      : requested === "none" || runIds.includes(requested)
        ? requested
        : undefined;

  return (
    <ExperimentAnalysisView
      datasetId={experiment.dataset_id}
      runIds={runIds}
      baselineId={baselineId}
      mode="experiment"
    />
  );
};

export default ExperimentSummaryView;
