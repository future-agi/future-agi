import React, { useCallback, useMemo } from "react";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import { useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import { useDatasetRuns, useExperimentDetail } from "src/api/develop/runs";

import { runResultsKey } from "src/api/develop/run-results";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { useExperimentDetailContext } from "../experiment-context";
import RunResultsView from "../results/RunResultsView";
import ExperimentRunHistory, { useExperimentRunHistory } from "../ExperimentRunHistory";
import useResultsUrlState, {
  experimentDefaultBaseline,
} from "../results/useResultsUrlState";

/**
 * Baseline on the experiment page: `baseline=` if it is one of its runs,
 * else the dataset's pinned baseline when it is one of them (R3 MUST 3:
 * TS #2 → #2B, as on the leaderboard), else run A (C18).
 */
export function experimentBaseline(runIds, requested, pinnedId) {
  return experimentDefaultBaseline(runIds, requested, pinnedId);
}

/**
 * Experiment Data tab (PRODUCT-SPEC §3.10.1): the results surface over this
 * experiment's runs only. A single-run experiment is the same grid with one
 * run. `Add Evaluations` keeps the dataset's EvaluationDrawer.
 */
function ExperimentDataView() {
  const { experimentId } = useParams();
  const queryClient = useQueryClient();
  const { evaluateOpen, setEvaluateOpen } = useExperimentDetailContext();
  const url = useResultsUrlState();
  const {
    data: experiment,
    isLoading,
    isError,
  } = useExperimentDetail(experimentId, {
    refetchInterval: (query) =>
      pollIntervalFor(query.state.data?.data?.result?.runs ?? []),
  });

  const allRuns = useMemo(() => experiment?.runs ?? [], [experiment]);
  const history = useExperimentRunHistory(allRuns);
  const { runs } = history;
  const runIds = useMemo(() => runs.map((r) => r.run_id), [runs]);
  const datasetId = experiment?.dataset_id;
  const { data: datasetRuns } = useDatasetRuns(datasetId);
  const defaultBaseline = experimentBaseline(
    runIds,
    null,
    datasetRuns?.pinned_run_id,
  );
  const baselineId = url.baseline === "none" ? "none" : experimentBaseline(
    runIds,
    url.baseline,
    datasetRuns?.pinned_run_id,
  );

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: runResultsKey(datasetId) });
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
  }, [queryClient, datasetId, experimentId]);

  // Column options for the Evaluate drawer's mapping (user columns only).
  const { data: columnOptions } = useQuery({
    queryKey: ["experiment-column-config", experimentId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.experimentDetail(experimentId), {
        params: { column_config_only: true },
      }),
    select: (res) =>
      (res?.data?.result?.column_config ?? [])
        .filter(
          (c) =>
            !["evaluation", "experiment"].includes(c?.origin_type) &&
            !c?.name?.startsWith("Experiment"),
        )
        .map((c) => ({
          headerName: c.name,
          field: c.id,
          originType: c.origin_type,
          dataType: c.data_type,
        })),
    enabled: !!experimentId && evaluateOpen,
    refetchOnWindowFocus: false,
  });

  if (isLoading || (!experiment && !isError)) {
    return (
      <Stack
        alignItems="center"
        justifyContent="center"
        sx={{ flex: 1, py: 8 }}
      >
        <CircularProgress size={24} />
      </Stack>
    );
  }
  if (isError || !datasetId) {
    return (
      <Box sx={{ px: 2, py: 4 }}>
        <Typography typography="s1" color="text.secondary">
          This experiment could not be loaded.
        </Typography>
      </Box>
    );
  }

  return (
    <>
      <ExperimentRunHistory history={history} />
      <RunResultsView
        datasetId={datasetId}
        runIds={runIds}
        baselineId={baselineId}
        mode="experiment"
        experimentId={experimentId}
        initialRuns={runs}
        onBaselineChange={(id) =>
          url.patch({ baseline: id === defaultBaseline ? null : id })
        }
        onAddEvaluations={() => setEvaluateOpen(true)}
        onExperimentEdited={refresh}
      />
      <EvaluationDrawer
        open={evaluateOpen}
        onClose={() => setEvaluateOpen(false)}
        allColumns={columnOptions ?? []}
        refreshGrid={refresh}
        module="experiment"
        id={datasetId}
        showAdd
        showTest
        runLabel="Add & Run"
      />
    </>
  );
}

export default ExperimentDataView;
