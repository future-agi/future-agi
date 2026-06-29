import { Box } from "@mui/material";
import React from "react";
import { useParams } from "react-router";
import TestDetailBreadcrumb from "../TestDetailBreadcrumb";
import OptimizationDetailComponent from "../FixMyAgentDrawer/OptimizationDetail/OptimizationDetailComponent";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useOptimizeTrialPrompts } from "src/api/tests/testDetails";

const TrialDetail = () => {
  const { testId, executionId, optimizationId, trialId } = useParams();
  const { data: optimizationData, isLoading: isLoadingOptimization } = useQuery(
    {
      queryKey: ["fix-my-agent-optimization-details", optimizationId],
      queryFn: () =>
        axios.get(
          endpoints.optimizeSimulate.getOptimizationDetails(optimizationId),
        ),
      enabled: !!optimizationId,
      select: (data) => data?.data?.result,
    },
  );

  const { data: trailPromptData, isLoading: isLoadingTrial } =
    useOptimizeTrialPrompts({
      optimizationId,
      trialId,
    });

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
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
              label: `Execution : ...${executionId?.slice(-5)}`,
              href: `/dashboard/simulate/test/${testId}/${executionId}`,
            },
            {
              label: "Optimization Runs",
              href: `/dashboard/simulate/test/${testId}/${executionId}/optimization_runs`,
            },
            {
              label: optimizationData?.optimiser_name ?? optimizationId,
              isLoading: isLoadingOptimization,
              href: `/dashboard/simulate/test/${testId}/${executionId}/${optimizationId}`,
            },
            {
              label: trailPromptData?.trial_name ?? trialId,
              isLoading: isLoadingTrial,
            },
          ]}
        />
      </Box>
      <Box sx={{ overflowY: "auto", height: "100%" }}>
        <OptimizationDetailComponent
          optimizationId={optimizationId}
          trialId={trialId}
          onClose={() => {}}
          isDrawer={false}
        />
      </Box>
    </Box>
  );
};

export default TrialDetail;
