import { Box } from "@mui/material";
import React from "react";
import TestDetailBreadcrumb from "../TestDetailBreadcrumb";
import { useParams } from "react-router";
import EachOptimizationComponent from "../FixMyAgentDrawer/EachOptimizationComponent";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const OptimizationDetail = () => {
  const { testId, executionId, optimizationId } = useParams();
  const { data: optimizationData, isLoading } = useQuery({
    queryKey: ["fix-my-agent-optimization-details", optimizationId],
    queryFn: () =>
      axios.get(
        endpoints.optimizeSimulate.getOptimizationDetails(optimizationId),
      ),
    enabled: !!optimizationId,
    select: (data) => data?.data?.result,
  });
  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        width: "100%",
      }}
    >
      <Box>
        <TestDetailBreadcrumb
          items={[
            {
              label: "Simulated runs",
              href: `/dashboard/simulate/test/${testId}`,
            },
            {
              label: `Execution : ...${executionId.slice(-5)}`,
              href: `/dashboard/simulate/test/${testId}/${executionId}`,
            },
            {
              label: "Optimization Runs",
              href: `/dashboard/simulate/test/${testId}/${executionId}/optimization_runs`,
            },
            {
              label: optimizationData?.optimiser_name ?? optimizationId,
              isLoading: isLoading,
            },
          ]}
        />
      </Box>
      <Box sx={{ overflowY: "auto" }}>
        <EachOptimizationComponent
          optimizationId={optimizationId}
          isDrawer={false}
        />
      </Box>
    </Box>
  );
};

export default OptimizationDetail;
