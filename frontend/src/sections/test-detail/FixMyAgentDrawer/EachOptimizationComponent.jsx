import { Box, Stack } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import OptimizingAgentSteps from "../OptimizeAgentDrawer.jsx/OptimizingAgentSteps";
import OptimizeAgentHeaderComponent from "../OptimizeAgentDrawer.jsx/OptimizeAgentHeaderComponent";
import FixMyAgentHeader from "./FixMyAgentHeader";
import OptimizationResultGraph from "./OptimizationResults/OptimizationResultGraph";
import OptimizationResultBar from "./OptimizationResults/OptimizationResultBar";
import OptimizationResultGrid from "./OptimizationResults/OptimizationResultGrid";
import {
  AgentPromptOptimizerRefetchStates,
  AgentPromptOptimizerStatus,
} from "./common";
import { ShowComponent } from "../../../components/show";
import OptmizationLoaderComponent from "../OptimizeAgentDrawer.jsx/OptmizationLoaderComponent";
import OptimizationResultProvider from "./OptimizationResults/context/OptimizationResultProvider";

const EachOptimizationComponent = ({
  optimizationId: drawersOptimizationId,
  isDrawer = true,
  onClose,
}) => {
  const { optimizationId } = useParams();
  const enabledOptimizationId = optimizationId ?? drawersOptimizationId;

  const { data: optimizationData, isPending: isPendingOptimizationData } =
    useQuery({
      queryKey: ["fix-my-agent-optimization-details", enabledOptimizationId],
      queryFn: () =>
        axios.get(
          endpoints.optimizeSimulate.getOptimizationDetails(
            enabledOptimizationId,
          ),
        ),
      enabled: !!enabledOptimizationId,
      refetchInterval: ({ state }) => {
        const result = state?.data?.data?.result;
        if (AgentPromptOptimizerRefetchStates.includes(result?.status)) {
          return 5000;
        }
        return false;
      },
      select: (data) => data?.data?.result,
    });

  return (
    <Stack
      spacing={2}
      sx={{ padding: isDrawer ? 2 : 0, width: "100%", height: "100%" }}
    >
      <ShowComponent condition={isDrawer}>
        <FixMyAgentHeader onClose={onClose} />
      </ShowComponent>
      <OptimizeAgentHeaderComponent
        optimization={optimizationData}
        isLoading={isPendingOptimizationData}
      />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
          overflow: "auto",
        }}
      >
        <OptimizingAgentSteps
          status={optimizationData?.status}
          optimizationId={enabledOptimizationId}
        />
        <ShowComponent
          condition={
            optimizationData?.status !== AgentPromptOptimizerStatus.COMPLETED
          }
        >
          <OptmizationLoaderComponent
            optimizationStatus={optimizationData?.status}
            reason={optimizationData?.error_message}
          />
        </ShowComponent>
        <ShowComponent
          condition={
            optimizationData?.status === AgentPromptOptimizerStatus.COMPLETED
          }
        >
          <OptimizationResultGraph optimizationId={enabledOptimizationId} />
          <OptimizationResultProvider>
            <OptimizationResultBar optimizationData={optimizationData} />
            <OptimizationResultGrid
              optimizationId={enabledOptimizationId}
              isDrawer={isDrawer}
            />
          </OptimizationResultProvider>
        </ShowComponent>
      </Box>
    </Stack>
  );
};

EachOptimizationComponent.propTypes = {
  optimizationId: PropTypes.string,
  isDrawer: PropTypes.bool,
  onClose: PropTypes.func,
};

export default EachOptimizationComponent;
