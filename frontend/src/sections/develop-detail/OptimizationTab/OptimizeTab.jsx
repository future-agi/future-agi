import { Box } from "@mui/material";
import React from "react";
import { useParams } from "react-router";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { DatasetOptimizationContainer } from "../DatasetOptimization";

const OptimizeTab = () => {
  const { dataset } = useParams();
  // Pass shouldFetch=true to enable the query
  const columns = useDatasetColumnConfig(dataset, false, true);

  return (
    <Box
      sx={{
        flex: 1,
        padding: "12px",
        height: "100%",
        overflowY: "hidden",
      }}
    >
      <DatasetOptimizationContainer
        datasetId={dataset}
        columns={columns || []}
      />
    </Box>
  );
};

export default OptimizeTab;
