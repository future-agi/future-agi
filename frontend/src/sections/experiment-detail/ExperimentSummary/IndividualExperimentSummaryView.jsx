import { Box, LinearProgress } from "@mui/material";
import React, { useMemo } from "react";
import ExperimentSummaryTable from "./ExperimentSummaryTable";
import ExperimentEvaluationChart from "./ExperimentEvaluationChart";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { ExperimentSummaryStaticColumnDefs } from "./tableConfig";
import ExperimentEvalCellRenderer from "./CustomRenderers/ExperimentEvalCellRenderer";
import RankWithIndexRenderer from "./CustomRenderers/RankWithIndexRenderer";

const IndividualExperimentSummaryView = () => {
  const { individualExperimentId } = useParams();

  const { data, isLoading } = useQuery({
    queryKey: ["experiment-summary", individualExperimentId],
    queryFn: () =>
      axios.get(
        endpoints.develop.experiment.getSummary(individualExperimentId),
      ),
    select: (e) => e?.data?.result,
  });

  const columns = useMemo(() => {
    let colData = [];
    if (data?.metadata?.isWinnerChosen) {
      colData.push({
        field: "rank",
        headerCellRenderer: null,
        headerName: "Ranking",
        width: 100,
        cellRenderer: RankWithIndexRenderer,
      });
    }

    colData = [...colData, ...ExperimentSummaryStaticColumnDefs];

    for (const eachCol of data?.column_config || data?.columnConfig || []) {
      colData.push({
        field: eachCol?.name,
        headerName: eachCol?.name,
        cellRenderer: ExperimentEvalCellRenderer,
        cellStyle: {
          padding: 0,
        },
        flex: 1,
        minWidth: 150,
      });
    }

    return colData;
  }, [data]);

  if (isLoading) {
    return <LinearProgress />;
  }

  return (
    <Box
      sx={{
        padding: "12px",
        flex: 1,
        overflowY: "auto",
        display: "flex",
        gap: 2,
        flexDirection: "column",
      }}
    >
      <ExperimentSummaryTable
        columns={columns}
        rows={data?.table_data || data?.tableData}
        evalsList={data?.column_config || data?.columnConfig}
      />
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {(data?.column_config || data?.columnConfig)?.map((col) => (
          <ExperimentEvaluationChart
            key={col.id}
            col={col}
            rows={data?.table_data || data?.tableData}
          />
        ))}
      </Box>
    </Box>
  );
};

export default IndividualExperimentSummaryView;
