import React, { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Button, LinearProgress, useTheme } from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useExperimentDetail } from "src/api/develop/runs";
import {
  experimentStatusQueryOptions,
  isExperimentPending,
} from "src/api/develop/experiment-status-refresh";
import { CLASSIC_EXPERIMENT_COPY as COPY } from "src/launch/classicExperimentCopy";
import { getUniqueColorPalette } from "src/utils/utils";
import { fmtScore } from "src/sections/common/format/fmt";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import ExperimentSummaryTable from "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryTable";
import ChooseWinnerDrawer from "src/sections/experiment-detail/ExperimentSummary/ChooseWinnerDrawer";
import ExperimentEvaluationChart from "src/sections/experiment-detail/ExperimentSummary/ExperimentEvaluationChart";
import SpiderChartExperiment from "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryChart/SpiderChartExperiment";
import { ExperimentSummaryStaticColumnDefs } from "src/sections/experiment-detail/ExperimentSummary/tableConfig";
import ExperimentEvalCellRenderer from "src/sections/experiment-detail/ExperimentSummary/CustomRenderers/ExperimentEvalCellRenderer";
import RankWithIndexRenderer from "src/sections/experiment-detail/ExperimentSummary/CustomRenderers/RankWithIndexRenderer";
import ClassicSummaryActions from "./ClassicSummaryActions";
import { OTHER_EXPERIMENTS_COPY } from "src/sections/develop-detail/ExperimentTabClassic/otherExperimentsCopy";

// Restores v1.56.1's summary host using the still-registered V2 stats API.
// Existing charts/renderers are reused unchanged. No DSX analysis query,
// candidate/verdict toolbar, Falcon analysis or task-review worklist mounts.
export default function ClassicExperimentSummary() {
  const { experimentId } = useParams();
  const queryClient = useQueryClient();
  const isDark = useTheme().palette.mode === "dark";
  const [chooseWinnerOpen, setChooseWinnerOpen] = useState(false);
  // Summary must follow completion even when re-run controls are unavailable.
  const detail = useExperimentDetail(experimentId, experimentStatusQueryOptions);
  // The stats are computed from the experiment's snapshot dataset. An
  // experiment the detail shows without one (snapshot_dataset_id null: the
  // sample experiment every organization gets, and the first experiment
  // writer's) has no stats to read (the stats read answers 400), so the read
  // is not sent and the page says so in words.
  const noSummary = detail.data?.snapshot_dataset_id === null;
  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["experiment-summary", experimentId],
    queryFn: ({ signal }) =>
      axios.get(endpoints.develop.experiment.getSummary(experimentId), { signal }),
    enabled: Boolean(experimentId) && !noSummary,
    select: (response) => response?.data?.result,
  });
  const detailStatus = detail.data?.status;
  const previousDetail = useRef(null);
  useEffect(() => {
    if (previousDetail.current?.experimentId !== experimentId) {
      previousDetail.current = null;
    }
    if (!experimentId || !detail.isSuccess) return;
    const previous = previousDetail.current;
    if (
      previous &&
      previous.updatedAt === detail.dataUpdatedAt &&
      previous.status === detailStatus
    ) return;
    const observation = {
      experimentId,
      updatedAt: detail.dataUpdatedAt,
      status: detailStatus,
      pending: isExperimentPending(detailStatus),
    };
    previousDetail.current = observation;
    const filters = { queryKey: ["experiment-summary", experimentId], exact: true };
    if (observation.pending) {
      void queryClient.invalidateQueries(filters);
    } else if (!previous || previous.pending) {
      // Invalidation alone joins an initial stats request with no cached data.
      // Supersede any pre-completion read so final stats are actually fetched.
      void queryClient.cancelQueries(filters).then(() => {
        if (previousDetail.current === observation) {
          return queryClient.invalidateQueries(filters);
        }
      });
    }
  }, [experimentId, detail.isSuccess, detail.dataUpdatedAt, detailStatus, queryClient]);
  const columns = useMemo(
    () => [
      ...(data?.metadata?.is_winner_chosen || data?.metadata?.isWinnerChosen
        ? [
            {
              field: "rank",
              headerName: COPY.ranking,
              width: 100,
              cellRenderer: RankWithIndexRenderer,
            },
          ]
        : []),
      ...ExperimentSummaryStaticColumnDefs,
      ...(data?.column_config || data?.columnConfig || []).map((col) => ({
        field: col.name,
        headerName: col.name,
        cellRenderer: ExperimentEvalCellRenderer,
        cellStyle: { padding: 0 },
        col,
        flex: 1,
        minWidth: 150,
      })),
    ],
    [data],
  );
  if (noSummary)
    return (
      <Box sx={{ p: 1.5 }}>
        <Alert
          severity="info"
          role="status"
          data-testid="summary-not-available"
        >
          {OTHER_EXPERIMENTS_COPY.summaryNotAvailable}
        </Alert>
      </Box>
    );
  if (isLoading) return <LinearProgress />;
  if (isError)
    return (
      <Alert
        severity="error"
        action={<Button onClick={() => refetch()}>{COPY.retry}</Button>}
      >
        {COPY.summaryReadFailed}
      </Alert>
    );
  const rows = data?.table_data || data?.tableData || [];
  const evals = data?.column_config || data?.columnConfig || [];
  // The legacy stats endpoint uses column names and a 0–100 score scale;
  // the shared plot now expects named series with 0–1 values, not data/cols.
  // Keep this adaptation local: no DSX analysis or baseline selection is needed.
  const chartMetrics = evals.map((col) => ({
    key: col.name,
    name: col.name,
    ...(col.output_type === OutputTypes.NUMERIC && {
      fmt: (value) => fmtScore(value * 100),
    }),
  }));
  const chartSeries = rows.map((row, index) => ({
    id: String(row.dataset_id ?? row.datasetId ?? index),
    label: row.experiment_dataset_name ?? row.experimentDatasetName,
    color: isDark
      ? getUniqueColorPalette(index).tagForeground
      : getUniqueColorPalette(index).solid,
    values: Object.fromEntries(
      evals.map((col) => {
        const value = row[col.name];
        return [
          col.name,
          typeof value === "number" && Number.isFinite(value)
            ? value / 100
            : null,
        ];
      }),
    ),
  }));
  return (
    <Box
      sx={{
        p: 1.5,
        flex: 1,
        overflowY: "auto",
        display: "flex",
        gap: 2,
        flexDirection: "column",
      }}
    >
      {detail.isError && (
        <Alert
          severity="warning"
          action={
            <Button disabled={detail.isFetching} onClick={() => detail.refetch()}>
              Refresh status
            </Button>
          }
        >
          Could not refresh experiment status. The summary may be out of date.
        </Alert>
      )}
      {/* Keep the v1.56.1 chooser independent of the mixed DSX toolbar. */}
      <Box display="flex" justifyContent="flex-end" gap={1}>
        {/* More: Download…, Print or save as PDF and Copy summary. */}
        <ClassicSummaryActions
          columns={columns}
          rows={rows}
          name={detail.data?.name}
        />
        <Button variant="contained" onClick={() => setChooseWinnerOpen(true)}>
          {COPY.chooseWinner}
        </Button>
      </Box>
      {chooseWinnerOpen && (
        <ChooseWinnerDrawer
          open
          onClose={() => setChooseWinnerOpen(false)}
          evalsList={evals}
        />
      )}
      <ExperimentSummaryTable
        columns={columns}
        rows={rows}
        loading={isFetching}
      />
      <SpiderChartExperiment series={chartSeries} metrics={chartMetrics} />
      {evals.map((col) => (
        <ExperimentEvaluationChart
          key={col.id ?? col.name}
          col={col}
          rows={rows}
        />
      ))}
    </Box>
  );
}
