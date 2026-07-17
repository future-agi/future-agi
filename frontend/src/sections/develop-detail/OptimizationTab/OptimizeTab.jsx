import { Box } from "@mui/material";
import React from "react";
import { useParams } from "react-router";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { DatasetOptimizationContainer } from "../DatasetOptimization";

const OptimizeTab = () => {
  const { dataset } = useParams();
  const { isOSS } = useDeploymentMode();
  // Pass shouldFetch=true to enable the query
  const columns = useDatasetColumnConfig(dataset, false, true);

  if (isOSS) {
    return <OSSUpgradeGate feature="optimization" />;
  }

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
