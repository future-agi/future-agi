import React, { useMemo, useState } from "react";
import { Alert, Box, Button, LinearProgress, useTheme } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
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

// Restores v1.56.1's summary host using the still-registered V2 stats API.
// Existing charts/renderers are reused unchanged. No DSX analysis query,
// candidate/verdict toolbar, Falcon analysis or task-review worklist mounts.
export default function ClassicExperimentSummary() {
  const { experimentId } = useParams();
  const isDark = useTheme().palette.mode === "dark";
  const [chooseWinnerOpen, setChooseWinnerOpen] = useState(false);
  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["experiment-summary", experimentId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.getSummary(experimentId)),
    enabled: Boolean(experimentId),
    select: (response) => response?.data?.result,
  });
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
      {/* Keep the v1.56.1 chooser independent of the mixed DSX toolbar. */}
      <Box display="flex" justifyContent="flex-end">
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
